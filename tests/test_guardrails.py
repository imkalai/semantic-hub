import pytest

from app.config import get_settings
from app.schemas.plan import PlanFilter, QueryPlan, TimeRange
from app.services.guardrails import apply_guardrails
from app.services.ontology import bootstrap, get_ontology
from app.services.planning import plan_question, resolve
from app.services.retrieval import build_index

LAST_3 = TimeRange(kind="relative", unit="month", count=3)


@pytest.fixture
def ready(db):
    bootstrap(db)
    build_index(db)
    return db


def _check(report, rule):
    """The check for a rule, preferring one that didn't simply pass."""
    found = [c for c in report.checks if c.rule == rule]
    return next((c for c in found if c.status != "pass"), found[0])


def _guard(db, plan: QueryPlan):
    onto = get_ontology(db)
    resolved, errors = resolve(onto, plan)
    assert resolved, errors
    return apply_guardrails(db, onto, resolved)


def test_join_direction_marks_fan_out(ready):
    onto = get_ontology(ready)
    assert onto.join_path("orders", "order_items")[0]["fans_out"] is True  # one order, many lines
    assert onto.join_path("order_items", "orders")[0]["fans_out"] is False


def test_large_partitioned_table_is_limited_to_a_period(ready):
    o = plan_question(ready, "AOV by channel")
    assert o.status == "ready"
    assert (o.plan.time_dimension, o.plan.time_range.count) == ("order_date", 12)
    assert _check(o.guardrails, "large_table_period").status == "adjusted"
    assert "(by order date)" in o.resolved.summary


def test_a_named_period_is_kept(ready):
    resolved, report = _guard(ready, QueryPlan(metrics=["revenue"], time_dimension="order_date", time_range=LAST_3))
    assert resolved.plan.time_range.count == 3
    assert _check(report, "large_table_period").status == "pass"


def test_large_table_without_partition_date_warns(ready):
    o = plan_question(ready, "units sold by territory")
    check = _check(o.guardrails, "large_table_period")
    assert o.status == "ready" and check.status == "warn" and "310 GB" in check.message


def test_double_counting_is_blocked_with_alternatives(ready):
    o = plan_question(ready, "revenue by product category last 3 months")
    check = _check(o.guardrails, "no_double_counting")
    assert o.status == "blocked" and check.status == "block"
    assert "Product revenue" in check.suggestions
    assert "counted more than once" in o.errors[0]


def test_distinct_counts_are_safe_across_fan_out(ready):
    o = plan_question(ready, "buying customers by product category last 3 months")
    assert o.status == "ready"
    assert _check(o.guardrails, "no_double_counting").status == "pass"


def test_ratio_parts_are_each_checked(ready):
    resolved, report = _guard(ready, QueryPlan(metrics=["return_rate"], dimensions=["customer_region"],
                                               time_dimension="order_date", time_range=LAST_3))
    assert {(p.metric, p.table) for p in resolved.metric_paths} == {("return_count", "returns"), ("order_count", "orders")}
    assert report.status == "pass"


def test_personal_data_is_blocked_unless_allowed(ready, monkeypatch):
    plan = QueryPlan(metrics=["buying_customers"], time_dimension="order_date", time_range=LAST_3,
                     filters=[PlanFilter(dimension="customer_email", values=["a@example.com"])])
    _, report = _guard(ready, plan)
    assert report.status == "block" and _check(report, "personal_data").status == "block"
    monkeypatch.setenv("SH_ALLOW_PII", "true")
    get_settings.cache_clear()
    _, report = _guard(ready, plan)
    assert _check(report, "personal_data").status == "warn" and report.status == "warn"


def test_filter_values_take_the_spelling_from_the_data(ready):
    plan = QueryPlan(metrics=["revenue"], time_dimension="order_date", time_range=LAST_3,
                     filters=[PlanFilter(dimension="customer_region", values=["north"])])
    resolved, report = _guard(ready, plan)
    assert resolved.plan.filters[0].values == ["North"]
    assert _check(report, "filter_values").status == "adjusted"
    plan.filters[0].values = ["Atlantis"]
    _, report = _guard(ready, plan)
    check = _check(report, "filter_values")
    assert check.status == "warn" and "Atlantis" in check.message


def test_wide_group_by_warns(ready, monkeypatch):
    monkeypatch.setenv("SH_MAX_GROUP_BY", "1")
    get_settings.cache_clear()
    _, report = _guard(ready, QueryPlan(metrics=["revenue"], dimensions=["customer_region", "sales_channel"],
                                        time_dimension="order_date", time_range=LAST_3))
    assert _check(report, "row_limits").status == "warn"


def test_blocked_plans_are_saved_with_status():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/questions", json={"question": "revenue by product category"},
                        headers={"X-Requested-With": "semantic-hub"}).json()
        assert r["status"] == "blocked"
        assert any(c["status"] == "block" for c in r["guardrails"]["checks"])
        assert client.get("/api/questions").json()[0]["status"] == "blocked"
