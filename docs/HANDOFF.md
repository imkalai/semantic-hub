# Handoff: where Semantic Hub stands and what comes next

Last updated: 2026-11-25, at commit "Dynamic sessions: build the ontology from selected BigQuery tables".
Update this file at the end of every phase.

## Status

| Phase | What | State |
|---|---|---|
| 1 | FastAPI skeleton, curated catalog, embedded graph store, bootstrap, healthcheck, mock Cortex, UI shell | Done |
| 2 | Metadata retrieval: SQLite metadata store, Chroma index, exact/value/vector matching | Done |
| 3 | Query planning with Cortex + ontology resolution, retry with reasons, question history | Done |
| 4 | Guardrails: period limits, filter values, no double counting, no personal data, result size | Done |
| A | Dynamic sessions from selected BigQuery tables, trust levels, approvals, guessed-join guardrail, Looker placeholder | Done |
| **5** | **Execution on BigQuery** (SQL compiler, dry-run cost guardrail, results UI) | **Next** |
| 6 | Real Cortex client (chat + embeddings) | Waiting for the corporate client code |
| 7 | Looker source (read explores as certified concepts) and Looker execution route | When Looker is available |
| 8 | Packaging for laptops (installer), shared approvals | Later |

84 tests pass. Demo mode works end to end offline.

## Decisions made, and why

- **No Neo4j.** The app ships to individual laptops, so the ontology graph is stored as nodes/edges in SQLite
  (unique ids, unique edges and foreign keys stand in for Neo4j constraints) and loaded into NetworkX. The
  ontology is small (hundreds to thousands of nodes), so this is plenty.
- **SQLite, no server database, no sign-in.** One user per laptop. The app binds to 127.0.0.1 and refuses other
  Host headers. Writes need the `X-Requested-With: semantic-hub` header.
- **JSON catalog = curated, certified layer.** It seeds the `catalog` scope's graph, is the fallback when that
  graph is unusable, and contributes certified concepts to any session that includes its tables.
- **Sessions instead of pre-built catalogs.** Projects and datasets are too large to model up front. A session
  assembles an ontology from the selected tables' metadata. Strongest source wins: curated > Looker > declared
  (BigQuery keys, policy tags) > inferred (names and types). People's approvals turn inferred items certified.
- **Approvals keyed by BigQuery names**, not sessions, so the app learns an estate over time.
- **Guessed joins block; guessed metrics/dimensions warn.** A wrong join silently changes which rows are combined.
- **Plans contain catalog names only.** The LLM never writes SQL or joins.
- **Each metric gets its own join path** (`ResolvedPlan.metric_paths`) and the double-counting guardrail checks
  each path. This is what makes ratios across tables (e.g. Return rate = returns / orders) correct, and it
  dictates how Execution must build SQL (below).
- **Mock first.** Cortex is mocked (keyword rules that follow the same prompt rules) until the corporate client
  is available; BigQuery has a demo provider. Every feature must work in demo mode.
- **Looker not available yet**; kept as a placeholder source (`app/services/sessions/looker.py`, `SH_LOOKER_MODE=off`).
- **Design language** is Looker Helper's (an earlier internal app). `static/css/app.css` came from it.

## Next: Phase 5, Execution on BigQuery

Goal: a "ready" plan runs on BigQuery and the user sees a table (and a simple chart) of results.

### SQL compiler (`app/services/execution/compiler.py`)

Input: `ResolvedPlan` plus the scope's `Ontology`. Output: SQL text and query parameters.

1. **One CTE per metric part** (`metric_paths`; a ratio contributes its numerator and denominator):
   - `FROM` the metric's table, `JOIN` along its path steps (all many-to-one or one-to-one, which the guardrail
     guarantees; use each step's `on` keys and the tables' full BigQuery names).
   - `SELECT` the group-by dimensions, the time bucket (`DATE_TRUNC(DATE(col), MONTH)` etc. for `time_grain`),
     and the aggregate (`SUM(col)`, `COUNT(*)`, `COUNT(DISTINCT col)`, `AVG`, `MIN`, `MAX`).
   - `WHERE` the filters and the time range, all **parameterized** (`@p0`, `@p1`; never paste values into SQL).
     Relative ranges: `DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL n MONTH)` with `include_current`
     deciding the upper bound.
   - `GROUP BY` the dimensions and the time bucket.
2. **Final select**: `FULL OUTER JOIN` the CTEs on the dimension columns (`USING`, or `COALESCE` for keys), compute
   ratios as `SAFE_DIVIDE(numerator, denominator)`, apply `ORDER BY` (`time` = the bucket) and `LIMIT`.
3. Use readable aliases (catalog names), quote identifiers with backticks, never select personal-data columns.

