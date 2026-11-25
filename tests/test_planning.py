import json

import pytest

from app.schemas.plan import PlanFilter, QueryPlan, TimeRange
from app.services.cortex.base import CortexClient
from app.services.ontology import bootstrap, get_ontology
from app.services.planning import parse_plan, plan_question, resolve
from app.services.retrieval import build_index


@pytest.fixture
def ready(db):
    bootstrap(db)
    build_index(db)
    return db


def _plan(db, q):
    o = plan_question(db, q)
    return o, o.plan


def test_trend_question_with_value_filter(ready):
    o, p = _plan(ready, "Monthly turnover by region for web orders")
    assert o.status == "ready" and len(o.attempts) == 1
    assert p.metrics == ["revenue"] and p.dimensions == ["customer_region"]
    assert (p.time_dimension, p.time_grain) == ("order_date", "month")
    assert p.time_range.count == 12 and not p.time_range.include_current
    assert [(f.dimension, f.operator, f.values) for f in p.filters] == [("sales_channel", "equals", ["web"])]
    assert o.resolved.base_table == "orders" and [j.name for j in o.resolved.joins] == ["orders_to_customers"]
    assert o.resolved.summary.startswith("Revenue by Customer region, per month of order date, over the last 12 months")
    assert any("last 12" in a for a in p.assumptions)


def test_how_many_uses_the_tables_count_metric(ready):
    o, p = _plan(ready, "How many cancellations by churn reason")
    assert o.status == "ready"
    assert p.metrics == ["churned_accounts"] and p.dimensions == ["churn_reason"]


def test_negation_top_n_and_relative_ranges(ready):
    _, p = _plan(ready, "revenue excluding web orders last 3 months")
    assert p.filters[0].operator == "not_equals"
    assert (p.time_range.unit, p.time_range.count) == ("month", 3)
    _, p = _plan(ready, "Top 5 product categories by product revenue this year")
    assert p.limit == 5 and p.order_by[0].field == "product_revenue" and p.order_by[0].direction == "desc"
    assert p.time_dimension == "order_date" and p.time_range.include_current  # the date comes through the join


def test_join_path_spans_several_tables(ready):
    o, _ = _plan(ready, "units sold by territory")
    assert [j.name for j in o.resolved.joins] == ["order_items_to_orders", "orders_to_customers", "customers_to_regions"]


def test_personal_data_dimensions_are_left_out(ready):
    o, p = _plan(ready, "revenue by customer email")
    assert o.status == "ready" and p.dimensions == []
    assert any("personal data" in a for a in p.assumptions)


def test_unknown_subject_asks_for_clarification(ready):
    o = plan_question(ready, "stock in the warehouse")
    assert o.status == "clarify" and "measure" in o.clarification


def test_unjoinable_concepts_are_rejected_after_a_retry(ready):
    o = plan_question(ready, "churn by order channel")
    assert o.status == "invalid" and len(o.attempts) == 2
    assert "no approved join" in o.errors[0]


class ScriptedCortex(CortexClient):
    """Replies with a fixed sequence of answers and records what it was sent."""
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def chat(self, messages, **_):
        self.sent.append(messages[-1]["content"])
        return self.replies.pop(0)

    def embed(self, texts):
        from app.services.cortex.mock import hashed_embedding
        return [hashed_embedding(t, 256) for t in texts]


def test_rejected_plan_is_retried_with_the_reasons(ready, monkeypatch):
    bad = json.dumps({"metrics": ["profit"], "dimensions": ["customer_region"]})
    good = "Here you go:\n```json\n" + json.dumps({"metrics": ["revenue"], "dimensions": ["customer_region"]}) + "\n```"
    fake = ScriptedCortex([bad, good])
    monkeypatch.setattr("app.services.planning.get_cortex", lambda: fake)
    o = plan_question(ready, "profit by region")
    assert o.status == "ready" and len(o.attempts) == 2
    assert "'profit' isn't a metric" in fake.sent[1]  # the second request carries the errors
    assert o.attempts[0].errors and not o.attempts[1].errors


def test_unparseable_replies_end_invalid(ready, monkeypatch):
    monkeypatch.setattr("app.services.planning.get_cortex", lambda: ScriptedCortex(["no idea", "still no idea"]))
    o = plan_question(ready, "revenue")
    assert o.status == "invalid" and "JSON" in o.errors[0]


@pytest.mark.parametrize("plan, message", [
    (QueryPlan(metrics=[]), "no metric"),
    (QueryPlan(metrics=["revenue"], dimensions=["order_date"]), "time dimension: use time_dimension"),
    (QueryPlan(metrics=["revenue"], time_dimension="churn_date", time_grain="week"), "not week"),
    (QueryPlan(metrics=["revenue"], time_grain="month"), "without a time_dimension"),
    (QueryPlan(metrics=["revenue"], filters=[PlanFilter(dimension="sales_channel", operator="equals", values=[])]), "exactly one value"),
    (QueryPlan(metrics=["revenue"], filters=[PlanFilter(dimension="sales_channel", operator="between", values=["a"])]), "two values"),
    (QueryPlan(metrics=["revenue"], time_dimension="order_date", time_range=TimeRange(kind="relative")), "unit and a count"),
    (QueryPlan(metrics=["revenue"], limit=10**6), "above the maximum"),
    (QueryPlan(metrics=["revenue", "revenue"]), "listed twice"),
])
def test_plan_checks(ready, plan, message):
    resolved, errors = resolve(get_ontology(ready), plan)
    assert resolved is None and any(message in e for e in errors), errors


def test_parse_plan_fills_defaults_and_reports_bad_fields():
    plan, errors = parse_plan('{"metrics": ["revenue"], "limit": null}')
    assert plan and plan.limit == 500 and not errors
    plan, errors = parse_plan('{"metrics": "revenue", "time_grain": "fortnight"}')
    assert plan is None and len(errors) == 2


def test_questions_api_saves_history():
    from fastapi.testclient import TestClient

    from app.main import app

    h = {"X-Requested-With": "semantic-hub"}
    with TestClient(app) as client:
        r = client.post("/api/questions", json={"question": "AOV by channel"}, headers=h).json()
        assert r["status"] == "ready" and r["resolved"]["summary"].startswith("Average order value by Sales channel")
        history = client.get("/api/questions").json()
        assert history[0]["id"] == r["id"] and history[0]["status"] == "ready"
        again = client.get(f"/api/questions/{r['id']}").json()
        assert again["plan"] == r["plan"]
        assert client.get("/api/questions/999").status_code == 404
