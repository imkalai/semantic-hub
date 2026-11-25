"""Ontology resolution of a plan: check every name against the ontology, then work out the base table and
the approved joins that connect everything. A plan that can't be resolved is sent back with reasons."""

from app.config import get_settings
from app.schemas.plan import JoinStep, MetricPath, PathStep, QueryPlan, ResolvedPlan
from app.services.ontology.graph import Ontology

NO_VALUES = {"is_null", "is_not_null"}
ONE_VALUE = {"equals", "not_equals", "contains", "gt", "gte", "lt", "lte"}


def _concept(onto: Ontology, kind: str, name: str) -> dict | None:
    return onto.node(f"{kind}:{name}")


def check_plan(onto: Ontology, plan: QueryPlan) -> list[str]:
    errors: list[str] = []
    if plan.needs_clarification:
        return errors
    if not plan.metrics:
        errors.append("The plan has no metric. Pick at least one metric from the context.")
    for m in plan.metrics:
        if not _concept(onto, "metric", m):
            errors.append(f"'{m}' isn't a metric in the catalog.")
    for d in plan.dimensions:
        node = _concept(onto, "dimension", d)
        if not node:
            errors.append(f"'{d}' isn't a dimension in the catalog.")
        elif node["props"]["type"] == "time":
            errors.append(f"'{d}' is a time dimension: use time_dimension and time_grain instead of dimensions.")
    if len(set(plan.metrics)) != len(plan.metrics) or len(set(plan.dimensions)) != len(plan.dimensions):
        errors.append("A metric or dimension is listed twice.")

    if plan.time_dimension:
        node = _concept(onto, "dimension", plan.time_dimension)
        if not node:
            errors.append(f"'{plan.time_dimension}' isn't a dimension in the catalog.")
        elif node["props"]["type"] != "time":
            errors.append(f"'{plan.time_dimension}' isn't a time dimension.")
        elif plan.time_grain and plan.time_grain not in node["props"]["time_grains"]:
            errors.append(f"'{plan.time_dimension}' can be grouped by {', '.join(node['props']['time_grains'])}, "
                          f"not {plan.time_grain}.")
    elif plan.time_grain or plan.time_range:
        errors.append("time_grain or time_range is set without a time_dimension.")

    tr = plan.time_range
    if tr:
        if tr.kind == "relative" and not (tr.unit and tr.count):
            errors.append("A relative time_range needs a unit and a count.")
        if tr.kind == "absolute" and not (tr.start and tr.end):
            errors.append("An absolute time_range needs start and end dates.")

    for f in plan.filters:
        node = _concept(onto, "dimension", f.dimension)
        if not node:
            errors.append(f"Filter on '{f.dimension}', which isn't a dimension in the catalog.")
            continue
        n = len(f.values)
        if f.operator in NO_VALUES and n:
            errors.append(f"Filter '{f.dimension} {f.operator}' takes no values.")
        elif f.operator in ONE_VALUE and n != 1:
            errors.append(f"Filter '{f.dimension} {f.operator}' needs exactly one value.")
        elif f.operator == "between" and n != 2:
            errors.append(f"Filter '{f.dimension} between' needs two values.")
        elif f.operator in ("in", "not_in") and not n:
            errors.append(f"Filter '{f.dimension} {f.operator}' needs at least one value.")

    allowed = set(plan.metrics) | set(plan.dimensions) | ({"time"} if plan.time_grain else set())
    for o in plan.order_by:
        if o.field not in allowed:
            errors.append(f"order_by '{o.field}' isn't one of the plan's metrics or dimensions.")
    if plan.limit > get_settings().max_rows:
        errors.append(f"limit {plan.limit} is above the maximum of {get_settings().max_rows} rows.")
    return errors


