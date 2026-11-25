"""Keyword rules that stand in for Cortex's planning answer during development.

They read the same context the real LLM gets (question, retrieval candidates, the concepts on the tables
involved) and follow the same rules as the prompt. Deliberately simple; real Cortex replaces them.
"""

import re

GRAIN_WORDS = [
    (r"\b(daily|per day|by day|each day|every day)\b", "date"),
    (r"\b(weekly|per week|by week|each week|every week)\b", "week"),
    (r"\b(monthly|per month|by month|each month|every month|month on month|mom)\b", "month"),
    (r"\b(quarterly|per quarter|by quarter|each quarter|every quarter)\b", "quarter"),
    (r"\b(yearly|annual|annually|per year|by year|each year|every year|year on year|yoy)\b", "year"),
    (r"\b(trend|trends|over time|timeline|history)\b", "month"),
]
UNITS = {"day": "day", "days": "day", "week": "week", "weeks": "week", "month": "month", "months": "month",
         "quarter": "quarter", "quarters": "quarter", "year": "year", "years": "year"}
GRAIN_OF_UNIT = {"day": "date", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}
NEGATION = r"(?:excluding|exclude|except|not|without|other than|apart from)\s+(?:the\s+)?"


def _time_range(q: str) -> tuple[dict | None, str | None]:
    """(time_range, unit) from phrases like 'last 6 months', 'last quarter', 'this year', 'ytd'."""
    if m := re.search(r"\b(?:last|past|previous|prior)\s+(\d{1,3})\s+(days?|weeks?|months?|quarters?|years?)\b", q):
        unit = UNITS[m.group(2)]
        return {"kind": "relative", "unit": unit, "count": int(m.group(1)), "include_current": False}, unit
    if m := re.search(r"\b(?:last|previous|prior)\s+(day|week|month|quarter|year)\b", q):
        return {"kind": "relative", "unit": m.group(1), "count": 1, "include_current": False}, m.group(1)
    if m := re.search(r"\b(?:this|current)\s+(week|month|quarter|year)\b|\b(ytd|year to date)\b|\b(mtd|month to date)\b", q):
        unit = m.group(1) or ("year" if m.group(2) else "month")
        return {"kind": "relative", "unit": unit, "count": 1, "include_current": True}, unit
    if re.search(r"\byesterday\b", q):
        return {"kind": "relative", "unit": "day", "count": 1, "include_current": False}, "day"
    if re.search(r"\btoday\b", q):
        return {"kind": "relative", "unit": "day", "count": 1, "include_current": True}, "day"
    return None, None


