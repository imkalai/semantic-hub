"""SQLite metadata store: snapshots of BigQuery table details for the tables the catalog uses."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MetadataTable
from app.schemas.catalog import SemanticCatalog
from app.schemas.meta import TableMeta
from app.services.metadata import get_metadata_provider


def refresh_metadata(db: Session, catalog: SemanticCatalog) -> dict[str, TableMeta]:
    """Read every catalog table's details from BigQuery (metadata only) and save them."""
    provider = get_metadata_provider()
    by_dataset: dict[tuple[str, str], list[str]] = {}
    for t in catalog.tables:
        by_dataset.setdefault((t.project, t.dataset), []).append(t.table_name)
    fetched: dict[str, TableMeta] = {}
    for (project, dataset), names in by_dataset.items():
        for meta in provider.get_tables(project, dataset, names):
            fetched[meta.full_name] = meta
    existing = {r.full_name: r for r in db.scalars(select(MetadataTable))}
    for name, meta in fetched.items():
        row = existing.get(name)
        if row is None:
            db.add(MetadataTable(full_name=name, meta=meta.model_dump(mode="json")))
        else:
            row.meta = meta.model_dump(mode="json")
    for name, row in existing.items():
        if name not in fetched:
            db.delete(row)
    db.flush()
    return fetched


def load_metadata(db: Session) -> dict[str, TableMeta]:
    return {r.full_name: TableMeta.model_validate(r.meta) for r in db.scalars(select(MetadataTable))}
