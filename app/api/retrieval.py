from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.catalog import SCOPE
from app.db import get_db
from app.services.retrieval import RetrievalResult, build_index, index_status, retrieve

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])
SCOPE_FIELD = Field(default="catalog", pattern=r"^(catalog|session:\d{1,9})$")


class SearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=8, ge=1, le=30)
    scope: str = SCOPE_FIELD


@router.post("/search", response_model=RetrievalResult)
async def search(body: SearchIn, db: Session = Depends(get_db)):
    return await run_in_threadpool(retrieve, db, body.question, body.k, body.scope)


@router.get("/status")
async def status(scope: str = SCOPE, db: Session = Depends(get_db)):
    return await run_in_threadpool(index_status, db, scope)


@router.post("/index")
async def index(rebuild: bool = False, refresh: bool = True, scope: str = SCOPE, db: Session = Depends(get_db)):
    result = await run_in_threadpool(lambda: build_index(db, scope, rebuild=rebuild, refresh=refresh))
    return result.__dict__
