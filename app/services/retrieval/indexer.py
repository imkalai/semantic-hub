"""Build and update a scope's metadata index: refresh BigQuery details into SQLite, then embed only the
documents that are new or changed and keep Chroma in step with the index_documents table."""

import logging
from dataclasses import asdict, dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CATALOG_SCOPE, IndexDocument, IndexRun
from app.services.cortex import get_cortex
from app.services.ontology import get_ontology, scope_catalog
from app.services.retrieval.documents import Doc, build_documents
from app.services.retrieval.metadata_store import load_metadata, refresh_metadata
from app.services.retrieval.vector import VectorIndex

log = logging.getLogger(__name__)
BATCH = 64


@dataclass
class IndexResult:
    documents: int
    embedded: int
    removed: int
    unchanged: int
    tables_refreshed: int
    signature: str
    rebuilt: bool
    ontology_source: str


def embed_signature() -> str:
    s = get_settings()
    if s.cortex_mode == "mock":
        return f"mock:hashed:{s.embedding_dim}"
    return f"cortex:{s.cortex_embed_model}"


def current_documents(db: Session, scope: str = CATALOG_SCOPE, *, refresh: bool) -> tuple[list[Doc], int, str]:
    catalog = scope_catalog(db, scope)
    tables = [t.table for t in catalog.tables]
    metadata = refresh_metadata(db, catalog) if refresh else load_metadata(db, tables)
    onto = get_ontology(db, scope)
    return build_documents(onto, metadata), len(metadata), onto.source


def _stored(db: Session, scope: str) -> dict[str, IndexDocument]:
    return {r.id: r for r in db.scalars(select(IndexDocument).where(IndexDocument.scope == scope))}


def build_index(db: Session, scope: str = CATALOG_SCOPE, *, rebuild: bool = False, refresh: bool = True) -> IndexResult:
    signature = embed_signature()
    run = IndexRun(scope=scope, embed_signature=signature)
    try:
        docs, n_tables, source = current_documents(db, scope, refresh=refresh)
        index = VectorIndex(scope)
        if rebuild:
            index.reset()
        previous_signature = index.signature()
        col = index.collection(signature)  # recreated if the embedding model changed
        stored = _stored(db, scope)
        in_chroma = set(col.get(include=[])["ids"])
        rebuilt = rebuild or previous_signature not in (None, signature)
        if rebuilt:
            stored, in_chroma = {}, set()
            db.execute(delete(IndexDocument).where(IndexDocument.scope == scope))

        wanted = {d.id: d for d in docs}
        todo = [d for d in docs if d.id not in in_chroma or d.id not in stored
                or stored[d.id].text_hash != d.hash(signature)]
        cortex = get_cortex()
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            vectors = cortex.embed([d.text for d in batch])
            col.upsert(ids=[d.id for d in batch], embeddings=vectors, documents=[d.text for d in batch],
                       metadatas=[{"kind": d.kind, **d.meta} for d in batch])
            for d in batch:
                row = stored.get(d.id)
                if row is None:
                    db.add(IndexDocument(scope=scope, id=d.id, kind=d.kind, text_hash=d.hash(signature)))
                else:
                    row.kind, row.text_hash = d.kind, d.hash(signature)
            db.flush()

        stale = (set(stored) | in_chroma) - set(wanted)
        if stale:
            if stale & in_chroma:
                col.delete(ids=sorted(stale & in_chroma))
            db.execute(delete(IndexDocument).where(IndexDocument.scope == scope).where(IndexDocument.id.in_(stale)))
        result = IndexResult(documents=len(docs), embedded=len(todo), removed=len(stale),
                             unchanged=len(docs) - len(todo), tables_refreshed=n_tables if refresh else 0,
                             signature=signature, rebuilt=rebuilt, ontology_source=source)
        run.ok, run.summary = True, asdict(result)
        db.add(run)
        db.commit()
        log.info("Metadata index %s updated: %s", scope, run.summary)
        return result
    except Exception as exc:
        db.rollback()
        run.ok, run.error = False, str(exc)
        db.add(run)
        db.commit()
        raise


def drop_index(db: Session, scope: str) -> None:
    VectorIndex(scope).reset()
    db.execute(delete(IndexDocument).where(IndexDocument.scope == scope))


def index_status(db: Session, scope: str = CATALOG_SCOPE) -> dict:
    """Compare the index with what it should hold now (using the saved metadata, without calling BigQuery)."""
    signature = embed_signature()
    docs, n_tables, source = current_documents(db, scope, refresh=False)
    stored = {r.id: r.text_hash for r in _stored(db, scope).values()}
    index = VectorIndex(scope)
    in_chroma = index.ids()
    missing = [d.id for d in docs if d.id not in stored or d.id not in in_chroma]
    changed = [d.id for d in docs if d.id in stored and stored[d.id] != d.hash(signature)]
    extra = sorted((set(stored) | in_chroma) - {d.id for d in docs})
    last = db.scalars(select(IndexRun).where(IndexRun.scope == scope).order_by(IndexRun.id.desc()).limit(1)).first()
    return {
        "documents_expected": len(docs), "documents_indexed": len(in_chroma), "tables_with_metadata": n_tables,
        "missing": missing, "changed": changed, "extra": extra,
        "signature": signature, "index_signature": index.signature(),
        "up_to_date": not (missing or changed or extra) and index.signature() in (None, signature) and bool(docs),
        "ontology_source": source,
        "last_run": {"at": last.started_at.isoformat(), "ok": last.ok, "error": last.error} if last else None,
    }
