"""Request plumbing: transactions, auth dependencies, rate limits."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AdminUser
from server.services import auth as auth_service
from server.services.errors import ServiceError
from server.services.ingest import IngestRejected

ADMIN_COOKIE = "ltms_admin"
ADMIN_CSRF_HEADER = "x-ltms-admin"


@asynccontextmanager
async def transaction(request: Request):
    """Commit on success. Also commit when a *service* error is raised, so that
    audit entries and single-use challenge deletions persist; roll back on
    anything unexpected."""
    session: AsyncSession = request.app.state.sessionmaker()
    try:
        yield session
        await session.commit()
    except (ServiceError, IngestRejected):
        await session.commit()
        raise
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


def client_ip(request: Request) -> str | None:
    # Behind nginx, run uvicorn/gunicorn with --proxy-headers and
    # --forwarded-allow-ips=127.0.0.1 so this is the real client address.
    return request.client.host if request.client else None


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@dataclass
class MemberAuth:
    member_id: object
    name: str
    permissions: list[str]


async def current_member(request: Request, authorization: str | None = Header(default=None)) -> MemberAuth:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ServiceError(401, "invalid_token", "Authorization: Bearer <token> required")
    raw = authorization.split(" ", 1)[1].strip()
    async with transaction(request) as session:
        member, perms = await auth_service.member_from_token(session, raw)
        return MemberAuth(member.id, member.name, perms)


def require(permission: str, *alternatives: str):
    async def dep(request: Request, auth: MemberAuth = Depends(current_member)) -> MemberAuth:
        if not any(p in auth.permissions for p in (permission, *alternatives)):
            raise ServiceError(403, "forbidden", f"requires the {permission!r} permission")
        limiter = request.app.state.limiters["ingest" if permission == "contribute" else "recall"]
        if not limiter.allow(str(auth.member_id)):
            raise ServiceError(429, "rate_limited", "too many requests; slow down")
        return auth
    return dep


@dataclass
class AdminAuth:
    id: object
    username: str


async def current_admin(request: Request) -> AdminAuth:
    raw = request.cookies.get(ADMIN_COOKIE)
    async with transaction(request) as session:
        admin: AdminUser = await auth_service.admin_from_session(session, raw)
        result = AdminAuth(admin.id, admin.username)
    # CSRF guard: state-changing admin calls must carry a custom header, which a
    # cross-site form or image request cannot set. (The cookie is also SameSite=Strict.)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(ADMIN_CSRF_HEADER) != "1":
        raise ServiceError(403, "csrf", f"missing {ADMIN_CSRF_HEADER}: 1 header")
    return result
