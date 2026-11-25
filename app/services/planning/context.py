"""What Cortex sees when planning: the question, what retrieval found, and the catalog concepts on the
tables involved. Only governed concepts are offered; raw columns never reach a plan."""

from app.config import get_settings
from app.services.ontology.graph import Ontology
from app.services.retrieval import RetrievalResult


def _name(concept_id: str) -> str:
    return concept_id.split(":", 1)[1]


def build_context(onto: Ontology, r: RetrievalResult, errors: list[str] | None = None) -> dict:
    s = get_settings()
    candidates = []
    tables: set[str] = set()
    for c in r.candidates:
        if not c.governed or c.kind not in ("metric", "dimension", "table"):
            continue
        candidates.append({"name": _name(c.id), "kind": c.kind, "label": c.label, "table": c.table,
                           "exact": any(reason.startswith("matches") for reason in c.reasons), "score": c.score})
        if c.table:
            tables.add(c.table)

    # Tables one approved join away are offered too, mainly for their dates: order lines have no date of
    # their own, but "this year" can still use the order date through the join.
    joins = []
    for _, _, j in onto.joins.edges(data=True):
        left, right = onto.g.nodes[j["left"]]["name"], onto.g.nodes[j["right"]]["name"]
        if left in tables or right in tables:
            joins.append({"left": left, "right": right})
    tables |= {t for j in joins for t in (j["left"], j["right"])}

    metrics = [{"name": m["name"], "label": m["label"], "table": m["props"]["table"], "agg": m["props"]["agg"],
                "format": m["props"]["format"], "description": m["props"].get("description", "")}
               for m in onto.concepts("metric") if m["props"]["table"] in tables]
    dimensions = [{"name": d["name"], "label": d["label"], "table": d["props"]["table"], "column": d["props"]["column"],
                   "type": d["props"]["type"], "time_grains": d["props"].get("time_grains", []),
                   "pii": bool(d["props"].get("pii"))}
                  for d in onto.concepts("dimension") if d["props"]["table"] in tables]
    table_info = [{"name": t["name"], "label": t["label"], "time_column": t["props"].get("time_column")}
                  for t in onto.concepts("table") if t["name"] in tables]
    ctx = {
        "question": r.question,
        "candidates": candidates,
        "values": [{"dimension": _name(v.dimension), "value": v.value, "term": v.term} for v in r.values],
        "metrics": metrics,
        "dimensions": dimensions,
        "tables": table_info,
        "joins": joins,
        "defaults": {"default_months": s.default_months, "limit": s.default_limit, "max_rows": s.max_rows},
    }
    if errors:
        ctx["errors"] = errors
    return ctx
