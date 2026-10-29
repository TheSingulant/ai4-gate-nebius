"""The interface every authorizer backend implements.

Flow: ``propose(action)`` records the exact action and its policy decision.
For ``needs_approval`` a person calls ``approve(proposal_id, action_hash, ttl)``;
the approval is signed over the action hash and its expiry. Before anything
runs, ``authorize_execution(proposal_id, action)`` re-hashes the action it is
handed, checks it against the proposal and the approval, consumes the proposal
once, and returns a signed receipt either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

AUTO_ALLOW = "auto_allow"
NEEDS_APPROVAL = "needs_approval"
DENY = "deny"

# Receipt reason codes.
POLICY_ALLOW = "POLICY_ALLOW"
APPROVED = "APPROVED"
POLICY_DENY = "POLICY_DENY"
UNKNOWN_PROPOSAL = "UNKNOWN_PROPOSAL"
ACTION_MISMATCH = "ACTION_MISMATCH"
NOT_APPROVED = "NOT_APPROVED"
APPROVAL_SIGNATURE_INVALID = "APPROVAL_SIGNATURE_INVALID"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
PROPOSAL_EXPIRED = "PROPOSAL_EXPIRED"
ALREADY_CONSUMED = "ALREADY_CONSUMED"
UNAPPROVED_CONTENT = "UNAPPROVED_CONTENT"


BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"


class AuthorizerError(RuntimeError):
    """A request the authorizer refuses without issuing a receipt (e.g. a bad approve call).

    ``backend_failure`` is True when the backend itself is unreachable or
    misbehaving (as opposed to refusing a request on its merits). The gate
    fails closed on these: nothing runs.
    """

    def __init__(self, code: str, message: str, *, backend: str | None = None,
                 backend_failure: bool = False) -> None:
        prefix = f"[{backend}] " if backend else ""
        super().__init__(f"{prefix}{code}: {message}")
        self.code = code
        self.backend = backend
        self.backend_failure = backend_failure


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    action: dict[str, Any]
    action_hash: str
    decision: str  # auto_allow | needs_approval | deny
    reason: str
    expires_at: str
    receipt: dict[str, Any] | None = None  # a refusal receipt when decision == deny
    planner: str = "OPERATOR"
    model_id: str = "none"


@dataclass(frozen=True)
class Approval:
    approval_id: str
    proposal_id: str
    action_hash: str
    approver_key_id: str
    expires_at: str
    signature: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "proposal_id": self.proposal_id,
            "action_hash": self.action_hash,
            "approver_key_id": self.approver_key_id,
            "expires_at": self.expires_at,
            "signature": self.signature,
        }


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    receipt: dict[str, Any] = field(repr=False)


class Authorizer(Protocol):
    name: str

    def propose(self, action: dict[str, Any]) -> Proposal: ...

    def approve(self, proposal_id: str, action_hash: str, ttl_seconds: int = 300) -> Approval: ...

    def authorize_execution(self, proposal_id: str, action: dict[str, Any]) -> ExecutionDecision: ...

    def public_keys(self) -> dict[str, Any]: ...
