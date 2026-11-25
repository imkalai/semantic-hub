import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.models import OntologyNode
from app.services.cortex import get_cortex
from app.services.cortex.mock import hashed_embedding
from app.services.health import run_healthcheck
from app.services.ontology import bootstrap
from app.services.retrieval import build_index

H = {"X-Requested-With": "semantic-hub"}


def _by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_healthcheck_ready_after_bootstrap_and_index(db):
    bootstrap(db)
    build_index(db)
    report = run_healthcheck(db)
    assert report["ready"], [c for c in report["checks"] if c["status"] == "fail"]
    assert report["verdict"] == "Ready for runtime"
    checks = _by_name(report)
    assert checks["Ontology completeness vs catalog"]["status"] == "ok"
    assert checks["Chroma vector index"]["status"] == "ok"
    assert checks["Cortex (mock)"]["status"] == "warn"  # works, but it's the stand-in


def test_healthcheck_asks_for_bootstrap_when_graph_incomplete(db):
    bootstrap(db)
    db.execute(delete(OntologyNode).where(OntologyNode.kind == "synonym"))
    db.commit()
    report = run_healthcheck(db)
    assert not report["ready"]
    assert report["verdict"] == "Repair graph or rerun bootstrap"
    check = _by_name(report)["Ontology completeness vs catalog"]
    assert check["status"] == "fail" and "missing concept" in check["detail"]


def test_healthcheck_catches_catalog_columns_missing_in_bigquery(db, catalog_data, write_catalog):
    catalog_data["dimensions"][1]["column"] = "no_such_column"
    write_catalog(catalog_data)
    bootstrap(db)
    check = _by_name(run_healthcheck(db))["BigQuery metadata (demo)"]
    assert check["status"] == "fail" and "no_such_column" in check["detail"]


def test_mock_embeddings_are_deterministic_and_normalised():
    a, b = get_cortex().embed(["monthly revenue by region", "monthly revenue by region"])
    assert a == b and len(a) == 256
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    near = hashed_embedding("revenue per region", 256)
    far = hashed_embedding("warehouse stock levels", 256)
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    assert dot(a, near) > dot(a, far)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:  # runs startup, including the first-run bootstrap
        yield c


def test_first_start_bootstraps_and_serves_the_catalog(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["source"] == "graph_store"
    assert {m["id"] for m in body["metrics"]} >= {"metric:revenue", "metric:return_rate"}
    assert any(j["name"] == "orders_to_customers" for j in body["joins"])


def test_resolve_and_join_path_endpoints(client):
    r = client.get("/api/catalog/resolve", params={"term": "MRR"}).json()
    assert r["matches"][0]["id"] == "metric:monthly_recurring_revenue"
    assert r["matches"][0]["table"] == "accounts"
    assert client.get("/api/catalog/join-path", params={"from": "returns", "to": "customers"}).status_code == 200
    assert client.get("/api/catalog/join-path", params={"from": "returns", "to": "accounts"}).status_code == 404


def test_health_endpoints(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/health/full").json()["ready"] is True


def test_retrieval_endpoints(client):
    assert client.get("/api/retrieval/status").json()["up_to_date"] is True  # indexed on first start
    assert client.post("/api/retrieval/search", json={"question": "aov"}).status_code == 403
    r = client.post("/api/retrieval/search", json={"question": "AOV by channel"}, headers=H).json()
    assert [c["id"] for c in r["candidates"][:2]] == ["metric:average_order_value", "dimension:sales_channel"]
    assert client.post("/api/retrieval/search", json={"question": ""}, headers=H).status_code == 422
    r = client.post("/api/retrieval/index", headers=H).json()
    assert r["embedded"] == 0 and r["documents"] > 30


def test_bootstrap_endpoint_needs_app_header(client):
    assert client.post("/api/bootstrap").status_code == 403
    r = client.post("/api/bootstrap", headers=H)
    assert r.status_code == 200 and r.json()["steps"][0]["name"] == "Create constraints"


def test_other_host_names_are_refused(client):
    assert client.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_ui_is_served_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "Semantic Hub" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
