"""Ed25519 key helpers (via the ``cryptography`` package)."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def raw_public(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_hex(public_key: Ed25519PublicKey) -> str:
    return raw_public(public_key).hex()


def key_id(public_key: Ed25519PublicKey) -> str:
    """Key id: lowercase hex SHA-256 of the raw 32-byte public key."""
    return hashlib.sha256(raw_public(public_key)).hexdigest()


def sign_b64(private_key: Ed25519PrivateKey, data: bytes) -> str:
    return base64.b64encode(private_key.sign(data)).decode("ascii")


def load_or_create(path: str | Path | None) -> Ed25519PrivateKey:
    """Load a PEM private key from ``path``; create one there if missing.

    With ``path=None`` an ephemeral in-memory key is returned. Key files are
    created with mode 0600 and must never be committed (see .gitignore).
    """
    if path is None:
        return Ed25519PrivateKey.generate()
    path = Path(path)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("key file is not an Ed25519 private key")
        return key
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    path.write_bytes(pem)
    return key