def plan_from_context(ctx: dict) -> dict:
    q = " ".join(re.findall(r"[a-z0-9]+", ctx["question"].lower()))
    cands = ctx.get("candidates", [])
    metrics_by_name = {m["name"]: m for m in ctx.get("metrics", [])}
    dims_by_name = {d["name"]: d for d in ctx.get("dimensions", [])}
    tables = {t["name"]: t for t in ctx.get("tables", [])}
    defaults = ctx.get("defaults", {})
    assumptions: list[str] = []

    exact = [c for c in cands if c.get("exact")]
    metrics = [c["name"] for c in exact if c["kind"] == "metric" and c["name"] in metrics_by_name]
    if not metrics:
        # "how many <thing>": the count metric of a table the user named
        for c in exact:
            if c["kind"] == "table":
                count = next((m["name"] for m in metrics_by_name.values() if m["table"] == c["name"] and m["agg"] == "count"), None)
                if count:
                    metrics.append(count)
                    assumptions.append(f"Counted {c['label'].lower()} using {metrics_by_name[count]['label']}.")
                    break
    if not metrics:
        similar = [c for c in cands if c["kind"] == "metric" and c["name"] in metrics_by_name and c.get("score", 0) >= 0.3]
        if similar:
            metrics.append(similar[0]["name"])
            assumptions.append(f"Assumed you meant {similar[0]['label']}.")
    if not metrics:
        return {"needs_clarification": "Which measure should I use? I couldn't find one in the catalog for this question.",
                "explanation": "No metric matched the question."}

    base = metrics_by_name[metrics[0]]["table"]

    # time
    time_dims = [d for d in dims_by_name.values() if d["type"] == "time"]
    named_time = [c["name"] for c in exact if c["kind"] == "dimension" and dims_by_name.get(c["name"], {}).get("type") == "time"]
    grain = next((g for pattern, g in GRAIN_WORDS if re.search(pattern, q)), None)
    time_range, unit = _time_range(q)
    time_dim = None
    if grain or time_range or named_time:
        if named_time:
            time_dim = named_time[0]
        else:
            # the base table's own date, else the date of a table it joins to directly
            neighbours = [j["right"] if j["left"] == base else j["left"] for j in ctx.get("joins", [])
                          if base in (j["left"], j["right"])]
            for t in [base] + neighbours:
                main_col = tables.get(t, {}).get("time_column")
                on_t = [d for d in time_dims if d["table"] == t]
                time_dim = next((d["name"] for d in on_t if d["column"] == main_col), on_t[0]["name"] if on_t else None)
                if time_dim:
                    break
        if time_dim is None:
            assumptions.append("No date to filter or group by on this data, so the time part of the question was ignored.")
            grain = time_range = None
        elif grain and grain not in dims_by_name[time_dim]["time_grains"]:
            assumptions.append(f"{dims_by_name[time_dim]['label']} can't be grouped by {grain}; used "
                               f"{dims_by_name[time_dim]['time_grains'][-1]} instead.")
            grain = dims_by_name[time_dim]["time_grains"][-1]
    if time_dim and grain and not time_range:
        months = defaults.get("default_months", 12)
        time_range = {"kind": "relative", "unit": "month", "count": months, "include_current": False}
        assumptions.append(f"No period given, so the last {months} complete months are shown.")
    if time_dim and named_time and not grain and not time_range:  # "by order date": group by it
        grains = dims_by_name[time_dim]["time_grains"]
        grain = "month" if "month" in grains else grains[0]

    # dimensions and filters
    dims = [c["name"] for c in exact if c["kind"] == "dimension" and c["name"] in dims_by_name
            and dims_by_name[c["name"]]["type"] != "time"]
    for d in [d for d in dims if dims_by_name[d].get("pii")]:
        dims.remove(d)
        assumptions.append(f"{dims_by_name[d]['label']} is personal data, so it was left out.")
    filters = []
    for v in ctx.get("values", []):
        negated = re.search(NEGATION + re.escape(v["term"]) + r"\b", q)
        filters.append({"dimension": v["dimension"], "operator": "not_equals" if negated else "equals", "values": [v["value"]]})
        # a value-only match is a filter, not a grouping (unless the user also said "by <dimension>")
        if v["dimension"] in dims and not re.search(r"\b(by|per|each|across)\s+" + re.escape(dims_by_name[v["dimension"]]["label"].lower()), q):
            dims.remove(v["dimension"])

    # ordering and limit
    order_by, limit = [], defaults.get("limit", 500)
    if m := re.search(r"\b(top|bottom|lowest|highest|best|worst)\s+(\d{1,4})\b", q):
        limit = int(m.group(2))
        order_by.append({"field": metrics[0], "direction": "asc" if m.group(1) in ("bottom", "lowest", "worst") else "desc"})
    elif grain:
        order_by.append({"field": "time", "direction": "asc"})
    elif dims:
        order_by.append({"field": metrics[0], "direction": "desc"})

    labels = [metrics_by_name[m]["label"] for m in metrics]
    return {
        "metrics": metrics, "dimensions": dims, "time_dimension": time_dim, "time_grain": grain,
        "time_range": time_range, "filters": filters, "order_by": order_by, "limit": limit,
        "explanation": f"{' and '.join(labels)}" + (f" by {', '.join(dims_by_name[d]['label'] for d in dims)}" if dims else "")
        + (f", per {grain}" if grain else "") + ".",
        "assumptions": assumptions, "needs_clarification": None,
    }
