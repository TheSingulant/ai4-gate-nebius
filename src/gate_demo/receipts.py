"""Building and signing receipts. The format is specified in RECEIPT_SPEC.md.

This edition adds two signed fields, ``planner`` and ``model_id``, so a receipt
shows which planner produced the action. The signature covers both.
"""

from __future__ import annotations

import os
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gate_demo.canonical import action_hash, canonical_bytes
from gate_demo.clock import rfc3339
from gate_demo.keys import key_id, sign_b64

RECEIPT_VERSION = "gate-receipt/1"
APPROVAL_VERSION = "gate-approval/1"
PLANNER_LABELS = ("NEMOTRON-LIVE", "SCRIPTED", "OPERATOR")


def approval_payload(approval_id: str, proposal_id: str, hash_hex: str, approver_key_id: str,
                     expires_at: str) -> dict[str, Any]:
    """The exact object an approver signs."""
    return {
        "approval_version": APPROVAL_VERSION,
        "approval_id": approval_id,
        "proposal_id": proposal_id,
        "action_hash": hash_hex,
        "approver_key_id": approver_key_id,
        "expires_at": expires_at,
    }


class ReceiptSigner:
    def __init__(self, private_key: Ed25519PrivateKey, authorizer: str) -> None:
        self._key = private_key
        self.authorizer = authorizer
        self.key_id = key_id(private_key.public_key())

    def issue(self, *, allowed: bool, reason: str, detail: str, proposal_id: str | None,
              action: dict[str, Any], approved_action_hash: str | None,
              approval: dict[str, Any] | None, now: int, planner: str, model_id: str) -> dict[str, Any]:
        if planner not in PLANNER_LABELS:
            raise ValueError(f"planner must be one of {PLANNER_LABELS}")
        body: dict[str, Any] = {
            "receipt_version": RECEIPT_VERSION,
            "receipt_id": os.urandom(16).hex(),
            "authorizer": self.authorizer,
            "decision": "allowed" if allowed else "refused",
            "reason": reason,
            "detail": detail,
            "proposal_id": proposal_id,
            "action": action,
            "action_hash": action_hash(action),
            "approved_action_hash": approved_action_hash,
            "approval": approval,
            "issued_at": rfc3339(now),
            "nonce": os.urandom(16).hex(),
            "signer_key_id": self.key_id,
            "planner": planner,
            "model_id": model_id,
        }
        body["signature"] = sign_b64(self._key, canonical_bytes(body))
        return body
