"""Uniform JSON errors: {"error": {"code": ..., "message": ...}}."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.services.errors import ServiceError
from server.services.ingest import IngestRejected


def _err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError):
        return _err(exc.status, exc.code, exc.message)

    @app.exception_handler(IngestRejected)
    async def _ingest(_: Request, exc: IngestRejected):
        status = 403 if exc.code in ("not_active", "forbidden") else 422
        return _err(status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return _err(422, "invalid_request", "request failed validation", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _err(exc.status_code, "http_error", str(exc.detail))
