"""SQLite database in the user's data folder. Holds app data and the ontology graph store."""

import logging
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")  # edges can't point at missing nodes
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{settings.db_path.as_posix()}", connect_args={"check_same_thread": False})
        event.listen(_engine, "connect", _sqlite_pragmas)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    """Create any missing tables. The graph store's constraints are created here too (see bootstrap)."""
    from app import models  # noqa: F401  (registers tables)

    _upgrade_sqlite()
    Base.metadata.create_all(get_engine())
    log.info("Database ready at %s", get_settings().db_path)


# Tables holding only derived data (rebuilt by bootstrap and indexing), and columns added since the first
# release. Small enough not to need a migration tool yet.
_DERIVED = ["ontology_edges", "ontology_nodes", "index_documents"]
_ADDED_COLUMNS = {"questions": "scope", "bootstrap_runs": "scope", "index_runs": "scope"}


def _upgrade_sqlite() -> None:
    from sqlalchemy import inspect

    engine = get_engine()
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        if "ontology_nodes" in existing and "scope" not in {c["name"] for c in insp.get_columns("ontology_nodes")}:
            log.info("Upgrading the graph store to scoped tables; it's rebuilt from the catalog")
            for t in _DERIVED:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        for table, column in _ADDED_COLUMNS.items():
            if table in existing and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR(40) DEFAULT 'catalog'"))


def check_db() -> tuple[bool, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, str(get_settings().db_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Database check failed", exc_info=True)
        return False, str(exc)


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _SessionLocal = None


def new_session() -> Session:
    if _SessionLocal is None:
        get_engine()
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    db = new_session()
    try:
        yield db
    finally:
        db.close()
