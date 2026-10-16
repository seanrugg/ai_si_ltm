"""Opaque random tokens. The raw value goes to the client once; only its
SHA-256 is stored, so a database leak does not leak usable tokens."""
from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def new_token() -> str:
    """URL-safe random token (32 bytes of entropy)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


def new_nonce_hex() -> str:
    return secrets.token_hex(32)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
