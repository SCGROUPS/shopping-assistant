from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, title: str, detail: str, code: str) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code


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
            },
        )
