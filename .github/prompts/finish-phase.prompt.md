---
description: "Wrap up a phase: tests, app check, docs, commit"
mode: agent
---

Wrap up the current piece of work on Semantic Hub:

1. Run `.\.venv\Scripts\python -m pytest -q`; fix failures. Make sure new behaviour has tests.
2. If the UI changed, start `run_local.py`, click through the affected pages (light and dark theme), and fix
   anything broken.
3. Update README.md where behaviour, commands or settings changed, `.env.example` for new settings, and
   docs/HANDOFF.md (status table, decisions with their reasons, next steps).
4. Summarise for me in plain language: what changed, what was checked, what's not done.
5. Ask before committing. Commit with an imperative summary line and bullet points in the body.
