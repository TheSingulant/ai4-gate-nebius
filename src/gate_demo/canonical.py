"""Canonical JSON encoding used for action hashes and receipt signatures.

The accepted value space is deliberately small: objects with ASCII string keys,
arrays, strings, booleans, null and integers within +/-(2**53 - 1). Floats are
rejected, never rounded. For this subset, compact JSON with keys sorted by code
point produces the same bytes as RFC 8785 (JSON Canonicalization Scheme),
because ASCII keys sort identically by code point and by UTF-16 code unit, and
Python escapes control characters the same way RFC 8785 does.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalError(ValueError):
    """The value cannot be encoded canonically."""


def _check(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalError(f"{path}: integer outside the safe range")
        return
    if isinstance(value, float):
        raise CanonicalError(f"{path}: floats are not allowed")
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise CanonicalError(f"{path}: object keys must be ASCII strings")
            _check(item, f"{path}.{key}")
        return
    raise CanonicalError(f"{path}: unsupported type {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical UTF-8 bytes of ``value``."""
    _check(value, "$")
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:  # lone surrogates
        raise CanonicalError("string is not valid Unicode") from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def action_hash(action: dict[str, Any]) -> str:
    """SHA-256 (lowercase hex) of the action's canonical bytes."""
    return sha256_hex(canonical_bytes(action))
