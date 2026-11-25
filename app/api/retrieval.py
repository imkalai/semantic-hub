from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.retrieval import RetrievalResult, build_index, index_status, retrieve

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


class SearchIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=8, ge=1, le=30)


@router.post("/search", response_model=RetrievalResult)
async def search(body: SearchIn, db: Session = Depends(get_db)):
    return await run_in_threadpool(retrieve, db, body.question, body.k)


@router.get("/status")
async def status(db: Session = Depends(get_db)):
    return await run_in_threadpool(index_status, db)


@router.post("/index")
async def index(rebuild: bool = False, refresh: bool = True, db: Session = Depends(get_db)):
    result = await run_in_threadpool(lambda: build_index(db, rebuild=rebuild, refresh=refresh))
    return result.__dict__
