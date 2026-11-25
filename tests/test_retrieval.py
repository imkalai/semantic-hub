import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from app.models import IndexDocument, MetadataTable
from app.services.health import run_healthcheck
from app.services.ontology import bootstrap
from app.services.retrieval import VectorIndex, build_index, index_status, retrieve


@pytest.fixture
def indexed(db):
    bootstrap(db)
    build_index(db)
    return db


def _ids(result):
    return [c.id for c in result.candidates]


def test_index_embeds_once_then_only_changes(indexed, catalog_data, write_catalog):
    db = indexed
    st = index_status(db)
    assert st["up_to_date"] and st["documents_indexed"] == st["documents_expected"] > 30
    assert st["tables_with_metadata"] == 8
    again = build_index(db)
    assert again.embedded == 0 and again.unchanged == again.documents

    catalog_data["metrics"][0]["description"] = "All money taken from orders"
    catalog_data["metrics"] = [m for m in catalog_data["metrics"] if m["name"] != "units_sold"]
    write_catalog(catalog_data)
    bootstrap(db)
    assert index_status(db)["changed"] == ["metric:revenue"]
    r = build_index(db)
    # Revenue changed; Units sold is gone, so its column becomes a raw (not governed) column document.
    assert r.embedded == 2 and r.removed == 1
    ids = VectorIndex().ids()
    assert "metric:units_sold" not in ids and "column:order_items.quantity" in ids
    assert index_status(db)["up_to_date"]


def test_index_rebuilds_when_embedding_model_changes(indexed, monkeypatch):
    monkeypatch.setenv("SH_EMBEDDING_DIM", "128")
    get_settings.cache_clear()
    assert index_status(indexed)["up_to_date"] is False
    r = build_index(indexed)
    assert r.rebuilt and r.embedded == r.documents
    assert VectorIndex().signature() == "mock:hashed:128"


def test_index_repairs_lost_vectors(indexed):
    VectorIndex().reset()
    assert index_status(indexed)["missing"]
    r = build_index(indexed, refresh=False)
    assert r.embedded == r.documents


def test_keys_and_personal_data_values_stay_out_of_documents(indexed):
    ids = {r.id for r in indexed.scalars(select(IndexDocument))}
    assert "column:orders.order_id" not in ids  # primary key
    assert "column:orders.customer_id" not in ids  # join key
    assert "column:orders.amount" not in ids  # covered by the Revenue metric
    assert "column:customers.first_name" in ids  # raw column, not in the catalog
    docs = dict(zip(*[VectorIndex().collection().get(include=["documents"])[k] for k in ("ids", "documents")]))
    assert "Example values" not in docs["column:customers.first_name"]


def test_exact_terms_win_and_longest_phrase_wins(indexed):
    r = retrieve(indexed, "Number of orders by customer region")
    assert _ids(r)[:2] == ["metric:order_count", "dimension:customer_region"]
    assert "table:orders" not in _ids(r)[:2]  # "orders" is part of "number of orders"
    assert all("matches" in c.reasons[0] for c in r.candidates[:2])
    assert r.mode == "vector+exact" and r.unmatched == []


def test_plural_terms_and_values(indexed):
    r = retrieve(indexed, "refund reasons for web orders in the North")
    assert "dimension:return_reason" in _ids(r)
    assert {(v.dimension, v.value) for v in r.values} == {("dimension:sales_channel", "web"),
                                                          ("dimension:customer_region", "North")}


def test_meaning_finds_concepts_without_exact_words(indexed):
    r = retrieve(indexed, "monthly fee totals")
    top = r.candidates[0]
    assert top.id == "metric:monthly_recurring_revenue" and "similar meaning" in top.reasons[0]


def test_unknown_subjects_return_nothing(indexed):
    r = retrieve(indexed, "stock in the warehouse")
    assert r.candidates == [] and r.unmatched == ["stock", "warehouse"]


def test_falls_back_to_exact_matching_without_vectors(indexed, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("Cortex is down")

    monkeypatch.setattr("app.services.retrieval.retriever.get_cortex", lambda: type("C", (), {"embed": broken})())
    r = retrieve(indexed, "turnover by region")
    assert r.mode == "exact_only" and "Cortex is down" in r.note
    assert _ids(r)[:2] == ["metric:revenue", "dimension:customer_region"]


def test_healthcheck_flags_an_empty_index(db):
    bootstrap(db)
    report = run_healthcheck(db)
    check = next(c for c in report["checks"] if c["name"] == "Metadata index")
    assert check["status"] == "fail" and report["verdict"] == "Rebuild the metadata index"
    build_index(db)
    assert run_healthcheck(db)["ready"]


def test_healthcheck_warns_when_index_is_stale(indexed):
    indexed.execute(delete(IndexDocument).where(IndexDocument.id == "metric:revenue"))
    indexed.commit()
    check = next(c for c in run_healthcheck(indexed)["checks"] if c["name"] == "Metadata index")
    assert check["status"] == "warn" and "1 missing" in check["detail"]


def test_metadata_snapshot_is_saved(indexed):
    names = {r.full_name for r in indexed.scalars(select(MetadataTable))}
    assert "acme-analytics.sales.orders" in names and len(names) == 8
