"""Sessions: an ontology assembled on demand for the BigQuery tables someone picks.

Building a session reads metadata only (never table data), so table size doesn't matter:
1. Refresh the tables' details and read declared keys from BigQuery.
2. Assemble a catalog from the sources (curated, Looker placeholder, declared, inferred) and approvals.
3. Sync the session's graph and vector index (scope "session:<id>").

A fingerprint of everything that went in (table schemas, keys, curated catalog, approvals) lets an unchanged
session skip the work. Approvals are keyed by BigQuery names, so a decision made in one session applies to
every session over the same tables.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Approval, HubSession, IndexDocument, QuestionRecord, session_scope
from app.schemas.catalog import SemanticCatalog
from app.services.catalog import CatalogError, load_catalog
from app.services.metadata import MetadataError, get_metadata_provider
from app.services.ontology import invalidate, sync_graph
from app.services.ontology.store import SqliteGraphStore
from app.services.retrieval import build_index, drop_index
from app.services.retrieval.metadata_store import refresh_tables
from app.services.sessions.assemble import EDITABLE, AssemblyError, assemble

log = logging.getLogger(__name__)
BUILDER_VERSION = "1"  # bump when assembly rules change, so sessions rebuild


class SessionError(Exception):
    """A problem with a session the user can fix (bad table names, too many tables, ...)."""


def _check_tables(tables: list[str]) -> list[str]:
    clean = list(dict.fromkeys(t.strip() for t in tables if t and t.strip()))
    if not clean:
        raise SessionError("Pick at least one table.")
    limit = get_settings().max_session_tables
    if len(clean) > limit:
        raise SessionError(f"A session can have at most {limit} tables (SH_MAX_SESSION_TABLES).")
    bad = [t for t in clean if len(t.split(".")) != 3 or not all(t.split("."))]
    if bad:
        raise SessionError(f"Use project.dataset.table names: {', '.join(bad[:5])}")
    return clean


def create_session(db: Session, name: str, tables: list[str]) -> HubSession:
    s = HubSession(name=(name or "").strip()[:200] or "Untitled session", tables=_check_tables(tables))
    db.add(s)
    db.commit()
    return build_session(db, s, force=True)


def _approvals(db: Session) -> dict[str, dict]:
    return {a.key: {"decision": a.decision, "changes": a.changes or {}} for a in db.scalars(select(Approval))}


def _fingerprint(metas, constraints, curated_sha: str, approvals: dict, tables: list[str]) -> str:
    relevant = {k: v for k, v in approvals.items() if any(t in k for t in tables)}
    blob = json.dumps({
        "v": BUILDER_VERSION,
        "tables": {k: m.model_dump(mode="json", exclude={"row_count", "size_bytes"}) for k, m in sorted(metas.items())},
        "keys": [c.model_dump() for c in constraints], "curated": curated_sha, "approvals": relevant,
    }, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def build_session(db: Session, s: HubSession, *, force: bool = False) -> HubSession:
    """(Re)assemble a session's catalog, graph and index. Skips the work when nothing changed."""
    scope = s.scope
    try:
        metas = refresh_tables(db, s.tables)
        missing = [t for t in s.tables if t not in metas]
        if missing:
            raise SessionError(f"These tables weren't found: {', '.join(missing)}")
        provider = get_metadata_provider()
        constraints = []
        for project, dataset in dict.fromkeys(tuple(t.split(".")[:2]) for t in s.tables):
            constraints += provider.get_constraints(project, dataset)
        curated = load_catalog()
        approvals = _approvals(db)
        fp = _fingerprint(metas, constraints, curated.sha256, approvals, s.tables)
        if not force and s.status == "ready" and fp == s.fingerprint:
            return s
        catalog, report = assemble(curated.catalog, metas, constraints, approvals, version=f"session-{s.id}")
        s.catalog = catalog.model_dump(mode="json")
        s.report, s.fingerprint, s.error = report, fp, ""
        db.commit()
        invalidate(scope)
        sync_graph(db, catalog, scope, catalog_hash=fp)
        build_index(db, scope, refresh=False)
        s.status, s.built_at = "ready", datetime.now(timezone.utc)
        db.commit()
        log.info("Session %s built: %s", s.id, report["counts"])
    except (SessionError, MetadataError, CatalogError, AssemblyError) as exc:
        db.rollback()
        s.status, s.error = "failed", str(exc)
        db.commit()
        raise SessionError(str(exc)) from exc
    return s


