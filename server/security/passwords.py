"""bcrypt password hashing for the admin account."""
from __future__ import annotations

import bcrypt

# A real bcrypt hash used to keep login timing flat when the username is unknown.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), (hashed or _DUMMY_HASH).encode("utf-8")) and hashed is not None
    except ValueError:
        return False
