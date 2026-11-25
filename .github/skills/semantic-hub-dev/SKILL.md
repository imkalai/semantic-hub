---
name: semantic-hub-dev
description: How to make and verify a change in Semantic Hub (FastAPI + SQLite + Chroma semantic layer app) - set up, run, test, check the UI, and keep the docs current. Use for any code change in this repository.
---

# Making a change in Semantic Hub

## 1. Orient

- Read `AGENTS.md` (core ideas, layout, conventions) and `docs/HANDOFF.md` (status and next steps).
- Find the right layer: API (`app/api/`) → services (`app/services/<area>/`) → models/schemas. Follow an existing
  feature end to end before adding a new one (e.g. `POST /api/questions` → `plan_question` → `retrieve`,
  `resolve`, `apply_guardrails`).

## 2. Set up (once)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Behind a corporate proxy add `--index-url <internal PyPI>` (and `--cert <CA bundle>` if needed).

## 3. Change

- Keep demo mode working (sample tables in `app/services/metadata/demo.py`, mock Cortex).
- Pass `scope` through anything touching the graph, index, retrieval, planning or questions.
- New settings: `app/config.py` + `.env.example`. New Cortex tasks: prompt in `prompts.py` + mock behaviour.
- New tables/columns: `app/models.py`; columns on existing tables also go in `_ADDED_COLUMNS` (`app/db.py`).

## 4. Verify

```powershell
.\.venv\Scripts\python -m pytest -q                                   # all tests, ~10 s
.\.venv\Scripts\python -m app.cli healthcheck                         # Ready for runtime
.\.venv\Scripts\python -m app.cli plan "monthly turnover by region"   # quick planning check
.\.venv\Scripts\python run_local.py                                   # UI at http://127.0.0.1:8765
```

- Check the golden questions in `docs/HANDOFF.md` still behave as listed.
- For UI changes: check the page in light and dark themes and at phone width, with no console errors.
- Local data lives in `./data` (SQLite + Chroma). It's safe to delete; the app rebuilds it on start.

## 5. Finish

Update README.md and docs/HANDOFF.md, summarise in plain language, and ask before committing.
