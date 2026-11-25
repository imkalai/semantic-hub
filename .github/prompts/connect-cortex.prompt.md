---
description: "Connect the corporate Cortex client (chat + embeddings) and tune retrieval"
mode: agent
---

Connect the real Cortex client in Semantic Hub.

1. Read `app/services/cortex/base.py`, `real.py`, `prompts.py` and `mock_planner.py` (the mock shows the expected
   planning behaviour). Ask me for the corporate client code or its documentation if it isn't in the repo.
2. Implement `RealCortex.chat()` and `RealCortex.embed()` with that client, using the `SH_CORTEX_*` settings.
   Turn client errors into `CortexError` with a message a business user understands. Batch embeddings.
3. Never send table data or personal data to Cortex: only questions, catalog names, labels and descriptions.
4. Keep demo mode working: tests must still pass with `SH_CORTEX_MODE=mock`.
5. With `SH_CORTEX_MODE=real`: run `python -m app.cli healthcheck`, `python -m app.cli index --rebuild`, then try
   `python -m app.cli search` and `plan` on the golden questions in docs/HANDOFF.md and README examples.
   Suggest a value for `SH_RETRIEVAL_MIN_SIMILARITY` from what you see.
6. Update README.md ("Connecting the real services") and docs/HANDOFF.md.
