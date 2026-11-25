---
description: "Build Phase 5: run ready plans on BigQuery (compiler, cost guardrail, demo executor, results UI)"
mode: agent
---

Build Phase 5 (Execution on BigQuery) of Semantic Hub, following the design in
[docs/HANDOFF.md](../../docs/HANDOFF.md#next-phase-5-execution-on-bigquery) and the rules in
[AGENTS.md](../../AGENTS.md).

Work in this order, running `.\.venv\Scripts\python -m pytest -q` after each step:

1. `app/services/execution/compiler.py`: `ResolvedPlan` + `Ontology` → SQL and parameters. One CTE per metric part
   along `metric_paths`, final `FULL OUTER JOIN` on the dimensions, ratios with `SAFE_DIVIDE`, time buckets with
   `DATE_TRUNC`, filters and ranges as query parameters only. Add compiler tests first (cases listed in HANDOFF).
2. Executors: `DemoExecutor` (deterministic fake rows, works offline) and `BigQueryExecutor` (dry run for bytes,
   `maximum_bytes_billed`, timeout, row limit). Pick by `SH_METADATA_MODE`.
3. Cost guardrail after compilation: new settings `SH_COST_WARN_GB` and `SH_COST_BLOCK_GB` in config and
   `.env.example`.
4. API `POST /api/questions/{id}/run`, and CLI `python -m app.cli run "question" [--session N] [--sql-only]`.
5. UI on the plan card: Run button, results table formatted by metric format, simple line or bar chart drawn with
   inline SVG, "Show SQL", CSV download. Run the app and check it in light and dark themes and at phone width.
6. Update README.md (architecture table, a section on execution) and docs/HANDOFF.md (status, next steps).

Explain any data-engineering idea you rely on (CTEs, fan-out, partition pruning) in a sentence of plain English
in your summary.
