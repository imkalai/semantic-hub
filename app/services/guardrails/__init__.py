"""Validation and guardrails (diagram: Governance and Validation / Validation and Guardrails).

Runs on a resolved plan, before anything is executed. Some rules adjust the plan (and say so), the rest
pass, warn or block:

- Large tables are limited to a period on their partition date (adjusts).
- Filter values are matched to the known values' spelling, and unknown values are flagged (adjusts / warns).
- Only approved joins are used (by construction; reported).
- No double counting: a metric must not be split or filtered across a join that repeats its rows (blocks).
- No personal data in group-by or filters unless SH_ALLOW_PII is set (blocks).
- Row limit and number of group-by dimensions (reported / warns).
"""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.schemas.meta import TableMeta
from app.schemas.plan import QueryPlan, ResolvedPlan, TimeRange
from app.services.ontology.graph import Ontology
from app.services.planning.resolve import describe, metric_parts, resolve
from app.services.retrieval.metadata_store import load_metadata

GB = 1024**3
SAFE_ACROSS_FAN_OUT = {"count_distinct", "min", "max"}  # repeated rows don't change these
Status = Literal["pass", "adjusted", "warn", "block"]


class GuardrailCheck(BaseModel):
    rule: str
    label: str
    status: Status
    message: str
    suggestions: list[str] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)  # {"key", "label"}: items a person can confirm


class GuardrailReport(BaseModel):
    status: Literal["pass", "warn", "block"]
    checks: list[GuardrailCheck]

    @property
    def blocked(self) -> bool:
        return self.status == "block"


def _fmt_size(n: int) -> str:
    return f"{n / 1024**4:.1f} TB" if n >= 1024**4 else f"{n / GB:.0f} GB"


class _Ctx:
    def __init__(self, onto: Ontology, metadata: dict[str, TableMeta]):
        self.onto, self.metadata = onto, metadata

    def node(self, kind: str, name: str) -> dict:
        return self.onto.node(f"{kind}:{name}")

    def label(self, kind: str, name: str) -> str:
        n = self.node(kind, name)
        return n["label"] if n else name

    def table_meta(self, table: str) -> TableMeta | None:
        t = self.node("table", table)
        return self.metadata.get(t["props"]["table"]) if t else None

    def dim_table(self, d: str) -> str:
        return self.node("dimension", d)["props"]["table"]


# ---------- rules that adjust the plan ----------

def limit_large_tables(c: _Ctx, plan: QueryPlan) -> list[GuardrailCheck]:
    s = get_settings()
    tables = list(dict.fromkeys(c.node("metric", p)["props"]["table"] for m in plan.metrics for p in metric_parts(c.onto, m)))
    checks = []
    for t in tables:
        meta = c.table_meta(t)
        if not meta or not meta.size_bytes or meta.size_bytes < s.large_table_gb * GB:
            continue
        size = _fmt_size(meta.size_bytes)
        part_dim = next((d["name"] for d in c.onto.concepts("dimension")
                         if d["props"]["table"] == t and d["props"]["type"] == "time"
                         and d["props"]["column"] == meta.partition_field), None)
        if plan.time_range and plan.time_dimension:
            if plan.time_dimension == part_dim or c.dim_table(plan.time_dimension) != t:
                continue  # already limited (on another table's date, the join still narrows the rows)
            checks.append(GuardrailCheck(
                rule="large_table_period", label="Large tables limited to a period", status="warn",
                message=f"{c.label('table', t)} is {size} and is partitioned by "
                        f"{c.label('dimension', part_dim).lower() if part_dim else meta.partition_field}, but the period "
                        f"is on {c.label('dimension', plan.time_dimension).lower()}, so the whole table will be read."))
            continue
        if not part_dim:
            checks.append(GuardrailCheck(rule="large_table_period", label="Large tables limited to a period", status="warn",
                                         message=f"{c.label('table', t)} is {size} and has no partitioned date in the "
                                                 f"catalog, so the whole table will be read."))
            continue
        if plan.time_range is None:
            months = s.default_months
            if plan.time_dimension is None:
                plan.time_dimension = part_dim
            plan.time_range = TimeRange(kind="relative", unit="month", count=months, include_current=False)
            checks.append(GuardrailCheck(
                rule="large_table_period", label="Large tables limited to a period", status="adjusted",
                message=f"{c.label('table', t)} is {size}, so the question was limited to the last {months} months of "
                        f"{c.label('dimension', plan.time_dimension).lower()}. Name a period to change it."))
    if not checks:
        checks.append(GuardrailCheck(rule="large_table_period", label="Large tables limited to a period", status="pass",
                                     message="No large table is read without a period."))
    return checks


