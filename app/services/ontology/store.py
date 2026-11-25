"""Embedded graph store: ontology nodes and edges kept in the app's SQLite database.

This replaces Neo4j so the app ships as one package with nothing else to install. Each scope (the curated
catalog, or one session) has its own nodes and edges. Reads come back as a GraphSpec, which `Ontology`
turns into an in-memory NetworkX graph for traversal.
"""

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import CATALOG_SCOPE, OntologyEdge, OntologyNode
from app.services.catalog import GraphSpec

TABLES = [OntologyNode.__table__, OntologyEdge.__table__]


class GraphStoreError(Exception):
    pass


class SqliteGraphStore:
    name = "SQLite graph store"

    def __init__(self, db: Session, scope: str = CATALOG_SCOPE):
        self.db = db
        self.scope = scope

    def _nodes(self):
        return select(OntologyNode).where(OntologyNode.scope == self.scope)

    def _edges(self):
        return select(OntologyEdge).where(OntologyEdge.scope == self.scope)

    # ---- schema ("constraints") ----
    def ensure_schema(self) -> list[str]:
        """Create the node/edge tables with their unique and foreign-key constraints. Returns what was created."""
        engine = get_engine()
        existing = set(inspect(engine).get_table_names())
        missing = [t for t in TABLES if t.name not in existing]
        for t in missing:
            t.create(engine)
        return [t.name for t in missing]

    def schema_ok(self) -> bool:
        existing = set(inspect(get_engine()).get_table_names())
        return all(t.name in existing for t in TABLES)

    # ---- reads ----
    def counts(self) -> dict[str, int]:
        rows = self.db.execute(select(OntologyNode.kind, func.count()).where(OntologyNode.scope == self.scope)
                               .group_by(OntologyNode.kind)).all()
        out = {k: n for k, n in rows}
        out["edges"] = self.db.scalar(select(func.count()).select_from(OntologyEdge)
                                      .where(OntologyEdge.scope == self.scope)) or 0
        return out

    def is_empty(self) -> bool:
        return not self.db.scalar(select(func.count()).select_from(OntologyNode).where(OntologyNode.scope == self.scope))

    def load(self) -> GraphSpec:
        spec = GraphSpec()
        for n in self.db.scalars(self._nodes()):
            spec.nodes[n.id] = {"kind": n.kind, "name": n.name, "label": n.label, "props": n.props or {}}
        for e in self.db.scalars(self._edges()):
            spec.edges.append({"source": e.source, "target": e.target, "rel": e.rel, "props": e.props or {}})
        return spec

    # ---- writes ----
    def clear(self) -> None:
        self.db.execute(delete(OntologyEdge).where(OntologyEdge.scope == self.scope))
        self.db.execute(delete(OntologyNode).where(OntologyNode.scope == self.scope))

    def sync_nodes(self, spec: GraphSpec, kinds: set[str]) -> dict[str, int]:
        """Make nodes of these kinds match the spec: add missing, update changed, remove ones no longer listed."""
        wanted = {nid: n for nid, n in spec.nodes.items() if n["kind"] in kinds}
        current = {n.id: n for n in self.db.scalars(self._nodes().where(OntologyNode.kind.in_(kinds)))}
        added = updated = 0
        for nid, n in wanted.items():
            row = current.get(nid)
            if row is None:
                self.db.add(OntologyNode(scope=self.scope, id=nid, kind=n["kind"], name=n["name"], label=n["label"],
                                         props=n["props"]))
                added += 1
            elif (row.label, row.props) != (n["label"], n["props"]):
                row.label, row.props = n["label"], n["props"]
                updated += 1
        stale = [nid for nid in current if nid not in wanted]
        if stale:
            self.db.execute(delete(OntologyEdge).where(OntologyEdge.scope == self.scope)
                            .where(OntologyEdge.source.in_(stale) | OntologyEdge.target.in_(stale)))
            self.db.execute(delete(OntologyNode).where(OntologyNode.scope == self.scope).where(OntologyNode.id.in_(stale)))
        self.db.flush()
        return {"added": added, "updated": updated, "removed": len(stale)}

    def sync_edges(self, spec: GraphSpec, rels: set[str]) -> dict[str, int]:
        wanted = {(e["source"], e["target"], e["rel"]): e for e in spec.edges if e["rel"] in rels}
        current = {(e.source, e.target, e.rel): e for e in self.db.scalars(self._edges().where(OntologyEdge.rel.in_(rels)))}
        added = updated = 0
        for key, e in wanted.items():
            row = current.get(key)
            if row is None:
                self.db.add(OntologyEdge(scope=self.scope, source=e["source"], target=e["target"], rel=e["rel"],
                                         props=e["props"]))
                added += 1
            elif row.props != e["props"]:
                row.props = e["props"]
                updated += 1
        stale = [row.id for key, row in current.items() if key not in wanted]
        if stale:
            self.db.execute(delete(OntologyEdge).where(OntologyEdge.id.in_(stale)))
        self.db.flush()
        return {"added": added, "updated": updated, "removed": len(stale)}
