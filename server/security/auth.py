"""Admin password + opaque session helpers.

Admin login is bcrypt-hashed password + a 32-byte opaque session cookie stored
in ``admin_sessions`` (separate from member tokens). Session ids are cryptographically
random and never reveal the username.
"""
from __future__ import annotations

import hashlib
import secrets

SESSION_BYTES = 32


def issue_session() -> bytes:
    """Return a fresh 32-byte opaque session id."""
    return secrets.token_bytes(SESSION_BYTES)


def issue_session_hex() -> str:
    """Return a hex-encoded 32-byte opaque session id."""
    return issue_session().hex()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.

    Returns ``False`` for any malformed hash rather than raising, so a missing
    admin_user row or a foreign-format hash never leaks through an exception.
    """
    try:
        import bcrypt

        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of ``plain`` (returned as a str)."""
    import bcrypt

    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 of ``data``."""
    return hashlib.sha256(bytes(data)).hexdigest()
