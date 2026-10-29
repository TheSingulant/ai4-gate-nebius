"""The open reference authorizer: policy, exact-action binding, one-time use, expiry,
Ed25519-signed approvals and receipts, state in SQLite.

This is the hackathon reference engine. It runs in-process. It is not the
hosted AI4 authority service, and this package does not contain a client for
that service. Single process, one demo approver key held by the gate, no key
rotation.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gate_demo.authorizers import base
from gate_demo.authorizers.base import Approval, AuthorizerError, ExecutionDecision, Proposal
from gate_demo.canonical import action_hash, canonical_bytes
from gate_demo.clock import Clock, rfc3339, system_clock
from gate_demo.keys import key_id, public_hex, sign_b64
from gate_demo.policy import ALLOW, DENY, NEEDS_APPROVAL, Policy, default_policy
from gate_demo.receipts import PLANNER_LABELS, ReceiptSigner, approval_payload

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    action_json TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    consumed_at INTEGER,
    planner TEXT NOT NULL,
    model_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    action_hash TEXT NOT NULL,
    approver_key_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    signature TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
"""

_DECISION_MAP = {ALLOW: base.AUTO_ALLOW, NEEDS_APPROVAL: base.NEEDS_APPROVAL, DENY: base.DENY}
MAX_APPROVAL_TTL = 3600


def _check_provenance(planner: str, model_id: str) -> tuple[str, str]:
    if planner not in PLANNER_LABELS:
        raise AuthorizerError("BAD_PLANNER", f"planner must be one of {', '.join(PLANNER_LABELS)}")
    if not isinstance(model_id, str) or not model_id or len(model_id) > 128:
        raise AuthorizerError("BAD_MODEL", "model_id must be a short non-empty string")
    if any(ord(ch) < 32 for ch in model_id):
        raise AuthorizerError("BAD_MODEL", "model_id must not contain control characters")
    return planner, model_id


