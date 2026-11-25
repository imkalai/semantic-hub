"""Ontology layer: the embedded graph store, bootstrap from the catalog, completeness checks, and the
JSON catalog fallback (diagram: Ontology Resolution -> Neo4j graph / JSON Catalog Fallback)."""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BootstrapRun
from app.services.catalog import GraphSpec, LoadedCatalog, graph_spec, load_catalog
from app.services.ontology.graph import Ontology
from app.services.ontology.store import SqliteGraphStore

log = logging.getLogger(__name__)


# ---------- completeness: does the graph hold everything the catalog defines? ----------

@dataclass
class Completeness:
    expected_nodes: int
    expected_edges: int
    missing_nodes: list[str] = field(default_factory=list)
    missing_edges: list[str] = field(default_factory=list)
    changed_nodes: list[str] = field(default_factory=list)
    extra_nodes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not (self.missing_nodes or self.missing_edges or self.changed_nodes)

    def as_dict(self) -> dict:
        return {"complete": self.complete, "expected_nodes": self.expected_nodes, "expected_edges": self.expected_edges,
                "missing_nodes": self.missing_nodes, "missing_edges": self.missing_edges,
                "changed_nodes": self.changed_nodes, "extra_nodes": self.extra_nodes}


def compare(expected: GraphSpec, actual: GraphSpec) -> Completeness:
    result = Completeness(expected_nodes=len(expected.nodes), expected_edges=len(expected.edges))
    for nid, n in expected.nodes.items():
        got = actual.nodes.get(nid)
        if got is None:
            result.missing_nodes.append(nid)
        elif (got["label"], got["props"]) != (n["label"], n["props"]):
            result.changed_nodes.append(nid)
    have = actual.edge_keys()
    result.missing_edges = [f"{s} -{r}-> {t}" for s, t, r in sorted(expected.edge_keys() - have)]
    result.extra_nodes = sorted(set(actual.nodes) - set(expected.nodes))
    return result


# ---------- bootstrap ----------

@dataclass
class BootstrapStep:
    name: str
    added: int = 0
    updated: int = 0
    removed: int = 0
    detail: str = ""


def bootstrap(db: Session, loaded: LoadedCatalog | None = None, *, reset: bool = False) -> list[BootstrapStep]:
    """Seed the graph store from the catalog. Safe to rerun: it adds what's missing, updates what changed
    and removes what the catalog no longer lists. `reset` wipes the graph first."""
    loaded = loaded or load_catalog()
    spec = graph_spec(loaded.catalog)
    store = SqliteGraphStore(db)
    run = BootstrapRun(catalog_version=loaded.catalog.version, catalog_hash=loaded.sha256)
    steps: list[BootstrapStep] = []
    try:
        created = store.ensure_schema()
        steps.append(BootstrapStep("Create constraints", added=len(created),
                                   detail=("created" if created else "already in place")
                                   + ": unique node ids, unique edges, edges must point at existing nodes"))
        if reset:
            store.clear()
        # Concept nodes first (edges need both ends), then edges, then synonyms.
        tables = store.sync_nodes(spec, {"table"})
        metrics = store.sync_nodes(spec, {"metric"})
        dims = store.sync_nodes(spec, {"dimension"})
        steps.append(BootstrapStep("Seed metrics", **_sum(metrics, store.sync_edges(spec, {"MEASURES", "DERIVED_FROM"}))))
        steps.append(BootstrapStep("Seed dimensions",
                                   **_sum(_sum(dims, tables), store.sync_edges(spec, {"ATTRIBUTE_OF"})),
                                   detail=f"including {len(loaded.catalog.tables)} tables"))
        steps.append(BootstrapStep("Seed approved joins", **store.sync_edges(spec, {"JOINS"})))
        syn = store.sync_nodes(spec, {"synonym"})
        steps.append(BootstrapStep("Seed synonyms", **_sum(syn, store.sync_edges(spec, {"SYNONYM_OF"}))))
        run.ok = True
        run.summary = {s.name: {"added": s.added, "updated": s.updated, "removed": s.removed} for s in steps}
        db.add(run)
        db.commit()
    except Exception as exc:
        db.rollback()
        run.ok, run.error = False, str(exc)
        db.add(run)
        db.commit()
        raise
    finally:
        invalidate()
    log.info("Bootstrap finished: %s", run.summary)
    return steps


def _sum(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("added", "updated", "removed")}


# ---------- the ontology the rest of the app uses ----------

_cache: Ontology | None = None


def invalidate() -> None:
    global _cache
    _cache = None


def get_ontology(db: Session) -> Ontology:
    """The graph store when it's usable and matches the catalog's concepts; otherwise the JSON catalog."""
    global _cache
    if _cache is not None:
        return _cache
    loaded = load_catalog()
    expected = graph_spec(loaded.catalog)
    store = SqliteGraphStore(db)
    note = ""
    try:
        if not store.schema_ok() or store.is_empty():
            note = "The graph store hasn't been bootstrapped yet."
        else:
            actual = store.load()
            check = compare(expected, actual)
            if check.complete:
                _cache = Ontology(actual, "graph_store")
                return _cache
            note = (f"The graph store is incomplete ({len(check.missing_nodes)} missing concepts, "
                    f"{len(check.missing_edges)} missing links, {len(check.changed_nodes)} out of date).")
    except Exception as exc:  # noqa: BLE001  (a broken store must not stop the app)
        log.warning("Graph store unusable, using the JSON catalog", exc_info=True)
        note = f"The graph store couldn't be read: {exc}"
    _cache = Ontology(expected, "json_fallback", note=note + " Using the JSON catalog instead.")
    return _cache


def last_bootstrap(db: Session) -> BootstrapRun | None:
    return db.scalars(select(BootstrapRun).order_by(BootstrapRun.id.desc()).limit(1)).first()


__all__ = ["Ontology", "SqliteGraphStore", "Completeness", "BootstrapStep", "bootstrap", "compare",
           "get_ontology", "invalidate", "last_bootstrap"]
