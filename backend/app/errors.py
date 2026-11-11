"""Uniform API error format. LEAD-OWNED CONTRACT.

Every non-2xx response body is:
    {"error": {"code": "<machine_code>", "message": "<safe human text>", "details": {...}}}

Codes (HTTP):
    unauthenticated 401 · forbidden 403 · not_found 404 · conflict 409 · stale_version 409 ·
    validation_error 422 · rate_limited 429 · demo_disabled 404 · invalid_state 409 · internal 500
Resource-level denial (patient asks for another patient's episode; staff not assigned) returns
404 not_found so existence is not revealed. Role-level denial on a visible resource (nurse calls a
doctor-only action) returns 403 forbidden.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("ninety_six.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def unauthenticated(msg: str = "Please sign in.") -> ApiError:
    return ApiError(401, "unauthenticated", msg)


def forbidden(msg: str = "Your role cannot perform this action.") -> ApiError:
    return ApiError(403, "forbidden", msg)


def not_found(msg: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", msg)


def conflict(msg: str, details: Optional[dict[str, Any]] = None, code: str = "conflict") -> ApiError:
    return ApiError(409, code, msg, details)


def invalid_state(msg: str, details: Optional[dict[str, Any]] = None) -> ApiError:
    return ApiError(409, "invalid_state", msg, details)


def validation(msg: str, details: Optional[dict[str, Any]] = None) -> ApiError:
    return ApiError(422, "validation_error", msg, details)


def rate_limited(msg: str = "Too many attempts. Please wait and try again.", retry_after: int = 60) -> ApiError:
    return ApiError(429, "rate_limited", msg, {"retry_after_seconds": retry_after})


def demo_disabled() -> ApiError:
    return ApiError(404, "demo_disabled", "Not found.")


def _body(code: str, message: str, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):  # noqa: ANN202
        headers = {}
        if exc.status == 429:
            headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 60))
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):  # noqa: ANN202
        errs = [
            {"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse(_body("validation_error", "Some fields need attention.", {"fields": errs}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):  # noqa: ANN202
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(_body(code, str(exc.detail) if exc.status_code != 404 else "Not found."), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):  # noqa: ANN202
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(_body("internal", "Something went wrong on the server. Please try again."), status_code=500)
