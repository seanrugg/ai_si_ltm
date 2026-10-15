"""Exception → JSON error mapping.

Every handler-facing error is turned into ``{"detail": ..., "code": ...}`` with
the right HTTP status. ``ChainError`` and ``EnrollmentError`` carry a machine
``code``; ``AuthError`` carries its own status code. Unknown errors become a
generic 500 (without leaking internals to the client).
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.security.deps import AuthError
from server.services.chain import ChainError
from server.services.enrollment import EnrollmentError


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the exception handlers to ``app``."""

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(ChainError)
    async def _chain_error(_: Request, exc: ChainError) -> JSONResponse:
        # Never expose the raw message to unauthenticated callers; the code is
        # the stable, documented identifier.
        return JSONResponse(
            status_code=400,
            content={"detail": exc.code, "code": exc.code},
        )

    @app.exception_handler(EnrollmentError)
    async def _enrollment_error(_: Request, exc: EnrollmentError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": exc.code, "code": exc.code},
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error", "code": "internal"},
        )