def resolve(onto: Ontology, plan: QueryPlan) -> tuple[ResolvedPlan | None, list[str]]:
    errors = check_plan(onto, plan)
    if errors or plan.needs_clarification:
        return None, errors

    def table(kind: str, name: str) -> str:
        return onto.node(f"{kind}:{name}")["props"]["table"]

    def label(kind: str, name: str) -> str:
        return onto.node(f"{kind}:{name}")["label"]

    # Dimension tables every metric has to reach: group-by, time and filters.
    dim_refs = list(dict.fromkeys(plan.dimensions + ([plan.time_dimension] if plan.time_dimension else [])
                                  + [f.dimension for f in plan.filters]))
    base = table("metric", plan.metrics[0])
    joins: dict[str, JoinStep] = {}
    paths: list[MetricPath] = []
    for part in dict.fromkeys(p for m in plan.metrics for p in metric_parts(onto, m)):
        m_table = table("metric", part)
        mp = MetricPath(metric=part, table=m_table, agg=onto.node(f"metric:{part}")["props"]["agg"])
        for d in dim_refs:
            d_table = table("dimension", d)
            if d_table == m_table:
                continue
            path = onto.join_path(m_table, d_table)
            if path is None:
                errors.append(f"{label('metric', part)} can't be split or filtered by {label('dimension', d)}: there's "
                              f"no approved join between {label('table', m_table)} and {label('table', d_table)}.")
                continue
            for step in path:
                ps = PathStep(join=step["name"], from_table=step["from"], to_table=step["to"], fans_out=step["fans_out"])
                if ps not in mp.steps:
                    mp.steps.append(ps)
                joins.setdefault(step["name"], JoinStep(
                    name=step["name"], left=onto.g.nodes[step["left"]]["name"], right=onto.g.nodes[step["right"]]["name"],
                    on=step.get("on", []), relationship=step.get("relationship", "")))
        paths.append(mp)
    if errors:
        return None, list(dict.fromkeys(errors))
    tables = [base] + [p.table for p in paths] + [t for j in joins.values() for t in (j.left, j.right)]
    return ResolvedPlan(plan=plan, base_table=base, tables=list(dict.fromkeys(tables)), joins=list(joins.values()),
                        metric_paths=paths, summary=describe(onto, plan)), []


def metric_parts(onto: Ontology, name: str, _seen: frozenset = frozenset()) -> list[str]:
    """The metrics actually aggregated: a ratio is its numerator and denominator (recursively)."""
    node = onto.node(f"metric:{name}")
    p = node["props"] if node else {}
    if p.get("agg") != "ratio" or name in _seen:
        return [name]
    seen = _seen | {name}
    return metric_parts(onto, p["numerator"], seen) + metric_parts(onto, p["denominator"], seen)


_GRAIN = {"date": "day", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}
_OPS = {"equals": "is", "not_equals": "is not", "in": "is one of", "not_in": "is not one of", "contains": "contains",
        "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "is between", "is_null": "is empty",
        "is_not_null": "is not empty"}


def describe(onto: Ontology, plan: QueryPlan) -> str:
    """One plain-English sentence, e.g. 'Revenue by Customer region, per month, over the last 12 months,
    where Sales channel is web.'"""

    def label(kind: str, name: str) -> str:
        node = onto.node(f"{kind}:{name}")
        return node["label"] if node else name

    text = " and ".join(label("metric", m) for m in plan.metrics) or "Nothing"
    if plan.dimensions:
        text += " by " + ", ".join(label("dimension", d) for d in plan.dimensions)
    if plan.time_dimension and plan.time_grain:
        text += f", per {_GRAIN[plan.time_grain]} of {label('dimension', plan.time_dimension).lower()}"
    tr = plan.time_range
    if tr and tr.kind == "relative" and tr.unit:
        if tr.include_current:
            text += f", for this {tr.unit}" + (" so far" if tr.count == 1 else f" and the previous {tr.count - 1}")
        elif tr.count == 1:
            text += f", over the last complete {tr.unit}"
        else:
            text += f", over the last {tr.count} {tr.unit}s"
    elif tr and tr.kind == "absolute":
        text += f", from {tr.start} to {tr.end}"
    if tr and plan.time_dimension and not plan.time_grain:
        text += f" (by {label('dimension', plan.time_dimension).lower()})"
    if plan.filters:
        text += ", where " + " and ".join(
            f"{label('dimension', f.dimension)} {_OPS[f.operator]}"
            + (f" {' and '.join(map(str, f.values))}" if f.operator == "between" else
               f" {', '.join(map(str, f.values))}" if f.values else "")
            for f in plan.filters)
    top = next((o for o in plan.order_by if o.field != "time"), None)
    if top and plan.limit < get_settings().default_limit:
        text += f". {'Top' if top.direction == 'desc' else 'Bottom'} {plan.limit} by {label('metric', top.field) if top.field in plan.metrics else label('dimension', top.field)}"
    return text + "."
