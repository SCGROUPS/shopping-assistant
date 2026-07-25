import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.admin.routes import router as admin_router
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
        return

    # Content changes enqueue a reindex in their own transaction (see
    # `catalog.indexing`); something has to drain the queue. A task in the
    # application process is the right size for a single-replica deployment,
    # and means an operator's correction reaches search in seconds rather than
    # waiting for a manually triggered catalog job.
    from app.catalog.indexing import index_worker_loop

    worker = asyncio.create_task(index_worker_loop())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


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
app.include_router(admin_router)


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

    class SinglePageFiles(StaticFiles):
        """Serve the SPA shell for client-side routes, and only for those.

        The bare StaticFiles mount answers `/` but 404s on `/admin`, because
        no such directory is built. A blanket fallback overcorrects: a missing
        bundle or a mistyped API path would return the shell with a 200, so a
        broken deploy looks healthy and a client parses HTML as JSON. Only
        extensionless, non-API paths are treated as frontend routes.
        """

        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404:
                    raise
                head = path.split("/", 1)[0]
                looks_like_a_file = "." in path.rsplit("/", 1)[-1]
                if looks_like_a_file or head in {"api", "health"}:
                    raise
                return await super().get_response("index.html", scope)

    app.mount("/", SinglePageFiles(directory=static_directory, html=True), name="frontend")