def match_filter_values(c: _Ctx, plan: QueryPlan) -> GuardrailCheck:
    adjusted, unknown = [], []
    for f in plan.filters:
        if f.operator not in ("equals", "not_equals", "in", "not_in"):
            continue
        d = c.node("dimension", f.dimension)
        if d["props"]["type"] != "string" or d["props"].get("pii"):
            continue
        meta = c.table_meta(d["props"]["table"])
        col = meta.column(d["props"]["column"]) if meta else None
        known = [str(v) for v in (col.sample_values if col else [])]
        if not known:
            continue
        by_lower = {k.lower(): k for k in known}
        new_values = []
        for v in f.values:
            canonical = by_lower.get(str(v).lower())
            if canonical is None:
                unknown.append(f"“{v}” isn't a known {d['label'].lower()} (known: {', '.join(known[:6])})")
                new_values.append(v)
            else:
                if canonical != v:
                    adjusted.append(f"“{v}” → “{canonical}”")
                new_values.append(canonical)
        f.values = new_values
    if unknown:
        return GuardrailCheck(rule="filter_values", label="Filter values", status="warn",
                              message="; ".join(unknown) + ". The filter may return no rows.")
    if adjusted:
        return GuardrailCheck(rule="filter_values", label="Filter values", status="adjusted",
                              message="Matched the spelling used in the data: " + ", ".join(adjusted) + ".")
    return GuardrailCheck(rule="filter_values", label="Filter values", status="pass",
                          message="Filter values match known values." if plan.filters else "No filters.")


# ---------- rules that pass, warn or block ----------

def approved_joins(c: _Ctx, r: ResolvedPlan) -> GuardrailCheck:
    names = [f"{j.name} ({j.trust})" for j in r.joins]
    return GuardrailCheck(rule="approved_joins", label="Catalog joins only", status="pass",
                          message=f"Uses only joins from the catalog: {', '.join(names)}." if names else "No joins needed.")


def no_double_counting(c: _Ctx, r: ResolvedPlan) -> GuardrailCheck:
    problems, suggestions = [], []
    dims = r.plan.dimensions + ([r.plan.time_dimension] if r.plan.time_dimension else []) + [f.dimension for f in r.plan.filters]
    for mp in r.metric_paths:
        fan = [s for s in mp.steps if s.fans_out]
        if not fan or mp.agg in SAFE_ACROSS_FAN_OUT:
            continue
        reached = [d for d in dims if any(s.to_table == c.dim_table(d) for s in mp.steps)]
        s = fan[0]
        problems.append(f"{c.label('metric', mp.metric)} would be counted more than once when split or filtered by "
                        f"{', '.join(c.label('dimension', d) for d in reached) or 'this'}: each row of "
                        f"{c.label('table', s.from_table)} matches several rows of {c.label('table', s.to_table)}.")
        suggestions += _alternatives(c, mp.metric, dims)
    if problems:
        suggestions = list(dict.fromkeys(suggestions))[:3]
        return GuardrailCheck(rule="no_double_counting", label="No double counting", status="block",
                              message=" ".join(problems) + (f" Try {' or '.join(suggestions)} instead." if suggestions else ""),
                              suggestions=suggestions)
    return GuardrailCheck(rule="no_double_counting", label="No double counting", status="pass",
                          message="Every metric reaches its dimensions without repeating rows.")


def _alternatives(c: _Ctx, metric: str, dims: list[str]) -> list[str]:
    """Metrics with the same format that can be split by all these dimensions without fan-out."""
    fmt = c.node("metric", metric)["props"]["format"]
    out = []
    for m in c.onto.concepts("metric"):
        if m["name"] == metric or m["props"]["format"] != fmt:
            continue
        ok = True
        for part in metric_parts(c.onto, m["name"]):
            t = c.node("metric", part)["props"]["table"]
            agg = c.node("metric", part)["props"]["agg"]
            for d in dims:
                path = c.onto.join_path(t, c.dim_table(d))
                if path is None or (agg not in SAFE_ACROSS_FAN_OUT and any(s["fans_out"] for s in path)):
                    ok = False
        if ok:
            out.append(m["label"])
    return out


def personal_data(c: _Ctx, r: ResolvedPlan) -> GuardrailCheck:
    p = r.plan
    used = [d for d in p.dimensions + ([p.time_dimension] if p.time_dimension else []) + [f.dimension for f in p.filters]
            if c.node("dimension", d)["props"].get("pii")]
    if not used:
        return GuardrailCheck(rule="personal_data", label="No personal data", status="pass",
                              message="No personal-data fields are grouped by or filtered on.")
    labels = ", ".join(dict.fromkeys(c.label("dimension", d) for d in used))
    if get_settings().allow_pii:
        return GuardrailCheck(rule="personal_data", label="No personal data", status="warn",
                              message=f"{labels} is personal data. Allowed because SH_ALLOW_PII is on.")
    return GuardrailCheck(rule="personal_data", label="No personal data", status="block",
                          message=f"{labels} is personal data, so it can't be grouped by or filtered on.")


