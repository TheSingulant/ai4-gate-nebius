"""Judge-only credential: separate auth, same guided authorization, per-request expiry."""

from __future__ import annotations

import base64
import hmac
import inspect

from fastapi.testclient import TestClient

from gate_demo import api as api_mod
from gate_demo.api import create_app
from gate_demo.clock import ManualClock
from tests.test_api import _client, _env

OPERATOR = "operator-preview-token"
# 64 hex chars (>= 32)
JUDGE = "a" * 64
EXPIRY = "2026-12-16T08:00:00Z"
EXPIRY_EPOCH = 1797408000  # 2026-12-16T08:00:00Z


def _auth(token: str, *, basic: bool = False) -> dict[str, str]:
    if basic:
        blob = base64.b64encode(f"judge:{token}".encode()).decode()
        return {"Authorization": f"Basic {blob}"}
    return {"Authorization": f"Bearer {token}"}


def _app(tmp_path, clock: ManualClock | None = None, **extra):
    env = _env(
        tmp_path,
        PREVIEW_ACCESS_TOKEN=OPERATOR,
        JUDGE_ACCESS_TOKEN=JUDGE,
        JUDGE_TOKEN_EXPIRES_AT=EXPIRY,
        **extra,
    )
    app = create_app(env)
    if clock is not None:
        app.state.clock = clock
    return app


def _jclient(app):
    # Secure session cookies require an https base URL in TestClient.
    return TestClient(app, base_url="https://testserver")


def test_valid_judge_bearer_and_basic(tmp_path):
    clock = ManualClock(EXPIRY_EPOCH - 60)
    app = _app(tmp_path, clock=clock)
    with _jclient(app) as client:
        opened = client.post("/session", headers=_auth(JUDGE))
        assert opened.status_code == 200
        assert opened.json()["ok"] is True
        snap = client.get("/snapshot", headers=_auth(JUDGE))
        assert snap.status_code == 200
        assert snap.json()["mode"] == "guided" or "mode" in snap.json()
    with _jclient(_app(tmp_path, clock=clock)) as client:
        basic_ok = client.post("/session", headers=_auth(JUDGE, basic=True))
        assert basic_ok.status_code == 200


def test_invalid_and_missing_judge_rejected(tmp_path):
    clock = ManualClock(EXPIRY_EPOCH - 60)
    with _jclient(_app(tmp_path, clock=clock)) as client:
        assert client.post("/session").status_code == 401
        assert client.post("/session", headers=_auth("b" * 64)).status_code == 401
        assert client.post("/session", headers={"Authorization": "Bearer"}).status_code == 401
        assert client.post("/session", headers={"Authorization": "Basic not-base64"}).status_code == 401
        weird = base64.b64encode(b"nousernameorcolon").decode()
        assert client.post("/session", headers={"Authorization": f"Basic {weird}"}).status_code == 401


def test_malformed_expiry_disables_judge_only(tmp_path):
    with _client(
        tmp_path,
        PREVIEW_ACCESS_TOKEN=OPERATOR,
        JUDGE_ACCESS_TOKEN=JUDGE,
        JUDGE_TOKEN_EXPIRES_AT="not-a-timestamp",
    ) as client:
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 401
        assert client.post("/session", headers=_auth(OPERATOR)).status_code == 200
    with _client(
        tmp_path,
        PREVIEW_ACCESS_TOKEN=OPERATOR,
        JUDGE_ACCESS_TOKEN=JUDGE[:16],  # too short
        JUDGE_TOKEN_EXPIRES_AT=EXPIRY,
    ) as client:
        assert client.post("/session", headers=_auth(JUDGE[:16])).status_code == 401
        assert client.post("/session", headers=_auth(OPERATOR)).status_code == 200
    with _client(tmp_path, PREVIEW_ACCESS_TOKEN=OPERATOR) as client:
        # judge unset -> disabled
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 401
        assert client.post("/session", headers=_auth(OPERATOR)).status_code == 200


