"""Verify a gate receipt offline.

Usage::

    python verify.py receipt.json --trusted-key HEX [--approver-key HEX] [--json]

``--trusted-key`` is the receipt signer's raw Ed25519 public key as 64 hex
characters. Exit status: 0 valid, 1 invalid, 2 usage or input error.

Written separately from the gate (see RECEIPT_SPEC.md). Uses only the standard
library and ``cryptography``. It never contacts the network.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

RECEIPT_VERSION = "gate-receipt/1"
APPROVAL_VERSION = "gate-approval/1"
PLANNER_LABELS = {"NEMOTRON-LIVE", "SCRIPTED", "OPERATOR"}
LIMITATION = ("This checks receipt integrity and signer identity only. It does not prove the tool ran, "
              "that it succeeded, or that the approver was the person you think.")

_FIELDS: dict[str, tuple[type, ...]] = {
    "receipt_version": (str,),
    "receipt_id": (str,),
    "authorizer": (str,),
    "decision": (str,),
    "reason": (str,),
    "detail": (str,),
    "proposal_id": (str, type(None)),
    "action": (dict,),
    "action_hash": (str,),
    "approved_action_hash": (str, type(None)),
    "approval": (dict, type(None)),
    "issued_at": (str,),
    "nonce": (str,),
    "signer_key_id": (str,),
    "planner": (str,),
    "model_id": (str,),
    "signature": (str,),
}
_APPROVAL_FIELDS = ("approval_id", "proposal_id", "action_hash", "approver_key_id", "expires_at", "signature")
_SAFE = 2**53 - 1


class _Encode(ValueError):
    pass


def _canon(value: Any) -> str:
    """Canonical JSON (RFC 8785 for the restricted value space), written independently."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > _SAFE:
            raise _Encode("integer outside the safe range")
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_canon(v) for v in value) + "]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            if not isinstance(key, str) or not key.isascii():
                raise _Encode("object keys must be ASCII strings")
            parts.append(_string(key) + ":" + _canon(value[key]))
        return "{" + ",".join(parts) + "}"
    raise _Encode(f"unsupported JSON value {type(value).__name__}")


