"""SQLite metadata store: snapshots of BigQuery table details, shared by every scope that uses a table."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MetadataTable
from app.schemas.catalog import SemanticCatalog
from app.schemas.meta import TableMeta
from app.services.metadata import get_metadata_provider


def refresh_tables(db: Session, full_names: list[str]) -> dict[str, TableMeta]:
    """Read these tables' details from BigQuery (metadata only) and save them."""
    provider = get_metadata_provider()
    by_dataset: dict[tuple[str, str], list[str]] = {}
    for name in dict.fromkeys(full_names):
        project, dataset, table = name.split(".")
        by_dataset.setdefault((project, dataset), []).append(table)
    fetched: dict[str, TableMeta] = {}
    for (project, dataset), names in by_dataset.items():
        for meta in provider.get_tables(project, dataset, names):
            fetched[meta.full_name] = meta
    existing = {r.full_name: r for r in db.scalars(select(MetadataTable).where(MetadataTable.full_name.in_(list(fetched))))}
    for name, meta in fetched.items():
        row = existing.get(name)
        if row is None:
            db.add(MetadataTable(full_name=name, meta=meta.model_dump(mode="json")))
        else:
            row.meta = meta.model_dump(mode="json")
    db.flush()
    return fetched


def refresh_metadata(db: Session, catalog: SemanticCatalog) -> dict[str, TableMeta]:
    return refresh_tables(db, [t.table for t in catalog.tables])


def load_metadata(db: Session, full_names: list[str] | None = None) -> dict[str, TableMeta]:
    query = select(MetadataTable)
    if full_names is not None:
        query = query.where(MetadataTable.full_name.in_(full_names))
    return {r.full_name: TableMeta.model_validate(r.meta) for r in db.scalars(query)}
