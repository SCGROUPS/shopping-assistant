from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.common.config import get_settings
from app.common.database import database_ready
from app.common.errors import install_error_handlers
from app.common.store import store

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.demo_mode:
        store.seed()
    yield


app = FastAPI(
    title="Intelligent Tourism E-Ticket Shopping Assistant",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if settings.demo_mode else settings.cors_origins,
    allow_origin_regex=(
        r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if settings.demo_mode else None
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handlers(app)
app.include_router(router)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    correlation = request.headers.get("X-Correlation-ID", str(uuid4()))
    request.state.correlation_id = correlation
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health/live")
async def live() -> JSONResponse:
    return JSONResponse({"status": "live"})


@app.get("/health/ready")
async def ready() -> JSONResponse:
    database = await database_ready()
    healthy = settings.demo_mode or database
    return JSONResponse(
        {"status": "ready" if healthy else "degraded", "database": database},
        status_code=200 if healthy else 503,
    )


static_directory = Path(__file__).parent / "static"
if static_directory.exists():
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")
