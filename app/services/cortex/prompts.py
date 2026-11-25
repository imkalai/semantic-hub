"""Prompts sent to Cortex. Each system prompt starts with 'TASK: <name>' (the mock answers by task).
The user message carries the context as JSON inside <context>...</context>."""

import json

PLAN_SYSTEM = """TASK: plan
You turn a business question into a query plan for a governed semantic layer.
Use ONLY the metric and dimension names given in the context. Never invent names, tables, columns or SQL.

Reply with one JSON object and nothing else:
{
  "metrics": ["<metric name>", ...],            // at least one, unless needs_clarification is set
  "dimensions": ["<dimension name>", ...],      // what to group by ("by region" -> customer_region); not time
  "time_dimension": "<time dimension name>" | null,
  "time_grain": "date" | "week" | "month" | "quarter" | "year" | null,   // must be in that dimension's time_grains
  "time_range": {"kind": "relative", "unit": "day|week|month|quarter|year", "count": <n>, "include_current": <bool>}
              | {"kind": "absolute", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} | null,
  "filters": [{"dimension": "<name>", "operator": "equals|not_equals|in|not_in|contains|gt|gte|lt|lte|between|is_null|is_not_null", "values": [...]}],
  "order_by": [{"field": "<metric or dimension name in the plan, or time>", "direction": "asc|desc"}],
  "limit": <rows, default from context>,
  "explanation": "<one sentence, plain English>",
  "assumptions": ["<anything you assumed>"],
  "needs_clarification": null | "<question to ask the user when the request can't be planned>"
}

Rules:
- Candidates marked exact matched the user's words; prefer them. Others are only similar in meaning.
- "how many <thing>" with no metric named: use the count metric of that thing's table.
- Words that are dimension values ("web", "North") become filters, not dimensions. "excluding X" / "not X" -> not_equals.
- monthly/weekly/... or "trend" or "over time" -> set time_dimension (the time dimension of the first metric's table) and time_grain.
- "last N months" -> relative range, include_current false. "this year", "year to date" -> include_current true.
- With a time grain but no range, use the last <default_months> months and say so in assumptions.
- "top N" -> order by the first metric desc and limit N. "bottom N" -> asc.
- Don't use dimensions flagged pii unless the user explicitly asks for that personal data.
- If context.errors is present, your previous plan was rejected for those reasons: fix them.
"""


def plan_messages(context: dict) -> list[dict]:
    return [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": f"<context>{json.dumps(context, default=str)}</context>"},
    ]
