# AGENTS.md: working on Semantic Hub

Instructions for AI coding assistants (GitHub Copilot, Claude Code and others) and for people picking up this
codebase. Read this first, then [docs/HANDOFF.md](docs/HANDOFF.md) for where the work stands and what comes next.

## What this is

Semantic Hub answers business questions about BigQuery data through a governed semantic layer. A question
like "monthly turnover by region for web orders" goes through:

```
Semantic Layer API (FastAPI)
  → Metadata Retrieval   SQLite metadata store + Chroma vector index (Cortex embeddings)
  → Query Planning       Cortex LLM writes a plan in catalog terms only (no tables, joins or SQL)
  → Ontology Resolution  names checked against the ontology graph; approved join paths found
  → Guardrails           period limits, no double counting, no personal data, confirmed joins
  → Execution            NOT BUILT YET: BigQuery SQL (later: Looker)
```

It's a **local, single-user app**: one Python process per laptop, no sign-in, answers only on 127.0.0.1.
Everything it stores is in a data folder (`SH_DATA_DIR`, default `./data`).

## Run, test, check

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q                  # 84 tests, ~10 s, no network needed
.\.venv\Scripts\python run_local.py                  # http://127.0.0.1:8765
.\.venv\Scripts\python -m app.cli healthcheck        # "Ready for runtime" when all is well
```

The defaults are **demo mode**: built-in sample BigQuery tables (`app/services/metadata/demo.py`) and a
mock Cortex (`app/services/cortex/mock.py`, `mock_planner.py`). Everything works offline.

Always run the full test suite after a change, and add tests for new behaviour. For UI changes, run the app
and click through the affected page. Behind a corporate proxy, pip may need `--index-url <internal PyPI>`.

## Layout

| Path | What |
|---|---|
| `app/main.py` | FastAPI app, security middleware (loopback-only Host check, `X-Requested-With: semantic-hub` on writes, CSP), first-run bootstrap and index |
| `app/config.py` | All settings (`SH_` env vars or `.env`); see `.env.example` |
| `app/models.py`, `app/db.py` | SQLite tables; `_upgrade_sqlite()` handles schema changes (no Alembic yet) |
| `app/schemas/` | `catalog.py` (catalog model + trust), `plan.py` (QueryPlan, ResolvedPlan), `meta.py` (BigQuery metadata) |
| `app/services/catalog/` | Load and validate the JSON catalog; `graph_spec()` turns a catalog into nodes and edges |
| `app/services/ontology/` | Embedded graph store (SQLite nodes/edges per scope), `Ontology` (NetworkX), bootstrap, completeness, fallback |
| `app/services/retrieval/` | Metadata store, document builder, Chroma index per scope, retriever (exact terms, values, vectors) |
| `app/services/planning/` | Cortex context, plan parsing, retry, `resolve.py` (checks, join paths per metric, summary) |
| `app/services/guardrails/` | Rules that adjust, warn or block a resolved plan |
| `app/services/sessions/` | Ontology built on demand from selected BigQuery tables; approvals; Looker placeholder |
| `app/services/cortex/` | LLM client: `base.py` interface, `mock.py`, `real.py` (stub for the corporate client), `prompts.py` |
| `app/services/metadata/` | BigQuery metadata: `demo.py`, `live.py`, PII detection |
| `app/services/health/` | Healthcheck used by the CLI and the Health page |
| `app/cli.py` | `bootstrap`, `index`, `search`, `plan`, `session`, `healthcheck` |
| `catalog/semantic_catalog.json` | Curated catalog (certified concepts) |
| `static/` | Plain HTML + CSS + ES modules. No npm, no build step |
| `tests/` | pytest; `conftest.py` gives each test its own data folder |

## Core ideas (don't break these)

- **Scopes.** Every graph, index and question row belongs to a scope: `catalog` (the curated catalog) or
  `session:<id>`. Functions take `scope` (default `catalog`). Scopes never see each other's rows.
- **Trust levels** on every concept and join: `certified` (curated catalog, Looker, or a person's approval),
  `declared` (BigQuery keys, policy tags), `inferred` (guessed). Guessed joins block a plan until confirmed.
- **Approvals** are keyed by BigQuery names, not by session (`join:proj.ds.a:proj.ds.b:col=col`,
  `metric:proj.ds.t:column:agg`, `dimension:proj.ds.t:column`, `table:proj.ds.t`), so they carry across sessions.
- **Plans name catalog concepts only.** The LLM never writes tables, joins or SQL; resolution and (later)
  execution derive those from the ontology, so the LLM can't invent a join or column.
- **Each metric is checked on its own path** to the plan's dimensions (`ResolvedPlan.metric_paths`). Crossing a
  join from the "one" side to the "many" side repeats rows, so sums/counts/averages are blocked there.
  Execution must therefore aggregate **each metric on its own table** and then line results up by the
  dimensions (see docs/HANDOFF.md).
- **Metadata only.** Reading BigQuery never reads table data, except the future Execution step, which runs
  the user's query. Never send table data or personal data to Cortex.
- **Personal data** (policy tags or column-name rules) is never sampled, and can't be grouped by or filtered
  on unless `SH_ALLOW_PII=true`.
- **The graph store replaces Neo4j** (the original design) because the app ships to individual laptops.
  Keep it behind `SqliteGraphStore` so another store could replace it.

## Conventions

- Python 3.11+, type hints, small focused modules. Errors the user can fix are raised as specific exceptions
  (`CatalogError`, `SessionError`, `MetadataError`, `CortexError`) with messages written for a business user.
- API: JSON under `/api/`. Writes need the `X-Requested-With: semantic-hub` header (the frontend's `api()`
  adds it). Scope parameters are validated with the pattern `^(catalog|session:\d{1,9})$`.
- Frontend: the **Looker Helper design language**: tokens and components in `static/css/app.css` (shared
  with Looker Helper; keep its tokens), app-specific styles in `static/css/hub.css`. Dark theme = black
  backgrounds with green accents. Every page renders into its own container (see `route()` in
  `static/js/main.js`) so late async results can't overwrite a newer page. Escape all text with `esc()`.
- User-facing text: plain English, short sentences, no jargon without a word of explanation.
- Don't add a build step, a JS framework, a server database or anything needing installation beyond pip.
- Commits: one topic per commit, imperative summary line, bullets in the body.

## Working with the maintainer

Explain data-engineering ideas (grain, joins, fan-out, partitions, semantic layers) in plain language with
concrete examples from the demo catalog (orders, customers, revenue). Propose, then build in phases; keep
the README and docs/HANDOFF.md up to date as part of every phase.
