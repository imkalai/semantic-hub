from sqlalchemy import delete

from app.models import OntologyEdge, OntologyNode
from app.services.catalog import graph_spec, load_catalog
from app.services.ontology import bootstrap, compare, get_ontology, invalidate
from app.services.ontology.store import SqliteGraphStore


def _complete(db) -> bool:
    return compare(graph_spec(load_catalog().catalog), SqliteGraphStore(db).load()).complete


def test_bootstrap_seeds_everything_and_is_idempotent(db):
    steps = bootstrap(db)
    assert [s.name for s in steps] == ["Create constraints", "Seed metrics", "Seed dimensions",
                                       "Seed approved joins", "Seed synonyms"]
    assert _complete(db)
    again = bootstrap(db)
    assert all(s.added == s.updated == s.removed == 0 for s in again[1:])


def test_bootstrap_repairs_a_damaged_graph(db):
    bootstrap(db)
    db.execute(delete(OntologyEdge).where(OntologyEdge.rel == "JOINS"))
    db.execute(delete(OntologyNode).where(OntologyNode.id == "metric:revenue"))
    db.commit()
    assert not _complete(db)
    steps = {s.name: s for s in bootstrap(db)}
    assert steps["Seed approved joins"].added == 6
    assert steps["Seed metrics"].added >= 1
    assert _complete(db)


def test_bootstrap_removes_concepts_dropped_from_catalog(db, catalog_data, write_catalog):
    bootstrap(db)
    catalog_data["metrics"] = [m for m in catalog_data["metrics"] if m["name"] != "units_sold"]
    write_catalog(catalog_data)
    steps = {s.name: s for s in bootstrap(db)}
    assert steps["Seed metrics"].removed >= 1
    assert SqliteGraphStore(db).load().nodes.get("metric:units_sold") is None


def test_ontology_uses_graph_store_when_complete(db):
    bootstrap(db)
    onto = get_ontology(db)
    assert onto.source == "graph_store"
    assert onto.resolve_term("Turnover") == ["metric:revenue"]
    assert onto.resolve_term("customer region") == ["dimension:customer_region"]  # by label
    assert onto.table_of("dimension:product_category") == "table:products"


def test_ontology_falls_back_to_json_when_graph_is_empty(db):
    onto = get_ontology(db)
    assert onto.source == "json_fallback"
    assert "hasn't been bootstrapped" in onto.note
    assert onto.resolve_term("aov") == ["metric:average_order_value"]


def test_ontology_falls_back_when_graph_is_incomplete(db):
    bootstrap(db)
    db.execute(delete(OntologyNode).where(OntologyNode.id == "dimension:territory"))
    db.commit()
    invalidate()
    onto = get_ontology(db)
    assert onto.source == "json_fallback"
    assert "incomplete" in onto.note
    assert onto.node("dimension:territory")  # the fallback still has it


def test_join_paths_follow_approved_joins_only(db):
    bootstrap(db)
    onto = get_ontology(db)
    path = onto.join_path("order_items", "regions")
    assert [p["name"] for p in path] == ["order_items_to_orders", "orders_to_customers", "customers_to_regions"]
    assert onto.join_path("orders", "accounts") is None  # sales and CRM aren't joined
