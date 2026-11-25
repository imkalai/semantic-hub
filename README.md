# Semantic Hub

Ask questions about your data in business terms. Semantic Hub maps words to governed metrics, dimensions and approved joins (the ontology), plans the query with Cortex, checks it against guardrails, and runs it on BigQuery. Later, the same ontology maps to a Looker semantic layer.

It runs on your own laptop: one Python process, with everything it stores in a local data folder. There is nothing else to install (no Neo4j, Docker or database server) and no sign-in, so it only answers on `127.0.0.1`.

- **Backend:** Python, FastAPI, SQLite (SQLAlchemy), NetworkX, Chroma
- **Frontend:** plain HTML, CSS and JavaScript modules in `static/`, in the Looker Helper design language. No npm and no build step.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python run_local.py          # opens http://127.0.0.1:8765
```

On first start the graph store is seeded from the catalog automatically. The defaults use **demo mode**: built-in sample BigQuery tables and a stand-in for Cortex, so everything works without Google Cloud or Cortex access.

## Operator commands

```powershell
.\.venv\Scripts\python -m app.cli bootstrap            # seed the graph store from the catalog (safe to rerun)
.\.venv\Scripts\python -m app.cli bootstrap --reset    # wipe the graph first
.\.venv\Scripts\python -m app.cli index                # refresh BigQuery metadata, embed new/changed documents
.\.venv\Scripts\python -m app.cli index --rebuild      # drop the vector index and embed everything again
.\.venv\Scripts\python -m app.cli search "turnover by region"   # show what retrieval finds
.\.venv\Scripts\python -m app.cli plan "monthly turnover by region" --json   # plan a question
.\.venv\Scripts\python -m app.cli healthcheck          # exit code 1 when not ready; --json for CI