_SHORT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch in _SHORT:
            out.append(_SHORT[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        elif 0xD800 <= ord(ch) <= 0xDFFF:
            raise _Encode("lone surrogate in string")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical(value: Any) -> bytes:
    return _canon(value).encode("utf-8")


def _key_from_hex(hex_text: str) -> Ed25519PublicKey:
    try:
        raw = bytes.fromhex(hex_text)
    except ValueError as exc:
        raise ValueError("key must be hex") from exc
    if len(raw) != 32:
        raise ValueError("key must be 32 bytes (64 hex characters)")
    return Ed25519PublicKey.from_public_bytes(raw)


def _sig_ok(public: Ed25519PublicKey, signature_b64: str, data: bytes) -> bool:
    try:
        public.verify(base64.b64decode(signature_b64, validate=True), data)
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


@dataclass
class Result:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)


def verify_receipt(receipt: Any, trusted_key_hex: str, approver_key_hex: str | None = None) -> Result:
    result = Result()
    trusted = _key_from_hex(trusted_key_hex)
    approver = _key_from_hex(approver_key_hex) if approver_key_hex else None

    if not isinstance(receipt, dict):
        result.fail("receipt is not a JSON object")
        return result
    missing = [k for k in _FIELDS if k not in receipt]
    extra = sorted(set(receipt) - set(_FIELDS))
    if missing:
        result.fail(f"missing fields: {missing}")
    if extra:
        result.fail(f"unexpected fields: {extra}")
    for name, types in _FIELDS.items():
        if name in receipt and not isinstance(receipt[name], types):
            result.fail(f"field {name} has the wrong type")
    if not result.valid:
        return result
    if receipt["receipt_version"] != RECEIPT_VERSION:
        result.fail(f"unsupported receipt_version {receipt['receipt_version']!r}")
        return result
    if receipt["planner"] not in PLANNER_LABELS:
        result.fail("planner must be NEMOTRON-LIVE, SCRIPTED, or OPERATOR")
    model_id = receipt["model_id"]
    if not model_id or len(model_id) > 128 or any(ord(ch) < 32 for ch in model_id):
        result.fail("model_id is missing or not a short string")

    trusted_raw = bytes.fromhex(trusted_key_hex)
    if receipt["signer_key_id"] != hashlib.sha256(trusted_raw).hexdigest():
        result.fail("SIGNER_MISMATCH: receipt was not signed by the trusted key")

    unsigned = {k: v for k, v in receipt.items() if k != "signature"}
    try:
        signed_bytes = canonical(unsigned)
    except _Encode as exc:
        result.fail(f"receipt cannot be canonicalized: {exc}")
        return result
    if not _sig_ok(trusted, receipt["signature"], signed_bytes):
        result.fail("SIGNATURE_INVALID: signature does not verify over the canonical receipt")

    try:
        recomputed = hashlib.sha256(canonical(receipt["action"])).hexdigest()
    except _Encode as exc:
        result.fail(f"action cannot be canonicalized: {exc}")
        recomputed = None
    if recomputed is not None and recomputed != receipt["action_hash"]:
        result.fail("ACTION_HASH_MISMATCH: action_hash does not match the action")

    decision, reason = receipt["decision"], receipt["reason"]
    if decision not in ("allowed", "refused"):
        result.fail("decision must be allowed or refused")
    if decision == "allowed" and receipt["approved_action_hash"] != receipt["action_hash"]:
        result.fail("allowed receipt must bind approved_action_hash to action_hash")

    approval = receipt["approval"]
    if decision == "allowed" and reason == "APPROVED" and approval is None:
        result.fail("APPROVED receipt has no approval")
    if approval is not None:
        if sorted(approval) != sorted(_APPROVAL_FIELDS) or not all(isinstance(approval[k], str) for k in approval):
            result.fail("approval has the wrong fields")
        else:
            if decision == "allowed" and approval["action_hash"] != receipt["action_hash"]:
                result.fail("approval is for a different action")
            if approval["proposal_id"] != receipt["proposal_id"]:
                result.fail("approval is for a different proposal")
            if approver is None:
                result.notes.append("approver signature not checked (no --approver-key given)")
            else:
                approver_raw = bytes.fromhex(approver_key_hex or "")
                payload = {k: approval[k] for k in _APPROVAL_FIELDS if k != "signature"}
                payload["approval_version"] = APPROVAL_VERSION
                if approval["approver_key_id"] != hashlib.sha256(approver_raw).hexdigest():
                    result.fail("approval was not made by the given approver key")
                elif not _sig_ok(approver, approval["signature"], canonical(payload)):
                    result.fail("APPROVAL_SIGNATURE_INVALID: approver signature does not verify")
                else:
                    result.notes.append("approver signature valid")

    action = receipt["action"]
    result.summary = {
        "decision": decision,
        "reason": reason,
        "tool": action.get("tool"),
        "target": action.get("target"),
        "issued_at": receipt["issued_at"],
        "authorizer": receipt["authorizer"],
        "planner": receipt["planner"],
        "model_id": receipt["model_id"],
        "receipt_id": receipt["receipt_id"],
        "action_hash": receipt["action_hash"],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a gate receipt offline.")
    parser.add_argument("receipt", help="path to receipt JSON")
    parser.add_argument("--trusted-key", required=True, help="receipt signer public key, 64 hex characters")
    parser.add_argument("--approver-key", help="approver public key, 64 hex characters (optional)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    try:
        with open(args.receipt, encoding="utf-8") as fh:
            receipt = json.load(fh)
        result = verify_receipt(receipt, args.trusted_key, args.approver_key)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"valid": result.valid, "errors": result.errors, "notes": result.notes,
                          "summary": result.summary}, indent=2, sort_keys=True))
    else:
        print("VALID" if result.valid else "INVALID")
        for key, value in result.summary.items():
            print(f"  {key}: {value}")
        for error in result.errors:
            print(f"  error: {error}")
        for note in result.notes:
            print(f"  note: {note}")
        print(LIMITATION)
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
