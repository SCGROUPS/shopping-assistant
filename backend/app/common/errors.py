from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code
        # RFC 7807 extension members, for refusals a client must act on rather
        # than merely display. The publish gate needs to say which six things
        # are wrong so the console can point at each one; a prose sentence
        # would have to be parsed back apart to do that.
        self.details = details or {}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": f"https://example.invalid/problems/{exc.code}",
                "title": exc.title,
                "status": exc.status,
                "detail": exc.detail,
                "instance": str(request.url.path),
                "code": exc.code,
                "correlation_id": correlation_id,
                **exc.details,
            },
        )
