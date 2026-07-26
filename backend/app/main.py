import asyncio
import logging
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
from app.common.degradation import intent_health
from app.common.errors import install_error_handlers
from app.common.intent_probe import probe_intent
from app.common.logging_setup import configure_logging
from app.common.store import store

logger = logging.getLogger(__name__)
settings = get_settings()


async def _run_intent_probe() -> None:
    """Record whether the intent deployment accepts the request we send it."""
    from app.assistant.provider import build_ai_provider
    from app.common.persistence import catalog_products

    try:
        products = await catalog_products()
        result = await probe_intent(
            build_ai_provider(),
            categories=sorted({product["category"] for product in products}),
            destinations=sorted({product["destination"] for product in products}),
        )
    except Exception as error:  # noqa: BLE001
        # The probe failing to run is not the deployment rejecting us, and must
        # not be reported as though it were.
        logger.warning("Intent probe did not run: %s", error)
        intent_health.record_probe(ok=True, detail=f"probe did not run: {error}")
        return
    if result.ok:
        logger.info("Intent probe: %s", result.detail)
    else:
        logger.error("Intent probe FAILED: %s", result.detail)
    intent_health.record_probe(ok=result.ok, detail=result.detail)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
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
    # Run alongside startup rather than blocking it: a slow model must not stop
    # the service coming up, and readiness stays false until the answer is in.
    probe = asyncio.create_task(_run_intent_probe())
    try:
        yield
    finally:
        probe.cancel()
        with suppress(asyncio.CancelledError):
            await probe
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
    # Bodies differ by language and by session, and both arrive in headers
    # rather than in the URL, so nothing downstream can tell two such responses
    # apart without being told. This is not a precaution against a CDN we do
    # not have yet: the browser's own cache is enough to hand a shopper who
    # just switched to Vietnamese the English response it already had.
    #
    # Merged, not assigned. CORSMiddleware sets `Vary: Origin` on credentialed
    # responses, and overwriting it makes one origin's preflight answer
    # cacheable for another - trading a caching bug for a security one.
    varies = [part.strip() for part in response.headers.get("Vary", "").split(",") if part.strip()]
    seen = {part.lower() for part in varies}
    for header in ("Accept-Language", "X-Session-ID"):
        if header.lower() not in seen:
            varies.append(header)
    response.headers["Vary"] = ", ".join(varies)
    return response


@app.get("/health/live")
async def live() -> JSONResponse:
    return JSONResponse({"status": "live"})


@app.get("/health/ready")
async def ready() -> JSONResponse:
    database = await database_ready()
    probe = intent_health.probe()
    # A revision whose every intent call is rejected must not take traffic. It
    # would serve HTTP 200 and a full page for each search while understanding
    # none of them, which is indistinguishable from working and is precisely how
    # two outages survived their own deployments. Failing readiness here leaves
    # the previous revision serving, which does understand its shoppers.
    intent_ok = probe is None or probe.ok
    healthy = (settings.demo_mode or database) and intent_ok
    body = {
        "status": "ready" if healthy else "degraded",
        "database": database,
        # None until the probe has answered: unknown is not the same as good,
        # and saying so keeps a slow start from reading as a failed one.
        "intent": None if probe is None else {"ok": probe.ok, "detail": probe.detail},
    }
    return JSONResponse(body, status_code=200 if healthy else 503)


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
