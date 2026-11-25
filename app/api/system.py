from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import APP_NAME, APP_VERSION, get_settings
from app.db import check_db, get_db
from app.services.health import run_healthcheck
from app.services.ontology import bootstrap

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/app")
def app_info():
    s = get_settings()
    return {"name": APP_NAME, "version": APP_VERSION, "metadata_mode": s.metadata_mode, "cortex_mode": s.cortex_mode}


@router.get("/health")
def health():
    """Quick liveness check (database only)."""
    ok, _ = check_db()
    return JSONResponse(status_code=200 if ok else 503, content={"ok": ok})


@router.get("/health/full")
async def health_full(db: Session = Depends(get_db)):
    # Some checks (Chroma, BigQuery, Cortex) block, so they run off the event loop.
    return await run_in_threadpool(run_healthcheck, db)


@router.post("/bootstrap")
def run_bootstrap(reset: bool = False, db: Session = Depends(get_db)):
    steps = bootstrap(db, reset=reset)
    return {"steps": [s.__dict__ for s in steps]}
