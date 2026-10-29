"""The gate: the only path from an agent to its tools.

``propose`` -> ``approve`` (a person, when policy asks) -> ``execute``.
``execute`` rebuilds the action from what it is handed, asks the authorizer,
verifies the returned receipt with the independent verifier, and only then
runs the tool. A refusal never runs anything.

Every action is stamped with the planner that produced it (``NEMOTRON-LIVE``,
``SCRIPTED``, or ``OPERATOR``) and that stamp is inside the signed receipt.
String values are redacted before they are hashed, so a Nebius API key in an
argument cannot enter a receipt.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from gate_demo.authorizers.base import Authorizer, AuthorizerError, NEEDS_APPROVAL
from gate_demo.safety import redact_obj
from gate_demo.tools import TOOLS, ToolError, build_action
from gate_demo.workspace import Workspace, WorkspaceError
from gate_verify.verify import verify_receipt


class Gate:
    def __init__(self, authorizer: Authorizer, workspace: Workspace, secrets: list[str] | None = None,
                 mode: str = "open") -> None:
        self.authorizer = authorizer
        self.workspace = workspace
        self.secrets = [s for s in (secrets or []) if isinstance(s, str) and len(s) >= 8]
        self.mode = mode if mode in {"open", "guided"} else "guided"
        # Guided mode runs only scenario files and writes the agent was allowed to make.
        self.record_writes = False
        self.executable_hashes: dict[str, set[str]] = {}
        self.events: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.pending: dict[str, dict[str, Any]] = {}
        self._outcome: dict[str, str] = {}
        self._cond = threading.Condition()
        self._keys: dict[str, Any] | None = None
        self.last_backend_error: dict[str, Any] | None = None

    def keys(self) -> dict[str, Any]:
        if self._keys is None:
            self._keys = self.authorizer.public_keys()
        return self._keys

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.authorizer.name,
            "backend": self.authorizer.name,
            "mode": "offline",
            "fallback": False,
            "notice": None,
            "last_backend_error": self.last_backend_error,
        }

    def labels(self, proposal_id: str | None = None) -> dict[str, Any]:
        return {"backend": self.authorizer.name, "fallback": False, "fallback_notice": None}

    def log(self, kind: str, **data: Any) -> None:
        data = redact_obj(data, self.secrets)
        with self._cond:
            self.events.append({"seq": len(self.events), "kind": kind, **data})

    def _store_receipt(self, receipt: dict[str, Any] | None) -> None:
        if receipt is not None:
            cleaned = redact_obj(receipt, self.secrets)
            with self._cond:
                self.receipts.append(cleaned)

    def receipt_ok(self, receipt: dict[str, Any]) -> bool:
        keys = self.keys()
        signer = keys["receipt_signer"]["public_key_hex"]
        return verify_receipt(receipt, signer).valid

    def _clean_args(self, args: dict[str, Any]) -> dict[str, Any]:
        cleaned = redact_obj(args, self.secrets)
        if not isinstance(cleaned, dict):
            raise ToolError("args must be an object")
        return cleaned

    def propose(self, tool: str, args: dict[str, Any], *, planner: str, model_id: str) -> dict[str, Any]:
        action = build_action(tool, self._clean_args(args))
        proposal = self.authorizer.propose(action, planner=planner, model_id=model_id)
        self._store_receipt(proposal.receipt)
        response = {
            "proposal_id": proposal.proposal_id,
            "action": action,
            "action_hash": proposal.action_hash,
            "decision": proposal.decision,
            "reason": proposal.reason,
            "expires_at": proposal.expires_at,
            "receipt": proposal.receipt,
            "planner": proposal.planner,
            "model_id": proposal.model_id,
            **self.labels(proposal.proposal_id),
        }
        response = redact_obj(response, self.secrets)
        if proposal.decision == NEEDS_APPROVAL:
            with self._cond:
                self.pending[proposal.proposal_id] = {k: response[k] for k in
                                                     ("proposal_id", "action", "action_hash", "reason", "expires_at",
                                                      "backend", "fallback", "fallback_notice", "planner", "model_id")}
        self.log("propose", tool=tool, target=action["target"], decision=proposal.decision,
                 proposal_id=proposal.proposal_id, reason=proposal.reason, backend=response["backend"],
                 planner=proposal.planner, model_id=proposal.model_id)
        return response

    def approve(self, proposal_id: str, action_hash: str, ttl_seconds: int = 300) -> dict[str, Any]:
        approval = self.authorizer.approve(proposal_id, action_hash, ttl_seconds)
        with self._cond:
            self.pending.pop(proposal_id, None)
            self._outcome[proposal_id] = "approved"
            self._cond.notify_all()
        self.log("approve", proposal_id=proposal_id, approval_id=approval.approval_id, expires_at=approval.expires_at)
        return {**approval.as_dict(), **self.labels(proposal_id)}

    def decline(self, proposal_id: str) -> None:
        with self._cond:
            if self.pending.pop(proposal_id, None) is None:
                raise AuthorizerError("NOT_PENDING", "no pending proposal with that id")
            self._outcome[proposal_id] = "declined"
            self._cond.notify_all()
        self.log("decline", proposal_id=proposal_id)

    def wait_for_decision(self, proposal_id: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self._cond:
            while proposal_id not in self._outcome:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout"
                self._cond.wait(remaining)
            return self._outcome[proposal_id]

    def seed_scenario_files(self) -> None:
        """Remember hashes of the scenario tree so run_tests can execute those files and no others."""
        for rel, text in self.workspace.python_sources():
            self._remember(rel, text)

    def remember_write(self, path: str, content: str) -> None:
        if self.mode == "guided" and self.record_writes:
            self._remember(path, content)

    def _remember(self, path: str, content: str) -> None:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.executable_hashes.setdefault(path, set()).add(digest)

    def _content_block(self, tool: str, args: dict[str, Any]) -> str | None:
        """Return a refusal detail when guided mode must not execute this file. None means proceed."""
        if self.mode != "guided" or tool not in {"run_python", "run_tests"}:
            return None
        if tool == "run_python":
            path = args.get("path", "")
            try:
                text = self.workspace.read_text(path)
            except WorkspaceError:
                return f"run_python refused: {path} is not an approved scenario file"
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest not in self.executable_hashes.get(path, set()):
                return f"run_python refused: {path} content does not match an approved scenario write"
            return None
        blocked: list[str] = []
        for rel, text in self.workspace.python_sources():
            if not (rel.startswith("src/") or rel.startswith("tests/")):
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest not in self.executable_hashes.get(rel, set()):
                blocked.append(rel)
        if blocked:
            shown = ", ".join(blocked[:6])
            return f"run_tests refused: unapproved python content in {shown}"
        return None

    def execute(self, proposal_id: str, tool: str, args: dict[str, Any], *,
                planner: str = "OPERATOR", model_id: str = "none") -> dict[str, Any]:
        action = build_action(tool, self._clean_args(args))
        detail = self._content_block(tool, action["args"])
        if detail:
            decision = self.authorizer.refuse_execution(
                proposal_id, action, reason="UNAPPROVED_CONTENT", detail=detail, planner=planner, model_id=model_id)
        else:
            decision = self.authorizer.authorize_execution(proposal_id, action, planner=planner, model_id=model_id)
        receipt = redact_obj(decision.receipt, self.secrets)
        labels = self.labels(proposal_id)
        self._store_receipt(receipt)
        if not self.receipt_ok(receipt):
            self.log("execute", tool=tool, target=action["target"], allowed=False, reason="RECEIPT_UNVERIFIED",
                     planner=receipt.get("planner"), model_id=receipt.get("model_id"), **labels)
            return {"allowed": False, "reason": "RECEIPT_UNVERIFIED", "receipt": receipt, "result": None,
                    "planner": receipt.get("planner"), "model_id": receipt.get("model_id"), **labels}
        allowed = decision.allowed and receipt.get("decision") == "allowed"
        result: dict[str, Any] | None = None
        if allowed:
            try:
                result = TOOLS[tool].run(self.workspace, action["args"])
            except WorkspaceError as exc:
                result = {"error": str(exc)}
            result = redact_obj(result, self.secrets)
        if allowed and tool == "write_file":
            self.remember_write(action["args"]["path"], action["args"]["content"])
        self.log("execute", tool=tool, target=action["target"], allowed=allowed, reason=decision.reason,
                 proposal_id=proposal_id, receipt_id=receipt.get("receipt_id"),
                 planner=receipt.get("planner"), model_id=receipt.get("model_id"), **labels)
        return {"allowed": allowed, "reason": decision.reason, "receipt": receipt, "result": result,
                "planner": receipt.get("planner"), "model_id": receipt.get("model_id"), **labels}


__all__ = ["Gate", "ToolError", "AuthorizerError"]
