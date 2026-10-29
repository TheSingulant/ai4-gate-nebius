"""Per-browser sessions. Each session owns a workspace and an authorizer database.

Sessions expire. The reaper deletes the workspace directory. Agent threads are
asked to stop by declining anything still waiting for approval.
"""

from __future__ import annotations

import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from gate_demo.config import Settings, build_authorizer
from gate_demo.gate import Gate
from gate_demo.planner import planner_identity
from gate_demo.safety import secret_values
from gate_demo.workspace import Workspace


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Session:
    id: str
    gate: Gate
    workspace: Workspace
    identity: dict[str, str]
    created: float
    last_seen: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    agent_running: bool = False
    last_live_at: float = 0.0
    active_label: str = ""
    active_model: str = ""
    planner_note: str = ""
    stop_requested: bool = False
    agent_jobs: int = 0
    auth_kind: str = "open"
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self, env: Mapping[str, str], settings: Settings) -> None:
        self.env = dict(env)
        self.settings = settings
        self.secrets = secret_values(env)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._running = 0
        if settings.session_root:
            self.root = Path(settings.session_root)
            self.root.mkdir(parents=True, exist_ok=True)
            self._owned_root = False
        else:
            self.root = Path(tempfile.mkdtemp(prefix="gate-sessions-"))
            self._owned_root = True

    def create(self) -> Session:
        with self._lock:
            if len(self._sessions) >= self.settings.max_sessions:
                raise SessionError("TOO_MANY_SESSIONS", "too many sessions; try again after one expires")
            sid = secrets.token_urlsafe(24)
            workspace = Workspace.create(self.root, self.settings.limits)
            authorizer = build_authorizer(self.env, db_path=str(workspace.root / "authorizer.sqlite3"))
            gate = Gate(authorizer, workspace, secrets=self.secrets, mode=self.settings.mode)
            if self.settings.mode == "guided":
                gate.seed_scenario_files()
            now = time.time()
            identity = planner_identity(self.env)
            session = Session(
                id=sid,
                gate=gate,
                workspace=workspace,
                identity=identity,
                created=now,
                last_seen=now,
                active_label=identity["planner"],
                active_model=identity["model_id"],
            )
            self._sessions[sid] = session
            return session

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            return None
        session.last_seen = time.time()
        return session

    def running_agents(self) -> int:
        with self._lock:
            return self._running

    def begin_agent(self) -> None:
        with self._lock:
            if self._running >= self.settings.max_concurrent_agents:
                raise SessionError("BUSY", "too many agents are already running")
            self._running += 1

    def end_agent(self) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)

    def reap(self, now: float | None = None) -> int:
        moment = time.time() if now is None else now
        ttl = self.settings.session_ttl
        dropped = 0
        with self._lock:
            expired = [s for s in self._sessions.values() if moment - s.last_seen > ttl]
        for session in expired:
            if session.agent_running:
                for proposal_id in list(session.gate.pending):
                    try:
                        session.gate.decline(proposal_id)
                    except Exception:
                        pass
                continue
            with self._lock:
                self._sessions.pop(session.id, None)
            session.workspace.cleanup()
            dropped += 1
        return dropped

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.workspace.cleanup()
        if self._owned_root:
            import shutil
            shutil.rmtree(self.root, ignore_errors=True)
