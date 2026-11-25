# Copilot instructions for Semantic Hub

Before changing anything, read:

1. [AGENTS.md](../AGENTS.md): what the app is, layout, core ideas that must not break, conventions.
2. [docs/HANDOFF.md](../docs/HANDOFF.md): what's done, decisions and why, and the detailed plan for the next phase
   (Phase 5: Execution on BigQuery).

Essentials:

- Local single-user FastAPI app with SQLite, NetworkX and Chroma. Plain HTML/CSS/JS frontend, no build step.
- Everything must work in demo mode (sample BigQuery tables, mock Cortex) and offline.
- Every graph, index and question row belongs to a scope: `catalog` or `session:<id>`. Pass `scope` through.
- Concepts and joins carry a trust level (certified, declared, inferred). Guessed joins block plans until confirmed.
- LLM plans use catalog names only; never let Cortex write SQL, tables or joins.
- Execution must aggregate each metric on its own table along `ResolvedPlan.metric_paths`, then line results up
  by the dimensions. Filter values are always query parameters.
- Never read table data except when executing the user's query; never send table data or personal data to Cortex.
- Run `.\.venv\Scripts\python -m pytest -q` after every change and add tests. For UI changes, run
  `run_local.py` and check the page.
- Keep README.md and docs/HANDOFF.md current at the end of each phase.
- Explain data-engineering concepts in plain language with examples when asked.
