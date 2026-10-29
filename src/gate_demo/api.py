"""FastAPI app: session sandbox, gate API, agent runner, and the single-page UI."""

from __future__ import annotations

import base64
import binascii
import hmac
import threading
import time
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from gate_demo.agent import DEFAULT_TASK, Agent
from gate_demo.authorizers.base import AuthorizerError
from gate_demo.canonical import CanonicalError
from gate_demo.clock import parse_rfc3339, system_clock
from gate_demo.config import Settings
from gate_demo.safety import BudgetExceeded, DailyBudget, RateLimitExceeded, SlidingRateLimiter, redact_bytes, secret_values
from gate_demo.scenarios import choose_planner, public_scenarios, resolve_scenario, SCENARIOS
from gate_demo.sessions import Session, SessionError, SessionStore
from gate_demo.tools import ToolError
from gate_verify.verify import verify_receipt

COOKIE = "ai4_gate_session"


class RunIn(BaseModel):
    task: str = ""
    scenario: str = "custom"


class ApproveIn(BaseModel):
    proposal_id: str
    action_hash: str
    ttl_seconds: int = 300


class DeclineIn(BaseModel):
    proposal_id: str


class VerifyIn(BaseModel):
    receipt: dict[str, Any]
    trusted_key_hex: str | None = None


class ProposeIn(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ExecuteIn(BaseModel):
    proposal_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)



JUDGE_TOKEN_MIN_LEN = 32


def _load_judge_credential(source: Mapping[str, str]) -> tuple[str, int | None]:
    """Return (token, expires_at_epoch) or ("", None) when the judge path is disabled.

    Both JUDGE_ACCESS_TOKEN and JUDGE_TOKEN_EXPIRES_AT must be set. A short token
    or an unparseable expiry disables the judge path only (fail closed for judges)
    and leaves the operator PREVIEW_ACCESS_TOKEN path unchanged.
    """
    raw_token = (source.get("JUDGE_ACCESS_TOKEN") or "").strip()
    raw_exp = (source.get("JUDGE_TOKEN_EXPIRES_AT") or "").strip()
    if not raw_token and not raw_exp:
        return "", None
    if not raw_token or not raw_exp:
        return "", None
    if len(raw_token) < JUDGE_TOKEN_MIN_LEN:
        return "", None
    try:
        expires_at = parse_rfc3339(raw_exp)
    except ValueError:
        return "", None
    return raw_token, expires_at


def _extract_presented_secret(header: str) -> bytes | None:
    if len(header) > 7 and header[:7].lower() == "bearer ":
        return header[7:].strip().encode()
    if len(header) > 6 and header[:6].lower() == "basic ":
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error):
            return None
        password = decoded.split(":", 1)[1] if ":" in decoded else ""
        return password.encode()
    return None