class ReferenceAuthorizer:
    name = "reference"

    def __init__(
        self,
        policy: Policy | None = None,
        *,
        signing_key: Ed25519PrivateKey | None = None,  # gitleaks:allow (type annotation, not a value)
        approver_key: Ed25519PrivateKey | None = None,  # gitleaks:allow (type annotation, not a value)
        db_path: str = ":memory:",
        clock: Clock = system_clock,
        proposal_ttl: int = 600,
    ) -> None:
        self.policy = policy or default_policy()
        self._signer_key = signing_key or Ed25519PrivateKey.generate()
        self._approver_key = approver_key or Ed25519PrivateKey.generate()
        self.signer = ReceiptSigner(self._signer_key, self.name)
        self.approver_key_id = key_id(self._approver_key.public_key())
        self._approver_public = {self.approver_key_id: self._approver_key.public_key()}
        self._clock = clock
        self._proposal_ttl = proposal_ttl
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def public_keys(self) -> dict[str, Any]:
        return {
            "authorizer": self.name,
            "receipt_signer": {"key_id": self.signer.key_id, "public_key_hex": public_hex(self._signer_key.public_key())},
            "approvers": [{"key_id": self.approver_key_id, "public_key_hex": public_hex(self._approver_key.public_key())}],
        }

    def _receipt(self, *, planner: str, model_id: str, **fields: Any) -> dict[str, Any]:
        receipt = self.signer.issue(now=self._clock(), planner=planner, model_id=model_id, **fields)
        self._db.execute("INSERT INTO receipts (receipt_id, body) VALUES (?, ?)",
                         (receipt["receipt_id"], json.dumps(receipt, sort_keys=True)))
        return receipt

    def propose(self, action: dict[str, Any], *, planner: str = "OPERATOR", model_id: str = "none") -> Proposal:
        planner, model_id = _check_provenance(planner, model_id)
        hash_hex = action_hash(action)
        result = self.policy.evaluate(action)
        decision = _DECISION_MAP[result.decision]
        proposal_id = os.urandom(16).hex()
        now = self._clock()
        expires = now + self._proposal_ttl
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO proposals (proposal_id, action_json, action_hash, decision, reason, "
                    "created_at, expires_at, planner, model_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (proposal_id, canonical_bytes(action).decode("utf-8"), hash_hex, decision, result.reason,
                     now, expires, planner, model_id),
                )
                receipt = None
                if decision == base.DENY:
                    receipt = self._receipt(planner=planner, model_id=model_id, allowed=False, reason=base.POLICY_DENY,
                                            detail=result.reason, proposal_id=proposal_id, action=action,
                                            approved_action_hash=None, approval=None)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return Proposal(proposal_id, action, hash_hex, decision, result.reason, rfc3339(expires), receipt,
                        planner, model_id)

    def approve(self, proposal_id: str, action_hash_hex: str, ttl_seconds: int = 300) -> Approval:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= MAX_APPROVAL_TTL:
            raise AuthorizerError("BAD_TTL", f"ttl_seconds must be 1..{MAX_APPROVAL_TTL}")
        with self._lock:
            row = self._db.execute(
                "SELECT action_hash, decision, expires_at, consumed_at FROM proposals WHERE proposal_id = ?",
                (proposal_id,)).fetchone()
            if row is None:
                raise AuthorizerError(base.UNKNOWN_PROPOSAL, "no such proposal")
            stored_hash, decision, proposal_expires, consumed_at = row
            if decision != base.NEEDS_APPROVAL:
                raise AuthorizerError("NOT_APPROVABLE", f"proposal decision is {decision}")
            if consumed_at is not None:
                raise AuthorizerError(base.ALREADY_CONSUMED, "proposal already used")
            if action_hash_hex != stored_hash:
                raise AuthorizerError(base.ACTION_MISMATCH, "action_hash does not match the proposal")
            now = self._clock()
            if now >= proposal_expires:
                raise AuthorizerError(base.PROPOSAL_EXPIRED, "proposal expired")
            if self._db.execute("SELECT 1 FROM approvals WHERE proposal_id = ?", (proposal_id,)).fetchone():
                raise AuthorizerError("ALREADY_APPROVED", "proposal already has an approval")
            approval_id = os.urandom(16).hex()
            expires_at = rfc3339(now + ttl_seconds)
            payload = approval_payload(approval_id, proposal_id, stored_hash, self.approver_key_id, expires_at)
            signature = sign_b64(self._approver_key, canonical_bytes(payload))
            self._db.execute(
                "INSERT INTO approvals (approval_id, proposal_id, action_hash, approver_key_id, expires_at, signature)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (approval_id, proposal_id, stored_hash, self.approver_key_id, now + ttl_seconds, signature),
            )
        return Approval(approval_id, proposal_id, stored_hash, self.approver_key_id, expires_at, signature)

    def _approval_valid(self, approval: Approval) -> bool:
        public = self._approver_public.get(approval.approver_key_id)
        if public is None:
            return False
        payload = approval_payload(approval.approval_id, approval.proposal_id, approval.action_hash,
                                   approval.approver_key_id, approval.expires_at)
        try:
            public.verify(base64.b64decode(approval.signature, validate=True), canonical_bytes(payload))
        except (InvalidSignature, ValueError):
            return False
        return True

    def authorize_execution(self, proposal_id: str, action: dict[str, Any], *,
                            planner: str = "OPERATOR", model_id: str = "none") -> ExecutionDecision:
        planner, model_id = _check_provenance(planner, model_id)
        attempted_hash = action_hash(action)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                decision = self._decide(proposal_id, action, attempted_hash, planner, model_id)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return decision

    def _decide(self, proposal_id: str, action: dict[str, Any], attempted_hash: str,
                caller_planner: str, caller_model: str) -> ExecutionDecision:
        now = self._clock()
        row = self._db.execute(
            "SELECT action_hash, decision, reason, expires_at, consumed_at, planner, model_id "
            "FROM proposals WHERE proposal_id = ?",
            (proposal_id,)).fetchone()

        def refuse(reason: str, detail: str, approved_hash: str | None = None,
                   approval: Approval | None = None, planner: str | None = None,
                   model_id: str | None = None) -> ExecutionDecision:
            receipt = self._receipt(
                planner=planner or caller_planner, model_id=model_id or caller_model,
                allowed=False, reason=reason, detail=detail, proposal_id=proposal_id,
                action=action, approved_action_hash=approved_hash,
                approval=approval.as_dict() if approval else None)
            return ExecutionDecision(False, reason, receipt)

        if row is None:
            return refuse(base.UNKNOWN_PROPOSAL, "no such proposal")
        stored_hash, decision, policy_reason, proposal_expires, consumed_at, stored_planner, stored_model = row
        if attempted_hash != stored_hash:
            return refuse(base.ACTION_MISMATCH, "the action differs from the one proposed", stored_hash,
                          planner=stored_planner, model_id=stored_model)
        if decision == base.DENY:
            return refuse(base.POLICY_DENY, policy_reason, stored_hash, planner=stored_planner, model_id=stored_model)

        approval = None
        if decision == base.NEEDS_APPROVAL:
            arow = self._db.execute(
                "SELECT approval_id, action_hash, approver_key_id, expires_at, signature FROM approvals"
                " WHERE proposal_id = ?", (proposal_id,)).fetchone()
            if arow is None:
                return refuse(base.NOT_APPROVED, "this action needs an approval and has none", stored_hash,
                              planner=stored_planner, model_id=stored_model)
            approval = Approval(arow[0], proposal_id, arow[1], arow[2], rfc3339(arow[3]), arow[4])
            if not self._approval_valid(approval):
                return refuse(base.APPROVAL_SIGNATURE_INVALID, "approval signature does not verify", stored_hash,
                              approval, stored_planner, stored_model)
            if approval.action_hash != attempted_hash:
                return refuse(base.ACTION_MISMATCH, "the approval is for a different action", stored_hash, approval,
                              stored_planner, stored_model)

        if consumed_at is not None:
            return refuse(base.ALREADY_CONSUMED, "this proposal was already used", stored_hash, approval,
                          stored_planner, stored_model)
        if approval is not None and now >= arow[3]:
            return refuse(base.APPROVAL_EXPIRED, "the approval has expired", stored_hash, approval,
                          stored_planner, stored_model)
        if approval is None and now >= proposal_expires:
            return refuse(base.PROPOSAL_EXPIRED, "the proposal has expired", stored_hash,
                          planner=stored_planner, model_id=stored_model)

        updated = self._db.execute(
            "UPDATE proposals SET consumed_at = ? WHERE proposal_id = ? AND consumed_at IS NULL",
            (now, proposal_id)).rowcount
        if updated != 1:
            return refuse(base.ALREADY_CONSUMED, "this proposal was already used", stored_hash, approval,
                          stored_planner, stored_model)
        reason = base.APPROVED if approval is not None else base.POLICY_ALLOW
        detail = "approved by a person" if approval is not None else policy_reason
        receipt = self._receipt(planner=stored_planner, model_id=stored_model, allowed=True, reason=reason,
                                detail=detail, proposal_id=proposal_id, action=action,
                                approved_action_hash=stored_hash, approval=approval.as_dict() if approval else None)
        return ExecutionDecision(True, reason, receipt)

    def refuse_execution(self, proposal_id: str, action: dict[str, Any], *, reason: str, detail: str,
                         planner: str = "OPERATOR", model_id: str = "none") -> ExecutionDecision:
        """Sign a refusal without consuming the proposal. Used when guided mode blocks execution."""
        planner, model_id = _check_provenance(planner, model_id)
        attempted_hash = action_hash(action)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT action_hash, planner, model_id FROM proposals WHERE proposal_id = ?",
                    (proposal_id,)).fetchone()
                if row is None:
                    receipt = self._receipt(
                        planner=planner, model_id=model_id, allowed=False, reason=base.UNKNOWN_PROPOSAL,
                        detail="no such proposal", proposal_id=proposal_id, action=action,
                        approved_action_hash=None, approval=None)
                    decision = ExecutionDecision(False, base.UNKNOWN_PROPOSAL, receipt)
                elif attempted_hash != row[0]:
                    receipt = self._receipt(
                        planner=row[1], model_id=row[2], allowed=False, reason=base.ACTION_MISMATCH,
                        detail="the action differs from the one proposed", proposal_id=proposal_id, action=action,
                        approved_action_hash=row[0], approval=None)
                    decision = ExecutionDecision(False, base.ACTION_MISMATCH, receipt)
                else:
                    receipt = self._receipt(
                        planner=row[1], model_id=row[2], allowed=False, reason=reason, detail=detail,
                        proposal_id=proposal_id, action=action, approved_action_hash=row[0], approval=None)
                    decision = ExecutionDecision(False, reason, receipt)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return decision

    def tamper_approval_signature_for_tests(self, proposal_id: str) -> None:
        """Corrupt a stored approval signature (used by tests only)."""
        with self._lock:
            sig = self._db.execute("SELECT signature FROM approvals WHERE proposal_id = ?", (proposal_id,)).fetchone()[0]
            raw = bytearray(base64.b64decode(sig))
            raw[0] ^= 0x01
            self._db.execute("UPDATE approvals SET signature = ? WHERE proposal_id = ?",
                             (base64.b64encode(bytes(raw)).decode("ascii"), proposal_id))
