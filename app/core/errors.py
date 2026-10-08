"""Application errors and the handlers that turn them into consistent JSON responses."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """Shape of every error body returned by the API."""

    detail: str


class AppError(Exception):
    """Base class for errors that map directly to an HTTP response."""

    status_code = 500

    def __init__(self, detail: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra or {}


class BadRequestError(AppError):
    status_code = 400


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail, **exc.extra}
    )


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # The traceback goes to the log only; the client gets a generic message.
    logger.error("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
