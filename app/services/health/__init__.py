"""Healthcheck (diagram: Operations & Reliability).

Checks run in four groups: Config / SQLite / Chroma, Cortex / BigQuery, graph store connectivity, and
ontology completeness against the catalog. The app is "ready for runtime" when no check fails; otherwise
the answer is to repair the graph or rerun bootstrap.
"""

import logging
import time
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import check_db
from app.services.catalog import CatalogError, LoadedCatalog, graph_spec, load_catalog
from app.services.cortex import get_cortex
from app.services.metadata import MetadataError, get_metadata_provider
from app.services.ontology import compare, last_bootstrap
from app.services.ontology.store import SqliteGraphStore
from app.services.retrieval import VectorIndex, index_status

log = logging.getLogger(__name__)
Status = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    group: str
    name: str
    status: Status
    detail: str
    fix: str = ""
    ms: int = 0


def _timed(fn):
    start = time.perf_counter()
    check = fn()
    check.ms = int((time.perf_counter() - start) * 1000)
    return check


# ---------- Config / SQLite / Chroma ----------

def check_config() -> Check:
    s = get_settings()
    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        probe = s.data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check("Config / SQLite / Chroma", "Settings and data folder", "fail",
                     f"Can't write to {s.data_dir}: {exc.strerror or exc}", "Set SH_DATA_DIR to a folder you can write to.")
    return Check("Config / SQLite / Chroma", "Settings and data folder", "ok",
                 f"Data folder {s.data_dir} · BigQuery {s.metadata_mode} · Cortex {s.cortex_mode}")


def check_catalog() -> tuple[Check, LoadedCatalog | None]:
    try:
        loaded = load_catalog()
    except CatalogError as exc:
        return Check("Config / SQLite / Chroma", "Semantic catalog", "fail", str(exc),
                     "Fix the catalog file, then run the healthcheck again."), None
    c = loaded.catalog
    return Check("Config / SQLite / Chroma", "Semantic catalog", "ok",
                 f"{loaded.path.name} v{c.version}: {len(c.tables)} tables, {len(c.metrics)} metrics, "
                 f"{len(c.dimensions)} dimensions, {len(c.joins)} joins"), loaded


def check_sqlite() -> Check:
    ok, detail = check_db()
    return Check("Config / SQLite / Chroma", "SQLite metadata store", "ok" if ok else "fail", detail,
                 "" if ok else "Check the data folder isn't read-only or locked by another copy of the app.")


def check_chroma() -> Check:
    s = get_settings()
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return Check("Config / SQLite / Chroma", "Chroma vector index", "warn", "chromadb isn't installed.",
                     "pip install -r requirements.txt")
    try:
        index = VectorIndex()
        index.client.heartbeat()
        n = index.count()
    except Exception as exc:  # noqa: BLE001
        return Check("Config / SQLite / Chroma", "Chroma vector index", "fail", f"Couldn't open {s.chroma_dir}: {exc}",
                     "Delete the chroma folder in the data folder, then run: python -m app.cli index")
    return Check("Config / SQLite / Chroma", "Chroma vector index", "ok", f"Opened {s.chroma_dir} ({n} vectors)")


def check_index(db: Session, loaded: LoadedCatalog | None) -> Check:
    group, name = "Config / SQLite / Chroma", "Metadata index"
    if loaded is None:
        return Check(group, name, "warn", "Skipped: the catalog couldn't be read.")
    try:
        st = index_status(db)
    except Exception as exc:  # noqa: BLE001
        return Check(group, name, "fail", f"Couldn't check the index: {exc}", "Run: python -m app.cli index --rebuild")
    fix = "Run: python -m app.cli index (or Rebuild index on this page)."
    if st["documents_indexed"] == 0 or st["tables_with_metadata"] == 0:
        return Check(group, name, "fail", "The index is empty, so questions can only be matched word for word.", fix)
    if st["index_signature"] not in (None, st["signature"]):
        return Check(group, name, "fail",
                     f"The index was built with {st['index_signature']}, but Cortex now uses {st['signature']}.", fix)
    if not st["up_to_date"]:
        parts = [f"{len(st[k])} {label}" for k, label in (("missing", "missing"), ("changed", "out of date"),
                                                           ("extra", "no longer in the catalog")) if st[k]]
        return Check(group, name, "warn", f"{st['documents_indexed']} documents indexed; " + ", ".join(parts) + ".", fix)
    return Check(group, name, "ok", f"{st['documents_indexed']} documents, metadata for {st['tables_with_metadata']} "
                 f"tables, embeddings {st['signature']}")


# ---------- Cortex / BigQuery ----------

def check_cortex() -> Check:
    s = get_settings()
    ok, detail = get_cortex().health()
    status: Status = "ok" if ok else "fail"
    if ok and s.cortex_mode == "mock":
        status = "warn"
    return Check("Cortex / BigQuery", f"Cortex ({s.cortex_mode})", status, detail,
                 "" if ok and s.cortex_mode == "real" else
                 ("Set SH_CORTEX_MODE=real once the corporate client is in app/services/cortex/real.py." if ok else
                  "Check SH_CORTEX_ENDPOINT and SH_CORTEX_API_KEY."))