# Sessions: an ontology built from the BigQuery tables you pick
.\.venv\Scripts\python -m app.cli session create --name "Ops" --tables acme-ops.warehouse.stock_levels,acme-ops.warehouse.warehouses
.\.venv\Scripts\python -m app.cli session list
.\.venv\Scripts\python -m app.cli session review 1     # what was inferred, with approval keys
.\.venv\Scripts\python -m app.cli session build 1 --force
.\.venv\Scripts\python -m app.cli plan "total stock level units on hand by warehouse city" --session 1
```

**Index** reads the details of every catalog table from BigQuery (metadata only, never table data) into the SQLite metadata store. It then writes one short document per metric, dimension and table, plus one per raw column the catalog doesn't cover yet (keys left out, and never sample values of personal-data columns). Only new or changed documents are embedded with Cortex and stored in Chroma. If the embedding model changes, the index is rebuilt.

**Bootstrap** creates the graph constraints, then seeds metrics, dimensions (with tables), approved joins and synonyms. It adds what's missing, updates what changed and removes what the catalog no longer lists.

**Healthcheck** runs four groups of checks:

1. Config / SQLite / Chroma: data folder, catalog file, database, vector index, and whether the metadata index is complete and up to date
2. Cortex / BigQuery: Cortex answers, and every catalog table and column exists in BigQuery
3. Graph store: the graph can be read
4. Ontology completeness: every concept and link in the catalog is in the graph

All passing: **Ready for runtime**. Otherwise: **Repair graph or rerun bootstrap**. The Health page in the app shows the same report.

## Architecture

| Box in the design | Where | Status |
|---|---|---|
| Semantic Layer API | `app/main.py`, `app/api/` | Phase 1 |
| Ontology graph (Neo4j in the original design) | `app/services/ontology/`: SQLite node/edge tables + NetworkX | Phase 1 |
| JSON catalog fallback | `catalog/semantic_catalog.json`, `app/services/catalog/` | Phase 1 |
| Bootstrap + healthcheck | `app/cli.py`, `app/services/health/` | Phase 1 |
| Cortex (embeddings + LLM) | `app/services/cortex/`: mock now, `real.py` to fill in | Phase 1 (mock) |
| BigQuery metadata | `app/services/metadata/` (from Looker Helper) | Phase 1 |
| Metadata retrieval (Chroma + SQLite) | `app/services/retrieval/`, `/api/retrieval/*` | Phase 2 |
| Query planning (Cortex LLM) + ontology resolution | `app/services/planning/`, `/api/questions` | Phase 3 |
| Validation and guardrails | `app/services/guardrails/` | Phase 4 |
| Dynamic sessions (ontology from selected BigQuery tables), approvals | `app/services/sessions/`, `/api/sessions`, `/api/approvals` | Phase A |
| Looker source | `app/services/sessions/looker.py` | Placeholder |
| Execution (BigQuery) | | Next |
| Ontology to Looker mapping | | Later |

**Why not Neo4j:** the app ships to individual laptops. An ontology of metrics, dimensions, joins and synonyms is small, so nodes and edges in SQLite (unique ids, unique edges and foreign keys stand in for Neo4j constraints) loaded into NetworkX for traversal are enough. The graph sits behind `SqliteGraphStore`, so another store can replace it later.

**Fallback:** if the graph store is empty, unreadable or doesn't match the catalog, the app builds the ontology straight from the JSON catalog and says so (Catalog page, `/api/catalog` → `summary.source`).

## Sessions: ontologies built on demand

A hand-written catalog can't cover every project and dataset. A **session** builds the ontology for the BigQuery tables someone picks, from their metadata only, so table size doesn't matter. Pick them on the **Sessions** page, then choose the session in **Working in** at the top to ask questions in it.

**Where a session's concepts come from**, strongest first:

| Source | Gives | Trust |
|---|---|---|
| Curated catalog (`catalog/semantic_catalog.json`) | Its metrics, dimensions and joins, for the selected tables it covers | certified |
| Looker | Placeholder, off (`SH_LOOKER_MODE=off`). See `app/services/sessions/looker.py`. | certified, later |
| BigQuery | Tables, partition dates, declared primary and foreign keys, personal-data policy tags | declared |
| Inference | A row count per table; sums or averages of numeric columns (money and percent spotted by name); dimensions for text, yes/no and date columns; joins where a column matches another table's key | inferred |
| Approvals | People's decisions on inferred items | certified |

The same thing (a metric on a column with an aggregation, a dimension on a column, a join between two tables) is kept once, from the strongest source. Keys aren't offered as dimensions or measures.

**Review.** Each session's page lists what isn't certified. You can **Confirm**, **Edit** (label, synonyms, calculation, relationship, personal-data flag, primary key) or **Reject** each item. Decisions are keyed by BigQuery names (e.g. `join:proj.ds.accounts:proj.ds.regions:region=region`), so they apply to every session over the same tables, now and later, and outlive the session.

**Guardrails for guesses:**
- A plan that uses a **guessed join is blocked** until the join is confirmed: the join decides which rows are combined. The plan card has a **Confirm** button for it.
- Guessed metrics and dimensions get a **warning** with their definition.

**Caching.** A fingerprint of the tables' schemas and last-modified times, declared keys, the curated catalog and the relevant approvals is stored. **Check for changes** rebuilds only when it differs. Each session has its own graph (`ontology_nodes` / `ontology_edges` with `scope = session:<id>`) and its own Chroma collection. The curated catalog is the scope `catalog`.

**Tip:** declaring primary and foreign keys in BigQuery (they're declared, not enforced) turns guessed joins into declared ones that need no confirmation.

## Metadata retrieval

For a question, retrieval returns the concepts it's about, using three signals, strongest first:

1. **Exact terms:** a phrase is a concept's name, label or synonym, also in the singular. The longest phrase wins, so "number of orders" is the metric, not the Orders table.
2. **Values:** a word is a known value of a dimension ("north" → Customer region = North). These become suggested filters.
3. **Meaning:** vector search in Chroma, for wording the catalog doesn't list. Raw columns rank below catalog concepts, and anything with personal data is flagged.

If Chroma or Cortex can't be used, retrieval still answers from exact terms and values, and says so. The Home page shows the result for a question. Planning a query from it comes next.

The mock embeddings are hashed word bags. They're good enough to exercise the pipeline, but matching by meaning only becomes useful with real Cortex embeddings. When switching, tune `SH_RETRIEVAL_MIN_SIMILARITY` and run `index` (the change of model triggers a rebuild).

## Query planning

A question goes through retrieval, then Cortex writes a **query plan** (`app/schemas/plan.py`). The plan uses catalog names only: metrics, dimensions, a time dimension with grain and range, filters, ordering and a row limit. It never contains tables, joins or SQL. The prompt is in `app/services/cortex/prompts.py`. Cortex sees the question, what retrieval found, and the concepts on those tables and the tables one approved join away.

**Ontology resolution** then checks the plan:
- every name exists, time grains are allowed, filters have the right number of values, and the limit is within `SH_MAX_ROWS`
- the base table is the first metric's table, and approved join paths reach every other table it needs

A rejected plan goes back to Cortex once, with the reasons. The outcome is one of:
- **ready**: the plan, a plain-English summary, tables and joins
- **clarify**: Cortex needs more detail, such as which measure
- **invalid**: the reasons it still failed

Every question is saved in SQLite and listed under Recent questions on Home.

In mock mode, keyword rules (`app/services/cortex/mock_planner.py`) follow the same rules as the prompt: grains like "monthly", ranges like "last 3 months" or "this year", "top N", "excluding X", "how many …". Personal-data dimensions are left out and listed as an assumption.

## Guardrails

Every resolved plan goes through guardrails before it can run. Some rules adjust the plan and say so; the rest pass, warn or block:

| Rule | What it does |
|---|---|
| Large tables limited to a period | A metric's table over `SH_LARGE_TABLE_GB` with no period is limited to the last `SH_DEFAULT_MONTHS` months of its partition date (**adjusted**). If the catalog has no partitioned date for it, or the period is on a different date column, it **warns** that the whole table will be read. |
| Filter values | Values are matched to the spelling in the data, e.g. "north" → "North" (**adjusted**). Values not among the known ones **warn**. |
| Approved joins only | Reports the joins used. Plans can only use approved joins anyway. |
| No double counting | Each metric (each part of a ratio) is checked on its own path to the plan's dimensions and filters. Crossing a join from the "one" side to the "many" side repeats rows, so sums, counts and averages are **blocked**, with metrics that would work suggested instead. Distinct counts, min and max are safe. |
| No personal data | Grouping by or filtering on a personal-data dimension is **blocked** unless `SH_ALLOW_PII=true`. |
| Result size | Reports the row limit, and **warns** above `SH_MAX_GROUP_BY` dimensions. |

A blocked plan is saved with status **blocked** and shows why. The guardrails are listed on every plan.

Execution will compute each metric on its own table and then line the results up by the dimensions, which is why the no-double-counting rule checks each metric's own path. An estimate of the BigQuery cost (dry run) needs the SQL, so it comes with the Execution phase.

## The catalog

`catalog/semantic_catalog.json` lists:
- **tables**: BigQuery `project.dataset.table`, primary key, time column
- **metrics**: `sum`, `count`, `count_distinct`, `avg`, `min`, `max`, or `ratio` of two metrics
- **dimensions**: column, type, time grains, personal-data flag
- **approved joins**
- **synonyms**: on each concept, plus extra global terms

Loading rejects unknown references, duplicate names and synonyms that point at two concepts. The demo catalog covers the built-in sales and CRM demo tables.

## Connecting the real services

| What | How |
|---|---|
| **BigQuery** | `pip install google-cloud-bigquery`, set `SH_METADATA_MODE=live`, and sign in with `gcloud auth application-default login`. |
| **Cortex** | Add the corporate client call in `app/services/cortex/real.py` (`chat()` and `embed()`), then set `SH_CORTEX_MODE=real` and the `SH_CORTEX_*` settings. |

All settings are listed in `.env.example`.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```
