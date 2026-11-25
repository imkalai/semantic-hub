---
name: catalog-authoring
description: Build or extend the semantic catalog and ontology for a business area (tables, grain, keys, joins, metrics, dimensions, synonyms) in catalog/semantic_catalog.json, or review a session's inferred items. Use when adding real business definitions, onboarding a new dataset, or explaining semantic-layer concepts.
---

# Authoring the semantic catalog

The curated catalog (`catalog/semantic_catalog.json`) holds **certified** definitions. Sessions reuse them for any
selected table they cover; everything else in a session is declared (from BigQuery) or inferred and needs review.
Work with the business owner on every definition, and explain each idea in plain language.

## Steps

1. **Start from questions.** Collect 10-20 real questions for one business area. Only model what they need.
2. **Tables and grain.** For each table: what one row is ("one row per order line"), its key, its main date and
   whether it's partitioned. Check the key on one recent partition (cheap):
   `SELECT COUNT(*), COUNT(DISTINCT key) FROM t WHERE DATE(date_col) = CURRENT_DATE() - 1`. Equal = unique.
3. **Joins.** For each pair people combine: the key columns and which side is "many"
   (`relationship: many_to_one` when `left` has many rows per `right` row). Only list correct, approved joins;
   wrong direction means wrong double-counting checks.
4. **Metrics.** Agree name, meaning, recipe (`agg`: sum, count, count_distinct, avg, min, max, or ratio of two
   metrics), exclusions (cancelled? refunds?), currency and format with the owner. **Reconcile** against a
   trusted report before marking it done. Start with 5-10.
5. **Dimensions.** Attributes to slice by; time dimensions with allowed `time_grains`; `pii: true` for personal data.
6. **Vocabulary.** Synonyms people actually use ("turnover", "GMV" → revenue). A term may point at one concept only.
7. **Load and check:**
   ```powershell
   .\.venv\Scripts\python -m app.cli bootstrap
   .\.venv\Scripts\python -m app.cli index
   .\.venv\Scripts\python -m app.cli healthcheck   # catches unknown columns, broken references
   ```
8. **Golden questions.** Run the step-1 questions with `python -m app.cli plan "..."`; add synonyms or fix
   definitions until each plans as expected; add them to the table in docs/HANDOFF.md.

## Shape of an entry

```json
{"name": "revenue", "label": "Revenue", "table": "orders", "agg": "sum", "column": "amount",
 "format": "currency", "description": "Total order value", "synonyms": ["sales", "turnover"]}
```

Names are lower_snake_case and unique per kind. `table` refers to a catalog table's `name`, whose `table` field
is the full `project.dataset.table`.

## Reviewing a session instead

Sessions page → open the session → for each inferred item: **Confirm**, **Edit** (label, synonyms, calculation,
relationship, personal-data flag, primary key) or **Reject**. Confirm guessed joins only after checking the keys
and direction as in steps 2-3. Decisions apply to every session over the same tables.

## Pitfalls

Mixing grains (orders vs order lines), vague metric names (gross vs net revenue), unstated date column or time
zone, and skipping reconciliation.
