"""Pytest fixtures: test DB, a signed-record factory, and auth helpers.

The whole chain is exercised against a temporary file-based SQLite database so
no running PostgreSQL is required. ``signed_record`` is the single source of
truth for building a valid, chain-linked record: it computes ``body_hash`` (JCS),
signs it with domain separation, and chains ``prev_hash`` -> ``hash``.

Every record dict is in the *wire* format accepted by ``RecordIn`` /
``ingest_batch``: hex-encoded ``body_hash``/``sig``/``hash``/``prev_hash``, ISO
timestamp strings, and a ``body`` payload.
"""
from __future__ import annotations

import hashlib
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from server import db as db_module  # noqa: E402
from server import main as app_module  # noqa: E402
from server.db import async_session_factory, create_all  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402
from server.models.adminsession import AdminSession  # noqa: E402
from server.models.adminuser import AdminUser  # noqa: E402
from server.models.member import Member  # noqa: E402
from server.models.token import ActiveToken  # noqa: E402
from server.security.auth import hash_password  # noqa: E402
from server.security.key import (  # noqa: E402
    GENESIS_PREV_HASH,
    Ed25519SecretKey,
    record_hash,
    sign_record,
)
from server.security.token import hash_token, issue_token  # noqa: E402


# --------------------------------------------------------------------------- #
# Record factory
# --------------------------------------------------------------------------- #
def make_secret() -> Ed25519SecretKey:
    """Return a fresh random Ed25519 secret key (32 raw bytes)."""
    return Ed25519SecretKey(secrets.token_bytes(32))


def signed_record(
    secret: Ed25519SecretKey,
    *,
    seq: int,
    body: dict,
    prev_hash_hex: str,
    ts: str | datetime | None = None,
    type: str = "communication",
    agent: str | None = None,
    body_hash_override: Optional[str] = None,
    sig_override: Optional[str] = None,
    hash_override: Optional[str] = None,
    prev_hash_override: Optional[str] = None,
) -> dict:
    """Build a valid, chain-linked record dict in wire format.

    ``body_hash`` = SHA-256(canonical(payload)) with the record's own
    ``hash``/``sig``/``body_hash`` stripped; ``sig`` = Ed25519 over
    ``ltms-record-v1 : 64-hex(body_hash)``; ``hash`` = SHA-256(sig bytes).

    Overrides exist for tests that need to inject a tampered field.
    """
    now = ts or datetime.now(timezone.utc)
    ts_str = now.isoformat() if isinstance(now, datetime) else now

    record_for_hash = dict(body)
    record_for_hash.update(
        {
            "seq": seq,
            "type": type,
            "agent": agent,
            "ts": ts_str,
            "prev_hash": prev_hash_hex,
        }
    )
    if body_hash_override is None:
        body_hash_hex = _compute_body_hash(record_for_hash)
    else:
        body_hash_hex = body_hash_override

    if sig_override is None:
        sig_hex = sign_record(secret, body_hash_hex)
    else:
        sig_hex = sig_override

    if hash_override is None:
        link_hex = record_hash(bytes.fromhex(sig_hex)).hex()
    else:
        link_hex = hash_override

    prev = prev_hash_override if prev_hash_override is not None else prev_hash_hex

    return {
        "seq": seq,
        "ts": ts_str,
        "type": type,
        "agent": agent,
        "body_hash": body_hash_hex,
        "sig": sig_hex,
        "hash": link_hex,
        "prev_hash": prev,
        "body": body,
    }


def _compute_body_hash(record: dict) -> str:
    """SHA-256 of the canonical record (JCS), minus chain fields, as hex."""
    from server.security.hash import canonical_bytes

    cleaned = {k: v for k, v in record.items() if k not in ("hash", "sig", "body_hash")}
    return hashlib.sha256(canonical_bytes(cleaned)).hexdigest()


def genesis_prev_hash_hex() -> str:
    """Genesis marker as hex (32 zero bytes)."""
    return GENESIS_PREV_HASH.hex()


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# DB + app fixtures
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def db_session():
    """A session bound to a fresh temp-file SQLite database.

    Swaps the global engine so every handler (which opens its own
    ``session_scope``) sees the same database. SQLite keeps an in-memory
    database per connection, so a temp file is required.
    """
    import tempfile
    _tmpdir = tempfile.mkdtemp(prefix="ltms_test_")
    tmp = Path(_tmpdir) / f"ltms_test_{secrets.token_hex(4)}.db"
    await db_module.engine.dispose()
    db_module.engine = db_module.make_engine(f"sqlite+aiosqlite:///{tmp}")
    # async_session_factory is bound to the engine at module load, so rebuild it
    # bound to the test engine too — handlers that call async_session_factory()
    # directly (not get_db's session_scope) would otherwise hit the original DB.
    db_module.async_session_factory = async_sessionmaker(
        db_module.engine, class_=AsyncSession
    )
    await create_all()
    session = db_module.async_session_factory()
    try:
        yield session
    finally:
        await session.close()
        await db_module.engine.dispose()
        try:
            tmp.unlink(missing_ok=True)
            Path(_tmpdir).rmdir()
        except OSError:
            pass


@pytest_asyncio.fixture
async def client(db_session):
    """A FastAPI TestClient bound to the test database."""
    from server.db import session_scope

    async def _override_get_db():
        async with session_scope() as session:
            yield session

    app_module.app.dependency_overrides[db_module.get_db] = _override_get_db
    with TestClient(app_module.app) as test_client:
        yield test_client
    app_module.app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Member + auth helpers
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def active_member(db_session) -> Member:
    """Create and return an active, contribute+recall member with a key."""
    secret = make_secret()
    member = Member(
        name="test-member",
        kind="agent",
        operator="tester",
        permissions=["contribute", "recall"],
        status="active",
        public_key=secret.verifying_key().raw,
        enrolled_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest_asyncio.fixture
async def admin_user(db_session):
    """Create and return an admin user with a known password."""
    admin = AdminUser(username="admin", password_hash=hash_password("admin-secret"))
    db_session.add(admin)
    await db_session.flush()
    return admin


def _add_token(db_session, member, *, expired=False, revoked=False):
    """Insert an ActiveToken row and return its raw hex token."""
    raw = issue_token()
    now = datetime.now(timezone.utc)
    db_session.add(
        ActiveToken(
            member_id=member.id,
            token_hash=hash_token(raw),
            permissions=member.permissions,
            issued_at=now,
            expires_at=now + timedelta(seconds=300),
            revoked_at=(now - timedelta(seconds=600)) if revoked else None,
        )
    )
    return raw.hex()


@pytest_asyncio.fixture
async def authed_client(client, active_member, db_session):
    """A TestClient pre-loaded with a valid bearer token for ``active_member``."""
    client.token = _add_token(db_session, active_member)
    return client


@pytest_asyncio.fixture
async def authed_client_expired(client, active_member, db_session):
    """A TestClient whose bearer token is expired."""
    raw = issue_token()
    now = datetime.now(timezone.utc)
    db_session.add(
        ActiveToken(
            member_id=active_member.id,
            token_hash=hash_token(raw),
            permissions=active_member.permissions,
            issued_at=now - timedelta(seconds=600),
            expires_at=now - timedelta(seconds=300),
        )
    )
    client.token = raw.hex()
    return client


def _auth_header(client) -> dict:
    """Bearer header for the client's stored token."""
    return {"Authorization": f"Bearer {client.token}"}
