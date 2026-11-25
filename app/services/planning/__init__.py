"""Query planning (diagram: Semantic Layer API -> Metadata Retrieval -> Query Planning (Cortex LLM) ->
Ontology Resolution). Retrieval finds the concepts, Cortex writes a plan in catalog terms, and the plan
is checked and resolved against the ontology. A rejected plan goes back to Cortex once with the reasons."""

import json
import logging
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CATALOG_SCOPE
from app.schemas.plan import QueryPlan, ResolvedPlan
from app.services.cortex import CortexError, get_cortex
from app.services.cortex.prompts import plan_messages
from app.services.ontology import get_ontology
from app.services.planning.context import build_context
from app.services.planning.resolve import check_plan, describe, resolve
from app.services.retrieval import RetrievalResult, retrieve

log = logging.getLogger(__name__)


class PlanAttempt(BaseModel):
    errors: list[str]
    raw: str  # what Cortex replied, kept for troubleshooting
    ms: int


class PlanOutcome(BaseModel):
    question: str
    scope: str = CATALOG_SCOPE
    status: Literal["ready", "clarify", "invalid", "blocked"]
    plan: QueryPlan | None = None
    resolved: ResolvedPlan | None = None
    clarification: str | None = None
    errors: list[str] = []
    attempts: list[PlanAttempt] = []
    retrieval: RetrievalResult
    cortex: str
    labels: dict[str, str] = {}  # "metric:revenue" -> "Revenue", for every concept the plan names
    guardrails: Any = None  # GuardrailReport, once the plan resolved


_JSON = re.compile(r"\{.*\}", re.S)


def parse_plan(text: str) -> tuple[QueryPlan | None, list[str]]:
    """The plan JSON from an LLM reply, tolerating code fences or words around it."""
    match = _JSON.search(text or "")
    if not match:
        return None, ["The reply didn't contain a JSON object. Reply with the plan JSON only."]
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return None, [f"The reply wasn't valid JSON ({exc.msg}). Reply with the plan JSON only."]
    if isinstance(data, dict):
        data.setdefault("limit", get_settings().default_limit)
        if data.get("limit") is None:
            data["limit"] = get_settings().default_limit
    try:
        return QueryPlan.model_validate(data), []
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8]]


def plan_question(db: Session, question: str, scope: str = CATALOG_SCOPE) -> PlanOutcome:
    settings = get_settings()
    onto = get_ontology(db, scope)
    retrieval = retrieve(db, question, scope=scope)
    cortex = get_cortex()
    attempts: list[PlanAttempt] = []
    errors: list[str] = []
    plan: QueryPlan | None = None

    for _ in range(max(1, settings.plan_attempts)):
        start = time.perf_counter()
        raw = cortex.chat(plan_messages(build_context(onto, retrieval, errors)), temperature=0.0, max_tokens=1500)
        plan, errors = parse_plan(raw)
        if plan is not None:
            if plan.needs_clarification:
                errors = []
            else:
                resolved, errors = resolve(onto, plan)
        attempts.append(PlanAttempt(errors=errors, raw=raw[:4000], ms=int((time.perf_counter() - start) * 1000)))
        if not errors:
            break
        log.info("Plan rejected, %s: %s", "retrying" if len(attempts) < settings.plan_attempts else "giving up", errors)

    report = None
    if plan is not None and not errors and not plan.needs_clarification:
        from app.services.guardrails import apply_guardrails  # (imports planning.resolve)

        resolved, report = apply_guardrails(db, onto, resolved)
        plan = resolved.plan

    labels: dict[str, str] = {}
    if plan is not None:
        refs = [("metric", m) for m in plan.metrics] + [("dimension", d) for d in plan.dimensions]
        refs += [("dimension", plan.time_dimension)] if plan.time_dimension else []
        refs += [("dimension", f.dimension) for f in plan.filters]
        refs += [(k, o.field) for o in plan.order_by for k in ("metric", "dimension")]
        for kind, name in refs:
            node = onto.node(f"{kind}:{name}")
            if node:
                labels[f"{kind}:{name}"] = node["label"]
    common = {"question": question, "scope": scope, "retrieval": retrieval, "attempts": attempts, "cortex": cortex.name,
              "labels": labels, "guardrails": report}
    if plan is not None and plan.needs_clarification and not errors:
        return PlanOutcome(status="clarify", plan=plan, clarification=plan.needs_clarification, **common)
    if errors or plan is None:
        return PlanOutcome(status="invalid", plan=plan, errors=errors, **common)
    if report is not None and report.blocked:
        return PlanOutcome(status="blocked", plan=plan, resolved=resolved,
                           errors=[ch.message for ch in report.checks if ch.status == "block"], **common)
    return PlanOutcome(status="ready", plan=plan, resolved=resolved, **common)


__all__ = ["PlanOutcome", "PlanAttempt", "plan_question", "parse_plan", "check_plan", "resolve", "describe",
           "CortexError"]