### Execution service (`app/services/execution/`)

- `estimate(plan)`: BigQuery **dry run** (`MetadataProvider.dry_run_bytes` already exists) → bytes read.
- New guardrail `query_cost`: warn above `SH_COST_WARN_GB`, block above `SH_COST_BLOCK_GB` (new settings). It
  runs after compilation, so add it in the execution step rather than in `apply_guardrails`.
- `run(plan)`: `google-cloud-bigquery` query with `maximum_bytes_billed` set, a timeout, and the row limit.
  Credentials: the user's own Application Default Credentials (`gcloud auth application-default login`).
- **Demo mode**: a demo executor returns deterministic fake rows of the right shape (seeded by the plan), so
  the UI and tests work offline.
- Executor interface with two implementations now (`BigQueryExecutor`, `DemoExecutor`) and a third later
  (`LookerExecutor`). A router picks Looker only when every concept in the plan came from one Looker explore.

### API and UI

- `POST /api/questions/{id}/run` → `{columns, rows, sql, bytes_estimated, bytes_billed, ms}`; save a summary of
  the run with the question (not the rows).
- Plan card: **Run** button when status is ready; results table (formatted by metric `format`), a line chart
  when there's a time grain, a bar chart with one dimension; "Show SQL" disclosure; download as CSV (client side).
- CLI: `python -m app.cli run "question" [--session N] [--sql-only]`.

### Tests to add

Compiler output for: single metric; metric + dimension through a join; ratio across two tables (two CTEs);
time grain and relative range; filters as parameters (no values in SQL text); top N. Demo executor shape.
Cost guardrail thresholds. API run flow.

## Other open items

- **Real Cortex** (`app/services/cortex/real.py`): implement `chat()` and `embed()` with the corporate client,
  set `SH_CORTEX_MODE=real`, then tune `SH_RETRIEVAL_MIN_SIMILARITY` and rebuild indexes. The planning prompt is
  in `prompts.py`; the mock planner shows the expected behaviour.
- **Live BigQuery**: `pip install google-cloud-bigquery`, `SH_METADATA_MODE=live`. Untested against a real project
  so far: check `live.py` (metadata, `get_constraints` via INFORMATION_SCHEMA) on a real dataset first.
- **Looker** (when available): fill in `LookerSource.items()`; map explores' measures, dimensions, dimension groups
  and joins (with `relationship`) to certified catalog items; tie views to tables via `sql_table_name`.
- **Small gaps**: rename a session; sample values for dimensions in live mode (Dataplex profiles or a tiny
  sample from the latest partition, never personal data); a migration tool once the schema changes often;
  index on `questions.scope` isn't created for databases upgraded in place (fine at current sizes).
- **Packaging**: a Windows installer or zipped venv with `run_local.py` as the entry point; data folder under
  `%LOCALAPPDATA%\SemanticHub` (`SH_DATA_DIR`).

## Golden questions (demo data)

Use these to check nothing regressed, in the curated catalog scope unless noted. Expected outcomes are with the
mock Cortex; the real one should do at least as well.

| Question | Expected |
|---|---|
| Monthly turnover by region for web orders | Ready: Revenue by Customer region, per month, last 12 months, Sales channel = web |
| Average basket size in the North last quarter | Ready: Average order value, last complete quarter, Customer region = North |
| How many cancellations by churn reason | Ready: Churned accounts by Churn reason |
| Top 5 product categories by product revenue this year | Ready: top 5, order date reached through order lines → orders |
| Units sold by territory | Ready, joins order lines → orders → customers → regions; warning: order lines table has no partition date |
| AOV by channel | Ready, adjusted to the last 12 months (orders is a large partitioned table) |
| Revenue by product category | Blocked: double counting; suggests Product revenue |
| Revenue by customer email | Ready without the email (personal data left out, stated as an assumption) |
| Churn by order channel | Couldn't plan: no approved join between churn events and orders |
| Stock in the warehouse | Needs detail: no measure in the catalog |
| (session over `acme-ops.warehouse.*`) Total stock level units on hand by warehouse city | Blocked until the guessed join stock_levels → warehouses is confirmed, then ready |

## How a session works (quick reference)

1. `create_session(name, tables)` → `build_session`: refresh table metadata, read declared keys.
2. `assemble()` merges sources (curated, Looker placeholder, BigQuery, inference) and applies approvals.
3. The merged catalog is stored on the session row (its fallback), synced into the graph under
   `session:<id>`, and indexed into Chroma collection `session_<id>`.
4. A fingerprint (schemas, keys, curated catalog, relevant approvals) skips rebuilds when nothing changed.
5. `decide(key, approved|rejected|reset, changes)` records a decision and rebuilds affected sessions.
