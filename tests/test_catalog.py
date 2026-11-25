import pytest

from app.schemas.catalog import SemanticCatalog, normalize_term
from app.services.catalog import CatalogError, graph_spec, load_catalog, validate_catalog


def test_demo_catalog_loads_and_is_consistent():
    loaded = load_catalog()
    assert loaded.catalog.metrics and loaded.catalog.dimensions and loaded.catalog.joins
    assert validate_catalog(loaded.catalog) == []
    assert len(loaded.sha256) == 64


def test_graph_spec_links_every_concept_to_its_table():
    spec = graph_spec(load_catalog().catalog)
    edges = spec.edge_keys()
    assert ("metric:revenue", "table:orders", "MEASURES") in edges
    assert ("dimension:customer_region", "table:customers", "ATTRIBUTE_OF") in edges
    assert ("table:orders", "table:customers", "JOINS") in edges
    assert ("synonym:turnover", "metric:revenue", "SYNONYM_OF") in edges
    assert ("metric:average_order_value", "metric:revenue", "DERIVED_FROM") in edges


def test_normalize_term():
    assert normalize_term("  Total   SALES! ") == "total sales"


@pytest.mark.parametrize("change, message", [
    (lambda d: d["metrics"][0].update(table="nope"), "unknown table 'nope'"),
    (lambda d: d["metrics"].append(dict(d["metrics"][0])), "defined twice"),
    (lambda d: d["joins"][0].update(right="ghost"), "join 'order_items_to_orders' uses unknown table 'ghost'"),
    (lambda d: d["dimensions"][1]["synonyms"].append("turnover"), "synonym 'turnover' is used for both"),
    (lambda d: d["synonyms"].append({"term": "x", "concept": "metric:ghost"}), "unknown concept 'metric:ghost'"),
    (lambda d: d["metrics"][2].update(denominator="ghost"), "needs a denominator"),
    (lambda d: d["dimensions"][0].update(time_grains=[]), "needs at least one time grain"),
])
def test_inconsistent_catalogs_are_rejected(catalog_data, write_catalog, change, message):
    change(catalog_data)
    write_catalog(catalog_data)
    with pytest.raises(CatalogError, match=message):
        load_catalog()


def test_schema_errors_are_readable(catalog_data, write_catalog):
    catalog_data["tables"][0]["table"] = "only.two"
    write_catalog(catalog_data)
    with pytest.raises(CatalogError, match="project.dataset.table"):
        load_catalog()


def test_bad_names_are_rejected():
    with pytest.raises(ValueError):
        SemanticCatalog.model_validate({"version": "1", "metrics": [{"name": "Bad Name", "table": "t", "agg": "count"}]})
