import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import catalog, questions, retrieval, sessions, system
from app.config import APP_NAME, APP_VERSION, BASE_DIR, get_settings
from app.db import init_db, new_session, reset_engine
from app.services.catalog import CatalogError
from app.services.cortex.base import CortexError
from app.services.metadata.base import MetadataError
from app.services.retrieval import VectorIndexError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
STATIC_DIR = BASE_DIR / "static"

# Every request that changes data must carry this header. Other websites can't add custom headers to
# cross-site requests without a CORS preflight (never allowed here), so this blocks CSRF from the browser.
APP_HEADER = ("x-requested-with", "semantic-hub")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


def _first_run_bootstrap() -> None:
    """A fresh install seeds the graph store from the catalog, so it works without running the CLI."""
    from app.services.ontology import bootstrap
    from app.services.ontology.store import SqliteGraphStore

    with new_session() as db:
        if not SqliteGraphStore(db).is_empty():
            return
        try:
            bootstrap(db)
            log.info("First run: graph store seeded from the catalog")
        except CatalogError as exc:
            log.error("First-run bootstrap failed, the JSON catalog can't be used either: %s", exc)


def _first_run_index() -> None:
    """Build the metadata index when it's empty. A failure (e.g. BigQuery unreachable) only means retrieval
    falls back to exact matching until `python -m app.cli index` succeeds."""
    from app.models import IndexDocument
    from app.services.retrieval import build_index

    with new_session() as db:
        if db.query(IndexDocument).first() is not None:
            return
        try:
            result = build_index(db)
            log.info("First run: metadata index built (%s documents)", result.documents)
        except Exception as exc:  # noqa: BLE001
            log.warning("First-run indexing failed; retrieval will use exact matches only: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings = get_settings()
    if settings.auto_bootstrap:
        _first_run_bootstrap()
    if settings.auto_index:
        _first_run_index()
    yield
    reset_engine()


_docs = get_settings().enable_api_docs
app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url=None, openapi_url="/openapi.json" if _docs else None)


@app.middleware("http")
async def security(request: Request, call_next):
    # No sign-in, so refuse requests addressed to any other host name (DNS rebinding from a web page).
    raw = (request.headers.get("host") or "").lower()
    host = raw[: raw.index("]") + 1] if raw.startswith("[") and "]" in raw else raw.rsplit(":", 1)[0]
    if host not in LOCAL_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Semantic Hub only answers on this computer."})
    if (request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get(APP_HEADER[0]) != APP_HEADER[1]):
        return JSONResponse(status_code=403, content={"detail": "Request blocked. Please use the Semantic Hub app."})
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        if not (request.url.path == "/docs" and k == "Content-Security-Policy"):
            response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path == "/" or request.url.path.startswith("/static/"):
        # Revalidate every time (cheap: unchanged files come back as 304), so updates show without a hard refresh
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.exception_handler(MetadataError)
@app.exception_handler(CortexError)
async def friendly_error(_: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(VectorIndexError)
async def index_error(_: Request, exc: VectorIndexError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(CatalogError)
async def catalog_error(_: Request, exc: CatalogError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


for r in (system.router, catalog.router, retrieval.router, questions.router, sessions.router):
    app.include_router(r)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
