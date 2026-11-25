from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import HubSession
from app.services.metadata import get_metadata_provider
from app.services.sessions import SessionError, build_session, create_session, decide, delete_session, review_items

router = APIRouter(prefix="/api", tags=["sessions"])


# ---------- browsing BigQuery (metadata only) ----------

@router.get("/bq/projects")
def projects():
    return [p.model_dump() for p in get_metadata_provider().list_projects()]


@router.get("/bq/projects/{project}/datasets")
def datasets(project: str):
    return [d.model_dump() for d in get_metadata_provider().list_datasets(project)]


@router.get("/bq/projects/{project}/datasets/{dataset}/tables")
def tables(project: str, dataset: str):
    return [t.model_dump() for t in get_metadata_provider().list_tables(project, dataset)]


# ---------- sessions ----------

class SessionIn(BaseModel):
    name: str = Field(default="", max_length=200)
    tables: list[str] = Field(min_length=1, max_length=500)


class DecisionIn(BaseModel):
    key: str = Field(min_length=3, max_length=500)
    decision: str  # approved | rejected | reset
    changes: dict = Field(default_factory=dict)


def _summary(s: HubSession) -> dict:
    return {"id": s.id, "scope": s.scope, "name": s.name, "tables": s.tables, "status": s.status, "error": s.error,
            "report": s.report or {}, "created_at": s.created_at.isoformat(),
            "built_at": s.built_at.isoformat() if s.built_at else None}


def _get(db: Session, session_id: int) -> HubSession:
    s = db.get(HubSession, session_id)
    if s is None:
        raise HTTPException(404, "That session wasn't found.")
    return s


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    return [_summary(s) for s in db.scalars(select(HubSession).order_by(HubSession.id.desc()))]


@router.post("/sessions")
async def new_session(body: SessionIn, db: Session = Depends(get_db)):
    try:
        s = await run_in_threadpool(create_session, db, body.name, body.tables)
    except SessionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _summary(s)


@router.get("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)):
    return _summary(_get(db, session_id))


@router.post("/sessions/{session_id}/build")
async def rebuild(session_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Check the tables for changes and rebuild if anything changed (or always, with force)."""
    s = _get(db, session_id)
    try:
        s = await run_in_threadpool(lambda: build_session(db, s, force=force))
    except SessionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _summary(s)


@router.delete("/sessions/{session_id}")
def remove(session_id: int, db: Session = Depends(get_db)):
    delete_session(db, _get(db, session_id))
    return {"ok": True}


@router.get("/sessions/{session_id}/review")
def review(session_id: int, db: Session = Depends(get_db)):
    s = _get(db, session_id)
    try:
        return {"session": _summary(s), "items": review_items(s)}
    except SessionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/approvals")
async def approve(body: DecisionIn, db: Session = Depends(get_db)):
    """Confirm, correct or reject an inferred item. Applies to every session over the same tables."""
    try:
        rebuilt = await run_in_threadpool(decide, db, body.key, body.decision, body.changes)
    except SessionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "rebuilt": [s.id for s in rebuilt]}