def create_app(env: Mapping[str, str] | None = None) -> FastAPI:
    import os
    from pathlib import Path
    source = dict(os.environ if env is None else env)
    settings = Settings.from_env(source)
    if settings.budget_db != ":memory:":
        Path(settings.budget_db).parent.mkdir(parents=True, exist_ok=True)
    preview_token = source.get("PREVIEW_ACCESS_TOKEN", "").strip()
    if preview_token and len(preview_token) < 8:
        raise ValueError("PREVIEW_ACCESS_TOKEN must be at least 8 characters when set")
    raw_judge = (source.get("JUDGE_ACCESS_TOKEN") or "").strip()
    # Fail closed: judge configured without an operator token would leave the
    # site open (classify_preview_auth returns "open" when preview_token is empty).
    # Intentional open local dev remains available when judge auth is unconfigured.
    if raw_judge and not preview_token:
        raise ValueError(
            "JUDGE_ACCESS_TOKEN requires PREVIEW_ACCESS_TOKEN; "
            "refusing to start with judge configured and operator token absent"
        )
    if raw_judge and preview_token:
        jt = raw_judge.encode()
        ot = preview_token.encode()
        if len(jt) == len(ot) and hmac.compare_digest(jt, ot):
            raise ValueError(
                "JUDGE_ACCESS_TOKEN must differ from PREVIEW_ACCESS_TOKEN"
            )
    judge_token, judge_expires_at = _load_judge_credential(source)
    secrets = secret_values(source)
    app = FastAPI(title="AI4 Gate Nebius Hackathon Edition", version="0.3.0")
    app.state.settings = settings
    app.state.env = source
    app.state.secrets = secrets
    app.state.preview_token = preview_token
    app.state.judge_token = judge_token
    app.state.judge_expires_at = judge_expires_at
    app.state.clock = system_clock
    app.state.hsts = source.get("PREVIEW_HSTS", "").strip().lower() in {"1", "true", "yes"}
    app.state.budget = DailyBudget(
        settings.daily_request_budget,
        settings.daily_token_budget,
        settings.budget_db,
        agent_run_cap=settings.daily_agent_runs,
    )
    app.state.sessions = SessionStore(source, settings)
    app.state.read_limiter = SlidingRateLimiter(settings.ip_read_rate, settings.global_read_rate)
    app.state.mutation_limiter = SlidingRateLimiter(settings.ip_mutation_rate, settings.global_mutation_rate)
    app.state._reaper_stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        def loop() -> None:
            while not app.state._reaper_stop.wait(15):
                app.state.sessions.reap()

        thread = threading.Thread(target=loop, name="session-reaper", daemon=True)
        thread.start()
        try:
            yield
        finally:
            app.state._reaper_stop.set()
            app.state.sessions.close()

    app.router.lifespan_context = lifespan

    def client_ip(request: Request) -> str:
        if settings.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip() or "unknown"
        if request.client is None:
            return "unknown"
        return request.client.host or "unknown"

    def session_or_401(request: Request) -> Session:
        session = app.state.sessions.get(request.cookies.get(COOKIE))
        if session is None:
            raise HTTPException(status_code=401, detail={"code": "NO_SESSION", "message": "open a session first"})
        kind = getattr(request.state, "auth_kind", None)
        if session.auth_kind == "judge" and not judge_is_active():
            if kind == "operator":
                session.auth_kind = "operator"
            else:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "JUDGE_EXPIRED", "message": "judge access has expired"},
                )
        return session

    def bad(exc: Exception, status: int = 422) -> HTTPException:
        code = getattr(exc, "code", type(exc).__name__)
        return HTTPException(status_code=status, detail={"code": code, "message": str(exc)})

    def stamp(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Gate-Backend"] = "reference"
        if app.state.hsts:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def judge_is_active(now: int | None = None) -> bool:
        token = app.state.judge_token
        expires_at = app.state.judge_expires_at
        if not token or expires_at is None:
            return False
        moment = app.state.clock() if now is None else now
        return moment < expires_at

    def classify_preview_auth(request: Request) -> str | None:
        """Return auth kind: 'open', 'operator', 'judge', or None if rejected."""
        if request.url.path == "/health":
            return "open"
        token = app.state.preview_token
        if not token:
            return "open"
        presented = _extract_presented_secret(request.headers.get("authorization", ""))
        if presented is None:
            return None
        operator_ok = hmac.compare_digest(presented, token.encode())
        judge_token = app.state.judge_token
        if judge_token:
            judge_match = hmac.compare_digest(presented, judge_token.encode())
        else:
            # Keep a compare_digest call so the missing-judge path stays constant-time shaped.
            judge_match = hmac.compare_digest(presented, presented) and False
        if operator_ok:
            return "operator"
        if judge_match and judge_is_active():
            return "judge"
        return None

    def preview_allows(request: Request) -> bool:
        return classify_preview_auth(request) is not None

    def judge_session_still_valid(session: Session) -> bool:
        if session.auth_kind != "judge":
            return True
        return judge_is_active()

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        if request.url.path != "/health":
            limiter = app.state.mutation_limiter if request.method != "GET" else app.state.read_limiter
            try:
                limiter.check(client_ip(request))
            except RateLimitExceeded as exc:
                denied = JSONResponse(
                    {"detail": {"code": exc.code, "message": str(exc)}},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                return stamp(denied)
            kind = classify_preview_auth(request)
            if kind is None:
                denied = JSONResponse(
                    {"detail": {"code": "UNAUTHORIZED", "message": "this preview requires an access token"}},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="AI4 Gate"'},
                )
                return stamp(denied)
            request.state.auth_kind = kind
            sid = request.cookies.get(COOKIE)
            if sid and kind == "judge":
                existing = app.state.sessions.get(sid)
                if existing is not None and not judge_session_still_valid(existing):
                    denied = JSONResponse(
                        {"detail": {"code": "JUDGE_EXPIRED", "message": "judge access has expired"}},
                        status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="AI4 Gate"'},
                    )
                    return stamp(denied)
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        body = redact_bytes(body, app.state.secrets)

        async def one_chunk():
            yield body

        response.body_iterator = one_chunk()
        response.headers["content-length"] = str(len(body))
        return stamp(response)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "true"}

    @app.post("/session")
    def open_session(request: Request, response: Response) -> dict[str, Any]:
        try:
            session = app.state.sessions.create()
        except SessionError as exc:
            raise bad(exc, 429) from None
        kind = getattr(request.state, "auth_kind", None) or classify_preview_auth(request) or "open"
        session.auth_kind = kind
        secure = bool(app.state.preview_token) or bool(app.state.judge_token) or app.state.hsts
        max_age = settings.session_ttl
        if kind == "judge" and app.state.judge_expires_at is not None:
            remaining = app.state.judge_expires_at - app.state.clock()
            if remaining <= 0:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "JUDGE_EXPIRED", "message": "judge access has expired"},
                )
            max_age = min(max_age, remaining)
        response.set_cookie(
            COOKIE, session.id, httponly=True, samesite="lax", secure=secure,
            max_age=max_age, path="/",
        )
        return {"ok": True, "identity": session.identity, "backend": "reference"}

    @app.get("/snapshot")
    def snapshot(request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        gate = session.gate
        return {
            "identity": session.identity,
            "status": gate.status(),
            "budget": app.state.budget.snapshot(),
            "agent_running": session.agent_running,
            "events": list(gate.events),
            "proposals": list(gate.pending.values()),
            "receipts": list(gate.receipts),
            "evidence": list(session.evidence),
            "keys": gate.keys(),
            "mode": settings.mode,
            "scenarios": public_scenarios() if settings.mode == "guided" else [],
            "run_planner": session.active_label or session.identity.get("planner"),
            "run_model": session.active_model or session.identity.get("model_id"),
            "planner_note": session.planner_note,
        }

    @app.post("/propose")
    def propose(body: ProposeIn, request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        try:
            return session.gate.propose(body.tool, body.args, planner="OPERATOR", model_id="none")
        except (ToolError, CanonicalError) as exc:
            raise bad(exc) from None
        except AuthorizerError as exc:
            raise bad(exc, 409) from None

    @app.post("/approve")
    def approve(body: ApproveIn, request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        try:
            return session.gate.approve(body.proposal_id, body.action_hash, body.ttl_seconds)
        except AuthorizerError as exc:
            raise bad(exc, 409) from None

    @app.post("/decline")
    def decline(body: DeclineIn, request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        try:
            session.gate.decline(body.proposal_id)
        except AuthorizerError as exc:
            raise bad(exc, 409) from None
        return {"declined": body.proposal_id, "backend": "reference"}

    @app.post("/execute")
    def execute(body: ExecuteIn, request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        try:
            return session.gate.execute(body.proposal_id, body.tool, body.args)
        except (ToolError, CanonicalError) as exc:
            raise bad(exc) from None
        except AuthorizerError as exc:
            raise bad(exc, 409) from None

    @app.post("/demo/replay")
    def replay(request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        receipt = _last_allowed(session)
        if receipt is None:
            raise bad(SessionError("NOTHING_TO_REPLAY", "no allowed action to replay yet"), 409)
        try:
            out = session.gate.execute(receipt["proposal_id"], receipt["action"]["tool"], receipt["action"]["args"])
        except (ToolError, AuthorizerError, CanonicalError) as exc:
            raise bad(exc, 409) from None
        session.gate.log("replay_attempt", allowed=out["allowed"], reason=out["reason"],
                         receipt_id=(out.get("receipt") or {}).get("receipt_id"))
        return out

    @app.post("/demo/tamper")
    def tamper(request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        receipt = _last_allowed(session)
        if receipt is None:
            raise bad(SessionError("NOTHING_TO_TAMPER", "no allowed action to alter yet"), 409)
        tool = receipt["action"]["tool"]
        args = dict(receipt["action"]["args"])
        _alter(args)
        try:
            out = session.gate.execute(receipt["proposal_id"], tool, args)
        except (ToolError, AuthorizerError, CanonicalError) as exc:
            raise bad(exc, 409) from None
        session.gate.log("tamper_attempt", allowed=out["allowed"], reason=out["reason"],
                         receipt_id=(out.get("receipt") or {}).get("receipt_id"))
        return out

    @app.post("/verify")
    def verify(body: VerifyIn, request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        keys = session.gate.keys()
        signer = body.trusted_key_hex or keys["receipt_signer"]["public_key_hex"]
        approvers = keys.get("approvers") or []
        try:
            result = verify_receipt(body.receipt, signer, approvers[0]["public_key_hex"] if approvers else None)
        except ValueError as exc:
            raise bad(exc) from None
        return {
            "valid": result.valid,
            "errors": result.errors,
            "notes": result.notes,
            "summary": result.summary,
            "receipt_id": body.receipt.get("receipt_id"),
            "backend": "reference",
        }

    @app.post("/agent/run")
    def run_agent(body: RunIn, request: Request) -> dict[str, Any]:
        session = session_or_401(request)
        if session.agent_running:
            raise bad(SessionError("BUSY", "agent already running in this session"), 409)
        mode = settings.mode
        if mode == "guided":
            scenario_id = resolve_scenario(body.scenario)
            if scenario_id is None:
                raise bad(SessionError("GUIDED_ONLY", "guided mode only runs a preset scenario"), 403)
            task = SCENARIOS[scenario_id]["task"]
        else:
            scenario_id = body.scenario or "custom"
            task = DEFAULT_TASK if body.scenario == "guided" or not body.task.strip() else body.task.strip()
        try:
            app.state.budget.before_agent_run()
            app.state.sessions.begin_agent()
        except BudgetExceeded as exc:
            raise bad(exc, 403) from None
        except SessionError as exc:
            raise bad(exc, 429) from None
        now = time.monotonic()
        interval = settings.live_interval
        allow_live = interval == 0 or session.last_live_at == 0.0 or (now - session.last_live_at) >= interval
        planner, note = choose_planner(
            app.state.env,
            allow_live=allow_live,
            budget_open=not app.state.budget.live_blocked(),
            evidence=session.evidence,
            budget=app.state.budget,
            secrets=app.state.secrets,
        )
        if note == "live":
            session.last_live_at = now
        session.active_label = planner.label
        session.active_model = planner.model_id
        session.planner_note = "" if note == "live" else note
        session.agent_jobs += 1
        session.agent_running = True
        if note != "live":
            session.gate.log("planner_fallback", reason=note, planner=planner.label, model_id=planner.model_id)

        def work() -> None:
            try:
                if mode == "guided":
                    session.gate.record_writes = True
                Agent(session.gate, planner, approval_timeout=float(source.get("GATE_APPROVAL_TIMEOUT", "600") or "600"),
                      should_stop=lambda: session.stop_requested).run(task)
            finally:
                session.gate.record_writes = False
                session.agent_jobs = max(0, session.agent_jobs - 1)
                session.agent_running = session.agent_jobs > 0
                app.state.sessions.end_agent()

        threading.Thread(target=work, name="gate-agent", daemon=True).start()
        return {
            "started": True,
            "scenario": scenario_id,
            "planner": planner.label,
            "model_id": planner.model_id,
            "fallback": None if note == "live" else note,
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("gate_demo").joinpath("static/index.html").read_text(encoding="utf-8")

    return app


def _last_allowed(session: Session) -> dict[str, Any] | None:
    for receipt in reversed(session.gate.receipts):
        if receipt.get("decision") == "allowed" and receipt.get("proposal_id") and receipt.get("action"):
            return receipt
    return None


def _alter(args: dict[str, Any]) -> None:
    if "remote" in args:
        args["remote"] = "mirror" if args["remote"] != "mirror" else "origin"
        return
    if "content" in args and isinstance(args["content"], str):
        args["content"] = args["content"] + "\n# altered\n"
        return
    if "body" in args and isinstance(args["body"], str):
        args["body"] = args["body"] + " altered"
        return
    if "message" in args and isinstance(args["message"], str):
        args["message"] = args["message"] + " altered"
        return
    if "path" in args and isinstance(args["path"], str):
        args["path"] = args["path"] + ".altered"
        return
    if args:
        key = next(iter(args))
        if isinstance(args[key], str):
            args[key] = args[key] + "x"
