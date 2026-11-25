"""Operator commands (diagram: Operator / CI).

    python -m app.cli bootstrap [--reset]     seed the graph store from the catalog
    python -m app.cli index [--rebuild]       refresh BigQuery metadata and update the vector index
    python -m app.cli search "question"       show what metadata retrieval finds
    python -m app.cli plan "question"         plan a question; exit code 1 unless the plan is ready
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
        r = retrieve(db, args.question, k=args.k)
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
            o = plan_question(db, args.question)
        except CortexError as exc:
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
    q.set_defaults(func=_search)
    p = sub.add_parser("plan", help="plan a question (retrieval, Cortex, ontology resolution)")
    p.add_argument("question")
    p.add_argument("--json", action="store_true", help="also print the plan JSON")
    p.set_defaults(func=_plan)
    h = sub.add_parser("healthcheck", help="check config, storage, Cortex, BigQuery and ontology completeness")
    h.add_argument("--json", action="store_true", help="print the report as JSON")
    h.set_defaults(func=_healthcheck)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
