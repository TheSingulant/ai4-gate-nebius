"""Redaction, rate limits, and the hard daily inference budget.

The Nebius API key is read from the environment by the planner and is never
stored on receipts. These helpers are the backstop: they strip the key from
text, and they stop a public demo from looping on paid inference.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

REDACTED = "[REDACTED]"


class RateLimitExceeded(RuntimeError):
    code = "RATE_LIMIT"

    def __init__(self, scope: str) -> None:
        super().__init__(f"rate limit exceeded ({scope})")
        self.scope = scope


class BudgetExceeded(RuntimeError):
    code = "BUDGET_EXCEEDED"

    def __init__(self, message: str) -> None:
        super().__init__(message)


def secret_values(env: Any) -> list[str]:
    """Secret strings that must never appear in receipts, logs, or responses."""
    if not hasattr(env, "get"):
        return []
    found: list[str] = []
    for name in ("NEBIUS_API_KEY", "PREVIEW_ACCESS_TOKEN", "JUDGE_ACCESS_TOKEN"):
        raw = env.get(name, "")
        if isinstance(raw, str) and len(raw) >= 8 and raw not in found:
            found.append(raw)
    return found


def redact_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in text:
            text = text.replace(secret, REDACTED)
    return text


def redact_obj(value: Any, secrets: list[str]) -> Any:
    if not secrets:
        return value
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, list):
        return [redact_obj(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact_obj(item, secrets) for key, item in value.items()}
    return value


def redact_bytes(body: bytes, secrets: list[str]) -> bytes:
    if not secrets or not body:
        return body
    text = body.decode("utf-8", errors="ignore")
    cleaned = redact_text(text, secrets)
    if cleaned == text:
        return body
    return cleaned.encode("utf-8")


class SlidingRateLimiter:
    """Fixed-window counter per IP plus one global counter. Window is 60 seconds."""

    def __init__(self, ip_limit: int, global_limit: int, window: float = 60.0) -> None:
        if ip_limit < 1 or global_limit < 1:
            raise ValueError("rate limits must be at least 1")
        self.ip_limit = ip_limit
        self.global_limit = global_limit
        self.window = window
        self._ip: dict[str, list[float]] = {}
        self._global: list[float] = []
        self._lock = threading.Lock()

    def check(self, ip: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._global = [stamp for stamp in self._global if moment - stamp < self.window]
            bucket = [stamp for stamp in self._ip.get(ip, []) if moment - stamp < self.window]
            if len(self._global) >= self.global_limit:
                raise RateLimitExceeded("global")
            if len(bucket) >= self.ip_limit:
                raise RateLimitExceeded("ip")
            bucket.append(moment)
            self._global.append(moment)
            self._ip[ip] = bucket

    def reset(self) -> None:
        with self._lock:
            self._ip.clear()
            self._global.clear()


class DailyBudget:
    """UTC-day caps on Nebius requests, tokens, and agent runs.

    State is SQLite so a restart does not reset the cap. Deleting the database
    file is the operator's reset. The request and token caps apply only to
    live Nebius calls. The agent-run cap also covers the scripted planner so a
    public process cannot be looped into burning CPU.
    """

    def __init__(self, request_cap: int, token_cap: int, db_path: str = ":memory:",
                 agent_run_cap: int = 100) -> None:
        if request_cap < 0 or token_cap < 0 or agent_run_cap < 0:
            raise ValueError("budget caps must be >= 0")
        self.request_cap = request_cap
        self.token_cap = token_cap
        self.agent_run_cap = agent_run_cap
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS budget ("
            "day TEXT PRIMARY KEY, requests INTEGER NOT NULL, tokens INTEGER NOT NULL, "
            "agent_runs INTEGER NOT NULL DEFAULT 0)"
        )

    @staticmethod
    def _day(when: datetime | None = None) -> str:
        moment = when or datetime.now(timezone.utc)
        return moment.strftime("%Y-%m-%d")

    def snapshot(self, *, day: str | None = None) -> dict[str, int | str]:
        key = day or self._day()
        with self._lock:
            requests, tokens, runs = self._row(key)
        return {
            "day": key,
            "requests": requests,
            "tokens": tokens,
            "agent_runs": runs,
            "request_cap": self.request_cap,
            "token_cap": self.token_cap,
            "agent_run_cap": self.agent_run_cap,
            "requests_remaining": max(0, self.request_cap - requests),
            "tokens_remaining": max(0, self.token_cap - tokens),
            "agent_runs_remaining": max(0, self.agent_run_cap - runs),
        }

    def _row(self, day: str) -> tuple[int, int, int]:
        row = self._db.execute(
            "SELECT requests, tokens, agent_runs FROM budget WHERE day = ?", (day,)).fetchone()
        return row if row else (0, 0, 0)

    def before_request(self) -> None:
        """Reserve one model request. Raises before any paid call when the cap is already hit."""
        with self._lock:
            day = self._day()
            requests, tokens, runs = self._row(day)
            if requests >= self.request_cap:
                raise BudgetExceeded(f"daily Nebius request budget reached ({self.request_cap})")
            if tokens >= self.token_cap:
                raise BudgetExceeded(f"daily Nebius token budget reached ({self.token_cap})")
            self._upsert(day, requests + 1, tokens, runs)

    def live_blocked(self) -> bool:
        """True when another Nebius call would exceed the daily request or token cap."""
        with self._lock:
            requests, tokens, _runs = self._row(self._day())
        return requests >= self.request_cap or tokens >= self.token_cap

    def before_agent_run(self) -> None:
        with self._lock:
            day = self._day()
            requests, tokens, runs = self._row(day)
            if runs >= self.agent_run_cap:
                raise BudgetExceeded(f"daily agent run budget reached ({self.agent_run_cap})")
            self._upsert(day, requests, tokens, runs + 1)

    def charge_tokens(self, count: int) -> None:
        count = max(0, int(count))
        with self._lock:
            day = self._day()
            requests, tokens, runs = self._row(day)
            self._upsert(day, requests, tokens + count, runs)

    def _upsert(self, day: str, requests: int, tokens: int, runs: int) -> None:
        self._db.execute(
            "INSERT INTO budget (day, requests, tokens, agent_runs) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET requests = excluded.requests, tokens = excluded.tokens, "
            "agent_runs = excluded.agent_runs",
            (day, requests, tokens, runs),
        )


def evidence_json(record: dict[str, Any], secrets: list[str]) -> str:
    """Serialize inference evidence after redaction. Never includes the API key."""
    cleaned = redact_obj(record, secrets)
    return json.dumps(cleaned, sort_keys=True, default=str)
