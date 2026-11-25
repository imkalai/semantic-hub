import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import get_engine, init_db, new_session, reset_engine
from app.models import Approval, OntologyNode
from app.schemas.catalog import SemanticCatalog
from app.services.ontology import get_ontology
from app.services.planning import plan_question
from app.services.retrieval import index_status, retrieve
from app.services.sessions import SessionError, build_session, create_session, decide, delete_session, review_items

S = "acme-analytics.sales"
CRM = "acme-analytics.crm"
OPS = "acme-ops.warehouse"
H = {"X-Requested-With": "semantic-hub"}


def _cat(s) -> SemanticCatalog:
    return SemanticCatalog.model_validate(s.catalog)


def _by_key(items, key):
    return next(i for i in items if i.key == key)


def test_uncatalogued_tables_get_declared_and_inferred_items(db):
    s = create_session(db, "Ops", [f"{OPS}.stock_levels", f"{OPS}.warehouses"])
    c = _cat(s)
    assert s.status == "ready"
    assert {t.name for t in c.tables} == {"stock_levels", "warehouses"}
    assert all(t.trust == "declared" for t in c.tables)
    join = c.joins[0]
    assert (join.left, join.right, join.relationship, join.trust) == ("stock_levels", "warehouses", "many_to_one", "inferred")
    units = _by_key(c.metrics, f"metric:{OPS}.stock_levels:units_on_hand:sum")
    assert units.label == "Total stock level units on hand" and units.trust == "inferred"
    assert {m.name for m in c.metrics} >= {"stock_levels_count", "warehouses_count"}
    city = _by_key(c.dimensions, f"dimension:{OPS}.warehouses:city")
    assert city.label == "Warehouse city" and "city" in city.synonyms
    # keys aren't dimensions or measures
    assert not any(d.column in ("warehouse_id", "stock_level_id") for d in c.dimensions)


def test_curated_items_are_reused_and_keys_declared_in_bigquery(db):
    s = create_session(db, "Sales", [f"{S}.orders", f"{S}.customers", f"{S}.order_items"])
    c = _cat(s)
    revenue = next(m for m in c.metrics if m.name == "revenue")
    assert revenue.trust == "certified" and revenue.source == "catalog"
    # orders.amount is covered by Revenue, so no inferred duplicate
    assert not any(m.column == "amount" and m.trust == "inferred" for m in c.metrics)
    # curated joins between selected tables are kept; AOV needs only orders, so it's in
    assert {j.name for j in c.joins} == {"order_items_to_orders", "orders_to_customers"}
    assert any(m.name == "average_order_value" for m in c.metrics)
    # return_rate needs the returns table, which isn't in the session
    assert not any(m.name == "return_rate" for m in c.metrics)


def test_declared_foreign_key_without_curated_catalog(db, catalog_data, write_catalog):
    write_catalog({**catalog_data, "tables": [], "metrics": [], "dimensions": [], "joins": [], "synonyms": []})
    s = create_session(db, "Bare", [f"{S}.orders", f"{S}.customers"])
    c = _cat(s)
    join = c.joins[0]
    assert (join.left, join.right, join.trust, join.source) == ("orders", "customers", "declared", "bigquery")
    orders = next(t for t in c.tables if t.name == "orders")
    assert orders.primary_key == "order_id" and orders.time_column == "created_at"


def test_inferred_join_blocks_until_confirmed_then_applies_everywhere(db):
    s1 = create_session(db, "CRM", [f"{CRM}.accounts", f"{CRM}.churn_events", f"{S}.regions"])
    q = "churned accounts by territory last 6 months"
    o = plan_question(db, q, s1.scope)
    check = next(ch for ch in o.guardrails.checks if ch.rule == "confirmed_joins")
    assert o.status == "blocked" and check.status == "block"
    key = check.actions[0]["key"]
    assert key == f"join:{CRM}.accounts:{S}.regions:region=region"

    rebuilt = decide(db, key, "approved")
    assert [x.id for x in rebuilt] == [s1.id]
    o = plan_question(db, q, s1.scope)
    assert o.status == "ready"
    # a later session over the same tables starts with the join confirmed
    s2 = create_session(db, "CRM again", [f"{CRM}.accounts", f"{S}.regions"])
    assert _by_key(_cat(s2).joins, key).trust == "certified"


def test_edits_and_rejections_are_applied(db):
    s = create_session(db, "Ops", [f"{OPS}.stock_levels", f"{OPS}.warehouses"])
    units = f"metric:{OPS}.stock_levels:units_on_hand:sum"
    decide(db, units, "approved", {"label": "Stock on hand", "synonyms": ["inventory", ""], "agg": "max",
                                   "column": "ignored"})
    city = f"dimension:{OPS}.warehouses:city"
    decide(db, city, "rejected")
    db.refresh(s)
    c = _cat(s)
    m = _by_key(c.metrics, units)
    assert (m.label, m.synonyms, m.agg, m.column, m.trust, m.source) == (
        "Stock on hand", ["inventory"], "max", "units_on_hand", "certified", "approved")
    assert not any(d.key == city for d in c.dimensions)
    assert get_ontology(db, s.scope).resolve_term("inventory") == [f"metric:{m.name}"]
    assert not any(i["key"] == units for i in review_items(s))
    decide(db, city, "reset")
    db.refresh(s)
    assert any(d.key == city for d in _cat(s).dimensions)


