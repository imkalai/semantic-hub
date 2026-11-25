---
applyTo: "app/**/*.py,tests/**/*.py,run_local.py"
---

# Python in Semantic Hub

- Python 3.11+, type hints everywhere. Pydantic v2 models for data shapes,
  SQLAlchemy 2.0 style (`select()`, `Mapped[...]`) for the database.
- Settings only via `app.config.get_settings()` (env prefix `SH_`). Add every new setting to `.env.example`
  with a comment. Tests change settings with `monkeypatch.setenv(...)` then `get_settings.cache_clear()`.
- Functions that touch the graph, index, retrieval, planning or questions take `scope: str = CATALOG_SCOPE`.
  Call `app.services.ontology.invalidate(scope)` after changing a scope's graph.
- Raise the module's own exception (`CatalogError`, `SessionError`, `MetadataError`, `CortexError`,
  `VectorIndexError`) with a message a business user can act on; API layers turn them into 400/503 responses.
- Schema changes: add tables/columns in `app/models.py`; for columns on existing tables, extend
  `_ADDED_COLUMNS` in `app/db.py` (no Alembic yet). Graph and index tables are derived data and may be rebuilt.
- BigQuery: metadata calls only (`app/services/metadata/`), except query execution. SQL text must never contain
  user-supplied values; use query parameters.
- Cortex: go through `get_cortex()`. Every new task needs a prompt in `prompts.py` and matching behaviour in the
  mock, so demo mode and tests keep working.
- Tests: pytest, one data folder per test (see `tests/conftest.py`). Prefer end-to-end service tests on the demo
  tables over mocks. Keep the suite fast (seconds) and offline.
