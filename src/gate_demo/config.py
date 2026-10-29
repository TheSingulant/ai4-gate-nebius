"""Build the open reference authorizer and read hosted-demo settings from the environment.

This edition has one authorizer: the in-process reference engine. There is no
client for a hosted authority service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gate_demo.authorizers.reference import ReferenceAuthorizer
from gate_demo.keys import load_or_create
from gate_demo.policy import Policy, default_policy
from gate_demo.workspace import SandboxLimits


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = env.get(name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.1) -> float:
    raw = env.get(name, "")
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    session_ttl: int = 1800
    max_sessions: int = 40
    max_concurrent_agents: int = 2
    ip_mutation_rate: int = 20
    global_mutation_rate: int = 60
    ip_read_rate: int = 240
    global_read_rate: int = 800
    daily_request_budget: int = 40
    daily_token_budget: int = 200_000
    daily_agent_runs: int = 100
    budget_db: str = ":memory:"
    session_root: str = ""
    trust_proxy: bool = False
    limits: SandboxLimits = SandboxLimits()
    mode: str = "guided"
    live_interval: int = 600

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        limits = SandboxLimits(
            python_timeout=_float(env, "SANDBOX_PYTHON_TIMEOUT", 8.0),
            tests_timeout=_float(env, "SANDBOX_TESTS_TIMEOUT", 20.0),
            cpu_seconds=_int(env, "SANDBOX_CPU_SECONDS", 5, minimum=1),
            memory_bytes=_int(env, "SANDBOX_MEMORY_BYTES", 256 * 1024 * 1024, minimum=64 * 1024 * 1024),
            output_chars=_int(env, "SANDBOX_OUTPUT_CHARS", 4000, minimum=200),
            max_write_bytes=_int(env, "SANDBOX_MAX_WRITE_BYTES", 65_536, minimum=1024),
        )
        mode = env.get("GATE_MODE", "guided").strip().lower() or "guided"
        if mode not in {"guided", "open"}:
            raise ValueError("GATE_MODE must be guided or open")
        return cls(
            session_ttl=_int(env, "SESSION_TTL_SECONDS", 1800, minimum=30),
            max_sessions=_int(env, "MAX_SESSIONS", 40, minimum=1),
            max_concurrent_agents=_int(env, "MAX_CONCURRENT_AGENTS", 2, minimum=1),
            ip_mutation_rate=_int(env, "GATE_IP_MUTATION_RATE_PER_MIN", 20, minimum=1),
            global_mutation_rate=_int(env, "GATE_GLOBAL_MUTATION_RATE_PER_MIN", 60, minimum=1),
            ip_read_rate=_int(env, "GATE_IP_READ_RATE_PER_MIN", 240, minimum=1),
            global_read_rate=_int(env, "GATE_GLOBAL_READ_RATE_PER_MIN", 800, minimum=1),
            daily_request_budget=_int(env, "GATE_DAILY_REQUEST_BUDGET", 40, minimum=0),
            daily_token_budget=_int(env, "GATE_DAILY_TOKEN_BUDGET", 200_000, minimum=0),
            daily_agent_runs=_int(env, "GATE_DAILY_AGENT_RUNS", 100, minimum=0),
            budget_db=env.get("GATE_BUDGET_DB", ":memory:") or ":memory:",
            session_root=env.get("GATE_SESSION_ROOT", "") or "",
            trust_proxy=env.get("GATE_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"},
            limits=limits,
            mode=mode,
            live_interval=_int(env, "GATE_LIVE_INTERVAL_SECONDS", 600, minimum=0),
        )


def build_authorizer(env: Mapping[str, str], **kwargs: Any) -> ReferenceAuthorizer:
    """AUTHORIZER must be ``reference``. This edition does not ship another backend."""
    choice = env.get("AUTHORIZER", "reference").strip().lower()
    if choice != "reference":
        raise ValueError("AUTHORIZER must be reference. This edition only includes the open reference authorizer.")
    policy_path = env.get("GATE_POLICY")
    policy = Policy.load(policy_path) if policy_path else default_policy()
    key_file = env.get("GATE_SIGNING_KEY_FILE") or None
    approver_file = env.get("GATE_APPROVER_KEY_FILE") or None
    kwargs.setdefault("signing_key", load_or_create(key_file))
    kwargs.setdefault("approver_key", load_or_create(approver_file))
    kwargs.setdefault("db_path", env.get("GATE_DB", ":memory:"))
    return ReferenceAuthorizer(policy, **kwargs)