def test_a_confirmed_primary_key_enables_joins(db):
    s = create_session(db, "Web", ["acme-mkt.web_events.sessions", f"{S}.regions"])
    assert next(t for t in _cat(s).tables if t.name == "sessions").primary_key == "session_id"
    with pytest.raises(SessionError):
        decide(db, f"table:{S}.regions", "rejected")


def test_unchanged_sessions_skip_the_build(db):
    s = create_session(db, "Ops", [f"{OPS}.warehouses"])
    built = s.built_at
    assert build_session(db, s).built_at == built
    assert build_session(db, s, force=True).built_at >= built


def test_sessions_are_isolated_from_each_other_and_the_catalog(db):
    from app.services.ontology import bootstrap
    from app.services.retrieval import build_index

    bootstrap(db)
    build_index(db)
    s = create_session(db, "Ops", [f"{OPS}.stock_levels", f"{OPS}.warehouses"])
    assert retrieve(db, "turnover", scope=s.scope).candidates == []
    assert retrieve(db, "turnover").candidates[0].id == "metric:revenue"
    assert index_status(db, s.scope)["up_to_date"] and index_status(db)["up_to_date"]
    assert get_ontology(db, s.scope).node("metric:revenue") is None

    delete_session(db, s)
    assert not db.scalars(select(OntologyNode).where(OntologyNode.scope == s.scope)).first()
    assert get_ontology(db).node("metric:revenue")  # the curated catalog is untouched


def test_bad_sessions_are_refused(db):
    with pytest.raises(SessionError, match="at least one"):
        create_session(db, "x", [])
    with pytest.raises(SessionError, match="project.dataset.table"):
        create_session(db, "x", ["orders"])
    with pytest.raises(SessionError, match="wasn't found|weren't found"):
        create_session(db, "x", [f"{S}.nope"])


def test_old_unscoped_database_is_upgraded(tmp_path):
    init_db()
    with get_engine().begin() as conn:  # make it look like the first release's graph tables
        conn.execute(text("DROP TABLE ontology_edges"))
        conn.execute(text("DROP TABLE ontology_nodes"))
        conn.execute(text("CREATE TABLE ontology_nodes (id VARCHAR PRIMARY KEY, kind VARCHAR)"))
        conn.execute(text("DROP INDEX ix_questions_scope"))
        conn.execute(text("ALTER TABLE questions DROP COLUMN scope"))
    reset_engine()
    init_db()
    with new_session() as db:
        cols = {r[1] for r in db.execute(text("PRAGMA table_info(ontology_nodes)"))}
        assert "scope" in cols
        assert "scope" in {r[1] for r in db.execute(text("PRAGMA table_info(questions)"))}


def test_session_api_flow():
    from app.main import app

    with TestClient(app) as client:
        assert any(p["id"] == "acme-ops" for p in client.get("/api/bq/projects").json())
        assert client.get("/api/bq/projects/acme-ops/datasets/warehouse/tables").status_code == 200
        r = client.post("/api/sessions", json={"name": "Ops", "tables": [f"{OPS}.stock_levels", f"{OPS}.warehouses"]},
                        headers=H)
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["status"] == "ready" and s["report"]["to_review"] > 0
        assert client.post("/api/sessions", json={"tables": ["bad"]}, headers=H).status_code == 400

        review = client.get(f"/api/sessions/{s['id']}/review").json()["items"]
        join = next(i for i in review if i["kind"] == "join")
        q = client.post("/api/questions", json={"question": "Total stock level units on hand by warehouse city",
                                                "scope": s["scope"]}, headers=H).json()
        assert q["status"] == "blocked" and q["scope"] == s["scope"]
        assert client.post("/api/approvals", json={"key": join["key"], "decision": "approved"},
                           headers=H).json()["rebuilt"] == [s["id"]]
        q = client.post("/api/questions", json={"question": "Total stock level units on hand by warehouse city",
                                                "scope": s["scope"]}, headers=H).json()
        assert q["status"] == "ready", q
        assert client.get(f"/api/questions?scope={s['scope']}").json()[0]["id"] == q["id"]
        assert client.get("/api/questions?scope=catalog").json() == []
        cat = client.get(f"/api/catalog?scope={s['scope']}").json()
        assert cat["joins"][0]["trust"] == "certified"
        assert client.get("/api/catalog?scope=session:999").status_code == 503
        assert client.get("/api/catalog?scope=evil").status_code == 422
        assert client.delete(f"/api/sessions/{s['id']}", headers=H).json() == {"ok": True}
        with new_session() as db:
            assert db.get(Approval, join["key"]) is not None  # decisions outlive the session
