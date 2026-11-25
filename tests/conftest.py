import json

import pytest

from app.config import BASE_DIR, get_settings
from app.db import init_db, new_session, reset_engine
from app.services import ontology

CATALOG = BASE_DIR / "catalog" / "semantic_catalog.json"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test gets its own data folder (SQLite + Chroma) and fresh settings/caches."""
    monkeypatch.setenv("SH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SH_METADATA_MODE", "demo")
    monkeypatch.setenv("SH_CORTEX_MODE", "mock")
    monkeypatch.delenv("SH_CATALOG_PATH", raising=False)
    get_settings.cache_clear()
    reset_engine()
    ontology.invalidate()
    yield
    reset_engine()
    ontology.invalidate()
    get_settings.cache_clear()


@pytest.fixture
def db():
    init_db()
    with new_session() as session:
        yield session


@pytest.fixture
def catalog_data() -> dict:
    return json.loads(CATALOG.read_text())


@pytest.fixture
def write_catalog(tmp_path, monkeypatch):
    """Point the app at an edited copy of the catalog."""

    def write(data: dict):
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps(data))
        monkeypatch.setenv("SH_CATALOG_PATH", str(path))
        get_settings.cache_clear()
        ontology.invalidate()
        return path

    return write