def check_bigquery(loaded: LoadedCatalog | None) -> Check:
    s = get_settings()
    name = f"BigQuery metadata ({s.metadata_mode})"
    if loaded is None:
        return Check("Cortex / BigQuery", name, "warn", "Skipped: the catalog couldn't be read.")
    c = loaded.catalog
    provider = get_metadata_provider()
    by_dataset: dict[tuple[str, str], list[str]] = {}
    for t in c.tables:
        by_dataset.setdefault((t.project, t.dataset), []).append(t.table_name)
    columns: dict[str, set[str]] = {}
    try:
        for (project, dataset), names in by_dataset.items():
            for meta in provider.get_tables(project, dataset, names):
                columns[meta.full_name] = {col.name.lower() for col in meta.columns}
    except MetadataError as exc:
        return Check("Cortex / BigQuery", name, "fail", str(exc), "Check the table names in the catalog and your BigQuery access.")
    except Exception as exc:  # noqa: BLE001  (missing credentials, library not installed, ...)
        return Check("Cortex / BigQuery", name, "fail", f"Couldn't read BigQuery: {exc}",
                     "Run 'gcloud auth application-default login', or set SH_METADATA_MODE=demo.")

    missing: list[str] = []

    def need(table_name: str, col: str | None) -> None:
        t = c.table(table_name)
        if t and col and col.lower() not in columns.get(t.table, set()):
            missing.append(f"{t.table}.{col}")

    for t in c.tables:
        need(t.name, t.primary_key)
        need(t.name, t.time_column)
    for m in c.metrics:
        need(m.table, m.column)
    for d in c.dimensions:
        need(d.table, d.column)
    for j in c.joins:
        for k in j.on:
            need(j.left, k.left)
            need(j.right, k.right)
    if missing:
        return Check("Cortex / BigQuery", name, "fail",
                     f"{len(missing)} catalog column(s) aren't in BigQuery: {', '.join(sorted(set(missing))[:8])}",
                     "Correct the catalog, or check that the right project and dataset are used.")
    status: Status = "warn" if s.metadata_mode == "demo" else "ok"
    return Check("Cortex / BigQuery", name, status,
                 f"All {len(c.tables)} catalog tables and their columns found" + (" (built-in demo tables)" if status == "warn" else ""),
                 "Set SH_METADATA_MODE=live to use real BigQuery." if status == "warn" else "")


def check_looker() -> Check:
    from app.services.sessions.looker import LookerSource

    status, detail = LookerSource().health()
    return Check("Cortex / BigQuery", "Looker", status, detail)


# ---------- graph store and completeness ----------

def check_graph_store(db: Session) -> Check:
    store = SqliteGraphStore(db)
    try:
        if not store.schema_ok():
            return Check("Graph store", "Graph store connectivity", "fail", "The graph tables don't exist yet.",
                         "Run bootstrap: python -m app.cli bootstrap")
        counts = store.counts()
    except Exception as exc:  # noqa: BLE001
        return Check("Graph store", "Graph store connectivity", "fail", f"Couldn't read the graph store: {exc}",
                     "Run bootstrap with --reset to rebuild it.")
    nodes = sum(v for k, v in counts.items() if k != "edges")
    run = last_bootstrap(db)
    when = f"; last bootstrap {run.started_at:%Y-%m-%d %H:%M} UTC ({'ok' if run.ok else 'failed'})" if run else ""
    return Check("Graph store", "Graph store connectivity", "ok", f"{nodes} nodes, {counts['edges']} edges{when}")


def check_completeness(db: Session, loaded: LoadedCatalog | None) -> Check:
    name = "Ontology completeness vs catalog"
    if loaded is None:
        return Check("Ontology", name, "fail", "Can't compare: the catalog couldn't be read.", "Fix the catalog first.")
    store = SqliteGraphStore(db)
    try:
        if not store.schema_ok():
            return Check("Ontology", name, "fail", "The graph store is empty.", "Run bootstrap: python -m app.cli bootstrap")
        result = compare(graph_spec(loaded.catalog), store.load())
    except Exception as exc:  # noqa: BLE001
        return Check("Ontology", name, "fail", f"Couldn't compare: {exc}", "Run bootstrap with --reset.")
    if result.complete:
        extra = f"; {len(result.extra_nodes)} extra node(s) not in the catalog" if result.extra_nodes else ""
        return Check("Ontology", name, "warn" if extra else "ok",
                     f"All {result.expected_nodes} expected concepts and {result.expected_edges} links present{extra}",
                     "Rerun bootstrap to remove them." if extra else "")
    parts = []
    if result.missing_nodes:
        parts.append(f"{len(result.missing_nodes)} missing concept(s), e.g. {', '.join(result.missing_nodes[:4])}")
    if result.missing_edges:
        parts.append(f"{len(result.missing_edges)} missing link(s), e.g. {result.missing_edges[0]}")
    if result.changed_nodes:
        parts.append(f"{len(result.changed_nodes)} out-of-date concept(s), e.g. {', '.join(result.changed_nodes[:4])}")
    return Check("Ontology", name, "fail", "; ".join(parts), "Repair the graph or rerun bootstrap.")


# ---------- run everything ----------

def run_healthcheck(db: Session) -> dict:
    checks: list[Check] = [_timed(check_config)]
    start = time.perf_counter()
    catalog_check, loaded = check_catalog()
    catalog_check.ms = int((time.perf_counter() - start) * 1000)
    checks += [catalog_check, _timed(check_sqlite), _timed(check_chroma), _timed(lambda: check_index(db, loaded)),
               _timed(check_cortex),
               _timed(lambda: check_bigquery(loaded)), _timed(check_looker), _timed(lambda: check_graph_store(db)),
               _timed(lambda: check_completeness(db, loaded))]
    failed = [c for c in checks if c.status == "fail"]
    if not failed:
        verdict = "Ready for runtime"
    elif any(c.group in ("Graph store", "Ontology") for c in failed):
        verdict = "Repair graph or rerun bootstrap"
    elif all(c.name == "Metadata index" for c in failed):
        verdict = "Rebuild the metadata index"
    else:
        verdict = "Fix the failing checks"
    return {
        "ready": not failed,
        "verdict": verdict,
        "checks": [asdict(c) for c in checks],
    }