def confirmed_definitions(c: _Ctx, r: ResolvedPlan) -> list[GuardrailCheck]:
    """Inferred items are guesses. A guessed join decides which rows are combined, so it must be confirmed
    before the plan can run; guessed metrics and dimensions only get a warning."""
    joins = [j for j in r.joins if j.trust == "inferred"]
    p = r.plan
    concepts = [("metric", m) for m in dict.fromkeys(x for m in p.metrics for x in [m, *metric_parts(c.onto, m)])]
    concepts += [("dimension", d) for d in dict.fromkeys(p.dimensions + ([p.time_dimension] if p.time_dimension else [])
                                                          + [f.dimension for f in p.filters])]
    guessed = [(k, n) for k, n in concepts if (c.node(k, n) or {}).get("props", {}).get("trust") == "inferred"]
    checks = []
    if joins:
        checks.append(GuardrailCheck(
            rule="confirmed_joins", label="Joins confirmed", status="block",
            message="These joins were guessed from column names: " + "; ".join(
                f"{j.left} → {j.right} on " + " and ".join(f"{k['left']} = {k['right']}" for k in j.on)
                + f" ({j.relationship.replace('_', ' ')})" for j in joins) + ". Confirm them if they're right.",
            actions=[{"key": j.key, "label": f"Confirm {j.left} → {j.right}"} for j in joins if j.key]))
    else:
        checks.append(GuardrailCheck(rule="confirmed_joins", label="Joins confirmed", status="pass",
                                     message="Every join used is certified or declared." if r.joins else "No joins needed."))
    if guessed:
        checks.append(GuardrailCheck(
            rule="confirmed_definitions", label="Definitions confirmed", status="warn",
            message="Not confirmed yet, so check the definition: " + ", ".join(
                f"{c.label(k, n)} ({_definition(c, k, n)})" for k, n in guessed) + ".",
            actions=[{"key": c.node(k, n)["props"]["key"], "label": f"Confirm {c.label(k, n)}"}
                     for k, n in guessed if c.node(k, n)["props"].get("key")]))
    return checks


def _definition(c: _Ctx, kind: str, name: str) -> str:
    p = c.node(kind, name)["props"]
    if kind == "metric":
        return "count of rows" if p["agg"] == "count" else f"{p['agg'].replace('_', ' ')} of {p['table']}.{p['column']}"
    return f"{p['table']}.{p['column']}"


def row_limits(c: _Ctx, r: ResolvedPlan) -> GuardrailCheck:
    s = get_settings()
    if len(r.plan.dimensions) > s.max_group_by:
        return GuardrailCheck(rule="row_limits", label="Result size", status="warn",
                              message=f"Grouped by {len(r.plan.dimensions)} dimensions: the result may be large and hard "
                                      f"to read. At most {r.plan.limit} rows are returned.")
    return GuardrailCheck(rule="row_limits", label="Result size", status="pass",
                          message=f"At most {r.plan.limit} rows are returned (maximum {s.max_rows}).")


# ---------- run them ----------

def apply_guardrails(db: Session, onto: Ontology, resolved: ResolvedPlan) -> tuple[ResolvedPlan, GuardrailReport]:
    c = _Ctx(onto, load_metadata(db))
    plan = resolved.plan.model_copy(deep=True)
    checks = limit_large_tables(c, plan) + [match_filter_values(c, plan)]
    if plan != resolved.plan:
        new, errors = resolve(onto, plan)  # the adjusted plan may need different joins
        if new is None:
            checks.append(GuardrailCheck(rule="adjustment", label="Adjusted plan", status="block",
                                         message="The adjusted plan isn't valid: " + " ".join(errors)))
            return resolved, _report(checks)
        new.plan.assumptions = list(dict.fromkeys(new.plan.assumptions))
        new.summary = describe(onto, new.plan)
        resolved = new
    checks += [approved_joins(c, resolved), *confirmed_definitions(c, resolved), no_double_counting(c, resolved),
               personal_data(c, resolved), row_limits(c, resolved)]
    return resolved, _report(checks)


def _report(checks: list[GuardrailCheck]) -> GuardrailReport:
    statuses = {ch.status for ch in checks}
    status = "block" if "block" in statuses else "warn" if "warn" in statuses else "pass"
    return GuardrailReport(status=status, checks=checks)


__all__ = ["GuardrailCheck", "GuardrailReport", "apply_guardrails"]