def delete_session(db: Session, s: HubSession) -> None:
    scope = s.scope
    SqliteGraphStore(db, scope).clear()
    drop_index(db, scope)
    db.query(QuestionRecord).filter(QuestionRecord.scope == scope).delete()
    db.query(IndexDocument).filter(IndexDocument.scope == scope).delete()
    db.delete(s)
    db.commit()
    invalidate(scope)


def session_catalog(s: HubSession) -> SemanticCatalog:
    if not s.catalog:
        raise SessionError("This session hasn't been built yet.")
    return SemanticCatalog.model_validate(s.catalog)


def review_items(s: HubSession) -> list[dict]:
    """Everything in the session that isn't certified, for a person to confirm, correct or reject."""
    c = session_catalog(s)
    full = {t.name: t.table for t in c.tables}
    out = []
    for m in c.metrics:
        if m.trust != "certified":
            how = "count of rows" if m.agg == "count" else f"{m.agg.replace('_', ' ')} of {m.column}"
            out.append({"key": m.key, "kind": "metric", "name": m.name, "label": m.label, "trust": m.trust,
                        "detail": f"{how} on {full.get(m.table, m.table)}", "synonyms": m.synonyms,
                        "agg": m.agg, "format": m.format})
    for d in c.dimensions:
        if d.trust != "certified":
            out.append({"key": d.key, "kind": "dimension", "name": d.name, "label": d.label, "trust": d.trust,
                        "detail": f"{d.type} · {full.get(d.table, d.table)}.{d.column}", "synonyms": d.synonyms,
                        "pii": d.pii})
    for j in c.joins:
        if j.trust != "certified":
            keys = " and ".join(f"{j.left}.{k.left} = {j.right}.{k.right}" for k in j.on)
            out.append({"key": j.key, "kind": "join", "name": j.name, "label": f"{j.left} → {j.right}", "trust": j.trust,
                        "detail": keys, "relationship": j.relationship})
    for t in c.tables:
        if t.trust != "certified":
            out.append({"key": t.key, "kind": "table", "name": t.name, "label": t.label, "trust": t.trust,
                        "detail": f"{t.table} · key: {t.primary_key or 'none found'}", "synonyms": t.synonyms,
                        "primary_key": t.primary_key})
    order = {"join": 0, "metric": 1, "dimension": 2, "table": 3}
    return sorted(out, key=lambda x: (order[x["kind"]], x["trust"] == "declared", x["label"].lower()))


def _tables_in_key(key: str) -> list[str]:
    parts = key.split(":")
    return [p for p in parts[1:3] if len(p.split(".")) == 3]


def decide(db: Session, key: str, decision: str, changes: dict | None = None) -> list[HubSession]:
    """Record a person's decision on an item, then rebuild the sessions it affects."""
    kind = key.split(":", 1)[0]
    if kind not in EDITABLE or decision not in ("approved", "rejected", "reset"):
        raise SessionError("Unknown item or decision.")
    if decision == "rejected" and kind == "table":
        raise SessionError("Tables can't be rejected; remove the table from the session instead.")
    changes = {k: v for k, v in (changes or {}).items() if k in EDITABLE[kind]}
    if "synonyms" in changes:
        changes["synonyms"] = [s.strip() for s in changes["synonyms"] if isinstance(s, str) and s.strip()][:30]
    row = db.get(Approval, key)
    if decision == "reset":
        if row:
            db.delete(row)
    elif row is None:
        db.add(Approval(key=key, kind=kind, decision=decision, changes=changes, tables=_tables_in_key(key)))
    else:
        row.decision, row.changes, row.tables = decision, changes, _tables_in_key(key)
    db.commit()
    affected = [s for s in db.scalars(select(HubSession))
                if set(_tables_in_key(key)) <= set(s.tables) and s.status != "new"]
    rebuilt = []
    for s in affected:
        try:
            rebuilt.append(build_session(db, s))
        except SessionError:
            log.warning("Rebuilding session %s after a decision failed", s.id, exc_info=True)
    return rebuilt


__all__ = ["SessionError", "create_session", "build_session", "delete_session", "review_items", "decide",
           "session_catalog", "session_scope"]
