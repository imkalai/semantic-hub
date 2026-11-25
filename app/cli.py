"""Operator commands (diagram: Operator / CI).

    python -m app.cli bootstrap [--reset]     seed the graph store from the catalog
    python -m app.cli index [--rebuild]       refresh BigQuery metadata and update the vector index
    python -m app.cli search "question"       show what metadata retrieval finds
    python -m app.cli plan "question"         plan a question; exit code 1 unless the plan is ready
    python -m app.cli session create --name N --tables p.d.t1,p.d.t2   build a session from BigQuery tables
    python -m app.cli session list | build <id> | review <id>
    (search and plan take --session <id> to work in a session)
    python -m app.cli healthcheck [--json]    run every check; exit code 1 when not ready
"""

import argparse
import json
import logging
import sys

from app.db import init_db, new_session
from app.services.catalog import CatalogError
from app.services.cortex import CortexError
from app.services.health import run_healthcheck
from app.services.metadata import MetadataError
from app.services.ontology import bootstrap
from app.services.planning import plan_question
from app.services.retrieval import VectorIndexError, build_index, retrieve

MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def _bootstrap(args) -> int:
    init_db()
    with new_session() as db:
        try:
            steps = bootstrap(db, reset=args.reset)
        except CatalogError as exc:
            print(f"Bootstrap failed: {exc}", file=sys.stderr)
            return 1
    print("Bootstrap finished" + (" (graph reset first)" if args.reset else ""))
    for s in steps:
        extra = f"  - {s.detail}" if s.detail else ""
        print(f"  {s.name:<22} +{s.added} ~{s.updated} -{s.removed}{extra}")
    return 0


def _scope(args) -> str:
    return f"session:{args.session}" if getattr(args, "session", None) else "catalog"


def _session(args) -> int:
    from app.models import HubSession
    from app.services.sessions import SessionError, build_session, create_session, review_items

    init_db()
    with new_session() as db:
        try:
            if args.action == "create":
                tables = [t for t in (args.tables or "").split(",") if t.strip()]
                s = create_session(db, args.name or "", tables)
            elif args.action == "list":
                for s in db.query(HubSession).order_by(HubSession.id):
                    print(f"  {s.id:>3}  {s.status:<7} {s.name}  ({len(s.tables)} tables)")
                return 0
            else:
                s = db.get(HubSession, args.id or 0)
                if s is None:
                    print("No session with that id.", file=sys.stderr)
                    return 1
                if args.action == "build":
                    s = build_session(db, s, force=args.force)
                elif args.action == "review":
                    for r in review_items(s):
                        print(f"  {r['kind']:<9} {r['trust']:<9} {r['label']:<40} {r['detail']}")
                        print(f"            key: {r['key']}")
                    return 0
        except SessionError as exc:
            print(f"Session failed: {exc}", file=sys.stderr)
            return 1
        c = s.report.get("counts", {})
        print(f"Session {s.id} '{s.name}': {s.status}")
        for kind, by in c.items():
            print(f"  {kind:<11} certified {by['certified']:>3} · declared {by['declared']:>3} · inferred {by['inferred']:>3}")
        for n in s.report.get("notes", []):
            print(f"  {n}")
        print(f"  Ask with: python -m app.cli plan \"...\" --session {s.id}")
    return 0


def _index(args) -> int:
    init_db()
    with new_session() as db:
        try:
            r = build_index(db, rebuild=args.rebuild, refresh=not args.no_refresh)
        except (CatalogError, MetadataError, CortexError, VectorIndexError) as exc:
            print(f"Indexing failed: {exc}", file=sys.stderr)
            return 1
    print("Metadata index " + ("rebuilt" if r.rebuilt else "updated"))
    print(f"  BigQuery tables refreshed  {r.tables_refreshed}")
    print(f"  Documents                  {r.documents} ({r.embedded} embedded, {r.unchanged} unchanged, {r.removed} removed)")
    print(f"  Embeddings                 {r.signature}")
    print(f"  Ontology read from         {r.ontology_source}")
    return 0


