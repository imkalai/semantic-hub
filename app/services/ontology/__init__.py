"""Ontology layer: the embedded graph store, bootstrap from the catalog, completeness checks, and the
JSON catalog fallback (diagram: Ontology Resolution -> Neo4j graph / JSON Catalog Fallback)."""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CATALOG_SCOPE, BootstrapRun, HubSession
from app.schemas.catalog import SemanticCatalog
from app.services.catalog import CatalogError, GraphSpec, LoadedCatalog, graph_spec, load_catalog
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
    """Seed the curated catalog's graph from the catalog file. Safe to rerun: it adds what's missing, updates
    what changed and removes what the catalog no longer lists. `reset` wipes the graph first."""
    loaded = loaded or load_catalog()
    return sync_graph(db, loaded.catalog, CATALOG_SCOPE, catalog_hash=loaded.sha256, reset=reset)


def sync_graph(db: Session, catalog: SemanticCatalog, scope: str, *, catalog_hash: str = "",
               reset: bool = False) -> list[BootstrapStep]:
    """Make a scope's graph match a catalog (the curated one, or a session's assembled one)."""
    spec = graph_spec(catalog)
    store = SqliteGraphStore(db, scope)
    run = BootstrapRun(scope=scope, catalog_version=catalog.version, catalog_hash=catalog_hash)
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
                                   detail=f"including {len(catalog.tables)} tables"))
        steps.append(BootstrapStep("Seed approved joins", **store.sync_edges(spec, {"JOINS"})))
        syn = store.sync_nodes(spec, {"synonym"})
        steps.append(BootstrapStep("Seed synonyms", **_sum(syn, store.sync_edges(spec, {"SYNONYM_OF"}))))
        run.ok = True
        run.summary = {s.name: {"added": s.added, "updated": s.updated, "removed": s.removed} for s in steps}
        db.add(run)
        db.commit()
    except Exception as exc:
        db.rollback()  # the caller's other pending changes go too; sync runs in its own unit of work
        run.ok, run.error = False, str(exc)
        db.add(run)
        db.commit()
        raise
    finally:
        invalidate(scope)
    log.info("Graph %s synced: %s", scope, run.summary)
    return steps


def _sum(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("added", "updated", "removed")}


# ---------- the ontology the rest of the app uses ----------

_cache: dict[str, Ontology] = {}


def invalidate(scope: str | None = None) -> None:
    if scope is None:
        _cache.clear()
    else:
        _cache.pop(scope, None)


def scope_catalog(db: Session, scope: str) -> SemanticCatalog:
    """The catalog a scope's graph should match: the catalog file, or a session's assembled catalog."""
    if scope == CATALOG_SCOPE:
        return load_catalog().catalog
    if not scope.startswith("session:") or not scope.split(":", 1)[1].isdigit():
        raise CatalogError(f"Unknown scope '{scope}'.")
    row = db.get(HubSession, int(scope.split(":", 1)[1]))
    if row is None:
        raise CatalogError("That session doesn't exist.")
    if not row.catalog:
        raise CatalogError(f"Session '{row.name}' hasn't been built yet.")
    return SemanticCatalog.model_validate(row.catalog)


def get_ontology(db: Session, scope: str = CATALOG_SCOPE) -> Ontology:
    """The scope's graph when it's usable and matches its catalog; otherwise that catalog itself."""
    if scope in _cache:
        return _cache[scope]
    expected = graph_spec(scope_catalog(db, scope))
    store = SqliteGraphStore(db, scope)
    note = ""
    try:
        if not store.schema_ok() or store.is_empty():
            note = ("The graph store hasn't been bootstrapped yet." if scope == CATALOG_SCOPE
                    else "The session's graph hasn't been built yet.")
        else:
            actual = store.load()
            check = compare(expected, actual)
            if check.complete:
                _cache[scope] = Ontology(actual, "graph_store")
                return _cache[scope]
            note = (f"The graph store is incomplete ({len(check.missing_nodes)} missing concepts, "
                    f"{len(check.missing_edges)} missing links, {len(check.changed_nodes)} out of date).")
    except Exception as exc:  # noqa: BLE001  (a broken store must not stop the app)
        log.warning("Graph store unusable, using the catalog", exc_info=True)
        note = f"The graph store couldn't be read: {exc}"
    what = "the JSON catalog" if scope == CATALOG_SCOPE else "the session's assembled catalog"
    _cache[scope] = Ontology(expected, "json_fallback", note=f"{note} Using {what} instead.")
    return _cache[scope]


def last_bootstrap(db: Session, scope: str = CATALOG_SCOPE) -> BootstrapRun | None:
    return db.scalars(select(BootstrapRun).where(BootstrapRun.scope == scope)
                      .order_by(BootstrapRun.id.desc()).limit(1)).first()


__all__ = ["Ontology", "SqliteGraphStore", "Completeness", "BootstrapStep", "bootstrap", "sync_graph", "compare",
           "get_ontology", "invalidate", "last_bootstrap", "scope_catalog", "CATALOG_SCOPE"]
