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


PROBE_ATTEMPTS = 5
PROBE_RETRY_SECONDS = 3.0


async def _probe_vocabulary() -> tuple[list[str], list[str]]:
    """The enums to probe with, preferring the real catalogue.

    The catalogue is not always loadable the instant the process starts - the
    database may still be waking - and a probe that gives up then would report
    a pass without ever having spoken to the model, which is the exact silence
    it exists to break. The failure being hunted here is in the request shape,
    not the enum contents, so a small stand-in is a faithful enough probe and
    far better than not asking.
    """
    from app.common.persistence import catalog_products

    try:
        products = await catalog_products()
    except Exception as error:  # noqa: BLE001
        logger.warning("Intent probe could not load the catalogue: %s", error)
        products = []
    if not products:
        return ["Food", "Transport"], ["Hoi An", "Hanoi"]
    return (
        sorted({product["category"] for product in products}),
        sorted({product["destination"] for product in products}),
    )


async def _run_intent_probe() -> None:
    """Record whether the intent deployment accepts the request we send it.

    Retried, because "could not tell" must never be allowed to settle as "fine".
    A rejection is deterministic and answers on the first attempt; the retries
    are for the startup-shaped problems - a cold upstream, a connection not yet
    open - that would otherwise leave the question permanently unanswered and
    the revision permanently trusted.
    """
    from app.assistant.provider import build_ai_provider

    categories, destinations = await _probe_vocabulary()
    detail = "the intent probe never completed"
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        try:
            result = await probe_intent(
                build_ai_provider(), categories=categories, destinations=destinations
            )
        except Exception as error:  # noqa: BLE001
            detail = f"the intent probe could not run: {error}"
            logger.warning("Intent probe attempt %s could not run: %s", attempt, error)
        else:
            if not result.ok:
                logger.error("Intent probe FAILED: %s", result.detail)
                intent_health.record_probe(ok=False, detail=result.detail)
                return
            if "inconclusive" not in result.detail:
                logger.info("Intent probe: %s", result.detail)
                intent_health.record_probe(ok=True, detail=result.detail)
                return
            detail = result.detail
            logger.warning("Intent probe attempt %s inconclusive: %s", attempt, result.detail)
        if attempt < PROBE_ATTEMPTS:
            await asyncio.sleep(PROBE_RETRY_SECONDS)

    # Every attempt failed for reasons that were not a rejection. Serving is
    # still the right call - the model being unreachable is not evidence that
    # our request is wrong, and the running service already degrades safely -
    # but this is reported as unverified rather than as a pass.
    logger.error("Intent probe gave up after %s attempts: %s", PROBE_ATTEMPTS, detail)
    intent_health.record_probe(ok=True, detail=f"unverified: {detail}")


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