def test_expiry_boundary_with_frozen_clock(tmp_path):
    clock = ManualClock(EXPIRY_EPOCH - 1)
    app = _app(tmp_path, clock=clock)
    with _jclient(app) as client:
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 200
    clock.now = EXPIRY_EPOCH  # at expiry: rejected
    with _jclient(app) as client:
        denied = client.post("/session", headers=_auth(JUDGE))
        assert denied.status_code == 401
        assert denied.json()["detail"]["code"] == "UNAUTHORIZED"
    clock.now = EXPIRY_EPOCH + 30
    with _jclient(app) as client:
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 401
        # operator still works after expiry without restart
        assert client.post("/session", headers=_auth(OPERATOR)).status_code == 200


def test_judge_cookie_rejected_after_expiry(tmp_path):
    clock = ManualClock(EXPIRY_EPOCH - 30)
    app = _app(tmp_path, clock=clock)
    with _jclient(app) as client:
        opened = client.post("/session", headers=_auth(JUDGE))
        assert opened.status_code == 200
        assert client.get("/snapshot", headers=_auth(JUDGE)).status_code == 200
        clock.now = EXPIRY_EPOCH + 1
        expired = client.get("/snapshot", headers=_auth(JUDGE))
        assert expired.status_code == 401
        assert expired.json()["detail"]["code"] in {"JUDGE_EXPIRED", "UNAUTHORIZED"}


def test_operator_ok_when_judge_revoked(tmp_path):
    # Revoke = judge vars absent (as after clearing env and restart)
    with _client(tmp_path, PREVIEW_ACCESS_TOKEN=OPERATOR) as client:
        assert client.post("/session", headers=_auth(OPERATOR)).status_code == 200
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 401


def test_timing_safe_compare_used():
    src = inspect.getsource(api_mod)
    assert "hmac.compare_digest" in src
    assert src.count("compare_digest") >= 3


def test_judge_same_guided_authorization_not_extra_privilege(tmp_path):
    clock = ManualClock(EXPIRY_EPOCH - 60)
    app = _app(tmp_path, clock=clock, GATE_MODE="guided")
    with _jclient(app) as client:
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 200
        refused = client.post(
            "/agent/run",
            headers=_auth(JUDGE),
            json={"task": "do anything free text", "scenario": "custom"},
        )
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "GUIDED_ONLY"


def test_refuse_startup_judge_without_operator(tmp_path):
    """Judge token set with no operator token must refuse to start (no fail-open)."""
    import pytest

    env = _env(
        tmp_path,
        PREVIEW_ACCESS_TOKEN="",
        JUDGE_ACCESS_TOKEN=JUDGE,
        JUDGE_TOKEN_EXPIRES_AT=EXPIRY,
    )
    with pytest.raises(ValueError) as ei:
        create_app(env)
    msg = str(ei.value)
    assert "JUDGE_ACCESS_TOKEN requires PREVIEW_ACCESS_TOKEN" in msg
    assert JUDGE not in msg
    assert OPERATOR not in msg


def test_refuse_startup_identical_tokens(tmp_path):
    """Judge and operator must be independently revocable; identical values refuse start."""
    import pytest

    shared = "s" * 64
    env = _env(
        tmp_path,
        PREVIEW_ACCESS_TOKEN=shared,
        JUDGE_ACCESS_TOKEN=shared,
        JUDGE_TOKEN_EXPIRES_AT=EXPIRY,
    )
    with pytest.raises(ValueError) as ei:
        create_app(env)
    msg = str(ei.value)
    assert "must differ" in msg
    assert shared not in msg


def test_open_local_dev_when_judge_unconfigured(tmp_path):
    """Neither token set: intentional open local development still starts."""
    env = _env(tmp_path, PREVIEW_ACCESS_TOKEN="", JUDGE_ACCESS_TOKEN="", JUDGE_TOKEN_EXPIRES_AT="")
    app = create_app(env)
    with _jclient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/session").status_code == 200


def test_valid_distinct_judge_and_operator_starts(tmp_path):
    clock = ManualClock(EXPIRY_EPOCH - 60)
    app = _app(tmp_path, clock=clock)
    assert app.state.preview_token == OPERATOR
    assert app.state.judge_token == JUDGE
    with _jclient(app) as client:
        assert client.post("/session", headers=_auth(OPERATOR)).status_code == 200
        assert client.post("/session", headers=_auth(JUDGE)).status_code == 200
