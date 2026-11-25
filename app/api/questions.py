from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.catalog import SCOPE
from app.api.retrieval import SCOPE_FIELD
from app.db import get_db
from app.models import QuestionRecord
from app.services.planning import plan_question

router = APIRouter(prefix="/api/questions", tags=["questions"])


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    scope: str = SCOPE_FIELD


def _summary(row: QuestionRecord) -> dict:
    return {"id": row.id, "scope": row.scope, "question": row.question, "status": row.status, "summary": row.summary,
            "created_at": row.created_at.isoformat()}


@router.post("")
async def ask(body: AskIn, db: Session = Depends(get_db)):
    """Plan a question in a scope: retrieval, Cortex planning, ontology resolution, guardrails. Saved to history."""
    outcome = await run_in_threadpool(plan_question, db, body.question.strip(), body.scope)
    summary = (outcome.resolved.summary if outcome.resolved else outcome.clarification
               or "; ".join(outcome.errors))
    row = QuestionRecord(scope=body.scope, question=outcome.question, status=outcome.status, summary=summary,
                         outcome=outcome.model_dump(mode="json"))
    db.add(row)
    db.commit()
    return {"id": row.id, **outcome.model_dump(mode="json")}


@router.get("")
def history(limit: int = Query(20, ge=1, le=200), scope: str = SCOPE, db: Session = Depends(get_db)):
    rows = db.scalars(select(QuestionRecord).where(QuestionRecord.scope == scope)
                      .order_by(QuestionRecord.id.desc()).limit(limit))
    return [_summary(r) for r in rows]


@router.get("/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)):
    row = db.get(QuestionRecord, question_id)
    if row is None:
        raise HTTPException(404, "That question wasn't found.")
    return {"id": row.id, "created_at": row.created_at.isoformat(), **row.outcome}
