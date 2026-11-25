"""Real BigQuery metadata using Application Default Credentials.

Only metadata calls and dry runs are made: no table data is ever read.
"""

import logging

from app.config import get_settings
from app.schemas.meta import ColumnMeta, DatasetInfo, KeyConstraint, ProjectInfo, TableInfo, TableMeta
from app.services.metadata.base import MetadataError, MetadataProvider
from app.services.metadata.pii import mark_pii
from app.services.text import humanize

log = logging.getLogger(__name__)
MAX_TABLES_LISTED = 200


def _client(project: str | None = None):
    from google.cloud import bigquery

    try:
        return bigquery.Client(project=project or get_settings().gcp_billing_project)
    except Exception as exc:  # missing credentials, bad project, ...
        raise MetadataError(
            "Couldn't connect to BigQuery. Check that Google Cloud credentials are set up "
            "(gcloud auth application-default login) and try again."
        ) from exc


def _friendly(exc: Exception, what: str) -> MetadataError:
    from google.api_core import exceptions as gexc

    if isinstance(exc, gexc.Forbidden):
        return MetadataError(f"You don't have permission to read {what}. Ask the data owner for BigQuery Metadata Viewer access.")
    if isinstance(exc, gexc.NotFound):
        return MetadataError(f"{what[:1].upper() + what[1:]} wasn't found.")
    log.exception("BigQuery error reading %s", what)
    return MetadataError(f"BigQuery couldn't read {what}. Try again in a moment.")


def _columns(fields, prefix: str = "") -> list[ColumnMeta]:
    cols: list[ColumnMeta] = []
    for f in fields:
        tags = list(getattr(f.policy_tags, "names", []) or []) if getattr(f, "policy_tags", None) else []
        cols.append(ColumnMeta(
            name=prefix + f.name, type=f.field_type, mode=f.mode or "NULLABLE",
            description=f.description or "", policy_tags=tags,
        ))
    return cols


class LiveBigQuery(MetadataProvider):
    def list_projects(self) -> list[ProjectInfo]:
        try:
            return [ProjectInfo(id=p.project_id, name=p.friendly_name or p.project_id) for p in _client().list_projects()]
        except MetadataError:
            raise
        except Exception as exc:
            raise _friendly(exc, "your projects") from exc

    def list_datasets(self, project: str) -> list[DatasetInfo]:
        client = _client(project)
        try:
            out = []
            for d in client.list_datasets(project):
                friendly = getattr(d, "friendly_name", None) or humanize(d.dataset_id)
                out.append(DatasetInfo(id=d.dataset_id, name=friendly))
            return out
        except Exception as exc:
            raise _friendly(exc, f"datasets in {project}") from exc

    def list_tables(self, project: str, dataset: str) -> list[TableInfo]:
        client = _client(project)
        try:
            out = []
            for i, item in enumerate(client.list_tables(f"{project}.{dataset}")):
                if i >= MAX_TABLES_LISTED:
                    break
                t = client.get_table(item.reference)
                out.append(TableInfo(
                    name=t.table_id, label=humanize(t.table_id), description=t.description or "",
                    row_count=t.num_rows, size_bytes=t.num_bytes,
                ))
            return out
        except Exception as exc:
            raise _friendly(exc, f"tables in {project}.{dataset}") from exc

    def get_tables(self, project: str, dataset: str, names: list[str]) -> list[TableMeta]:
        client = _client(project)
        metas = []
        for name in names:
            try:
                t = client.get_table(f"{project}.{dataset}.{name}")
            except Exception as exc:
                raise _friendly(exc, f"table {project}.{dataset}.{name}") from exc
            part = t.time_partitioning
            metas.append(TableMeta(
                name=t.table_id, project=project, dataset=dataset, description=t.description or "",
                row_count=t.num_rows, size_bytes=t.num_bytes,
                partition_field=part.field if part else None, partition_type=part.type_ if part else None,
                clustering=list(t.clustering_fields or []),
                last_modified=t.modified.isoformat() if t.modified else None,
                columns=mark_pii(_columns(t.schema)),
            ))
        return metas

    def get_constraints(self, project: str, dataset: str) -> list[KeyConstraint]:
        """Declared primary/foreign keys from INFORMATION_SCHEMA (a small metadata query, no table data)."""
        from google.cloud import bigquery

        sql = f"""
            SELECT tc.constraint_type, tc.table_name, k.column_name, k.ordinal_position,
                   c.table_name AS ref_table, c.column_name AS ref_column
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS` tc
            JOIN `{project}.{dataset}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE` k USING (constraint_name)
            LEFT JOIN `{project}.{dataset}.INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE` c
              ON tc.constraint_type = 'FOREIGN KEY' AND c.constraint_name = tc.constraint_name
            ORDER BY tc.constraint_name, k.ordinal_position"""
        client = _client(get_settings().gcp_billing_project or project)
        try:
            rows = list(client.query(sql, job_config=bigquery.QueryJobConfig(use_query_cache=True)).result())
        except Exception as exc:  # noqa: BLE001  (no permission, or constraints unsupported: treat as none)
            log.warning("Couldn't read key constraints for %s.%s: %s", project, dataset, exc)
            return []
        out: dict[tuple, KeyConstraint] = {}
        for r in rows:
            kind = "primary" if r.constraint_type == "PRIMARY KEY" else "foreign"
            key = (kind, r.table_name, r.ref_table)
            c = out.setdefault(key, KeyConstraint(
                kind=kind, table=f"{project}.{dataset}.{r.table_name}",
                ref_table=f"{project}.{dataset}.{r.ref_table}" if r.ref_table else None, columns=[]))
            if r.column_name not in c.columns:
                c.columns.append(r.column_name)
            if r.ref_column and r.ref_column not in c.ref_columns:
                c.ref_columns.append(r.ref_column)
        return list(out.values())

    def dry_run_bytes(self, project: str, sql: str) -> int | None:
        from google.cloud import bigquery

        try:
            client = _client(get_settings().gcp_billing_project or project)
            job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
            return int(job.total_bytes_processed or 0)
        except Exception:
            log.warning("Dry run failed", exc_info=True)
            return None