def _search(args) -> int:
    init_db()
    with new_session() as db:
        r = retrieve(db, args.question, k=args.k, scope=_scope(args))
    print(f"{r.mode} · ontology: {r.ontology_source}" + (f" · {r.note}" if r.note else ""))
    for c in r.candidates:
        flags = ("" if c.governed else " [not in catalog]") + (" [personal data]" if c.pii else "")
        print(f"  {c.score:4.2f}  {c.id:<40}{flags}  {'; '.join(c.reasons)}")
    if r.unmatched:
        print(f"  Not recognised: {', '.join(r.unmatched)}")
    return 0


def _plan(args) -> int:
    init_db()
    with new_session() as db:
        try:
            o = plan_question(db, args.question, _scope(args))
        except (CortexError, CatalogError) as exc:
            print(f"Planning failed: {exc}", file=sys.stderr)
            return 1
    print(f"{o.status} · {len(o.attempts)} attempt(s) · cortex: {o.cortex}")
    if o.resolved:
        print(f"  {o.resolved.summary}")
        print(f"  Tables: {', '.join(o.resolved.tables)}")
        for j in o.resolved.joins:
            print(f"  Join:   {j.name}")
    elif o.clarification:
        print(f"  {o.clarification}")
    for e in o.errors:
        print(f"  Error:  {e}")
    for a in (o.plan.assumptions if o.plan else []):
        print(f"  Assumed: {a}")
    if args.json and o.plan:
        print(o.plan.model_dump_json(indent=2, exclude_defaults=True))
    return 0 if o.status == "ready" else 1


def _healthcheck(args) -> int:
    init_db()
    with new_session() as db:
        report = run_healthcheck(db)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        group = None
        for c in report["checks"]:
            if c["group"] != group:
                group = c["group"]
                print(f"\n{group}")
            print(f"  [{MARK[c['status']]}] {c['name']}: {c['detail']}")
            if c["fix"] and c["status"] != "ok":
                print(f"         -> {c['fix']}")
        print(f"\n{report['verdict']}")
    return 0 if report["ready"] else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Windows consoles may not use UTF-8; never crash on a curly quote in the output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Semantic Hub operator commands")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bootstrap", help="seed the graph store from the semantic catalog")
    b.add_argument("--reset", action="store_true", help="wipe the graph before seeding")
    b.set_defaults(func=_bootstrap)
    i = sub.add_parser("index", help="refresh BigQuery metadata and update the vector index")
    i.add_argument("--rebuild", action="store_true", help="drop the vector index and embed everything again")
    i.add_argument("--no-refresh", action="store_true", help="use the saved metadata instead of reading BigQuery")
    i.set_defaults(func=_index)
    q = sub.add_parser("search", help="show what retrieval finds for a question")
    q.add_argument("question")
    q.add_argument("-k", type=int, default=8)
    q.add_argument("--session", type=int, help="search a session instead of the curated catalog")
    q.set_defaults(func=_search)
    p = sub.add_parser("plan", help="plan a question (retrieval, Cortex, ontology resolution, guardrails)")
    p.add_argument("question")
    p.add_argument("--session", type=int, help="plan in a session instead of the curated catalog")
    p.add_argument("--json", action="store_true", help="also print the plan JSON")
    p.set_defaults(func=_plan)
    se = sub.add_parser("session", help="create, list, rebuild or review sessions built from BigQuery tables")
    se.add_argument("action", choices=["create", "list", "build", "review"])
    se.add_argument("id", type=int, nargs="?", help="session id (build, review)")
    se.add_argument("--name", help="create: session name")
    se.add_argument("--tables", help="create: comma-separated project.dataset.table names")
    se.add_argument("--force", action="store_true", help="build: rebuild even if nothing changed")
    se.set_defaults(func=_session)
    h = sub.add_parser("healthcheck", help="check config, storage, Cortex, BigQuery and ontology completeness")
    h.add_argument("--json", action="store_true", help="print the report as JSON")
    h.set_defaults(func=_healthcheck)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
