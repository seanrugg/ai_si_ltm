"""Random 32-byte opaque tokens + SHA-256 hashing."""
from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 32


def issue() -> bytes:
    """Return a fresh 32-byte random token."""
    return secrets.token_bytes(TOKEN_BYTES)


def issue_token() -> bytes:
    """Return a fresh 32-byte raw bearer token as bytes."""
    return issue()


def revoke_token(raw: bytes) -> None:
    """Mark a raw 32-byte token as revoked (caller-owned: delete / flag it)."""
    # In production this would flag the ActiveToken row revoked_at=now().
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw token must be bytes")


def hash_token(raw: bytes) -> bytes:
    """SHA-256 a raw 32-byte bearer token into its stored form."""
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw token must be bytes")
    return hashlib.sha256(raw).digest()


def hash_token_hex(raw: bytes) -> str:
    return hash_token(raw).hex()
