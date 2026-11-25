"""The query plan: what Cortex is asked to produce from a question, in catalog terms only.

It names catalog metrics and dimensions, never tables or SQL. Tables, join paths and SQL are worked out
from the ontology afterwards, so the LLM can't invent a join or a column.
"""

from typing import Literal

from pydantic import BaseModel, Field

Grain = Literal["date", "week", "month", "quarter", "year"]
Operator = Literal["equals", "not_equals", "in", "not_in", "contains", "gt", "gte", "lt", "lte", "between",
                   "is_null", "is_not_null"]


class PlanFilter(BaseModel):
    dimension: str  # catalog dimension name
    operator: Operator = "equals"
    values: list[str | int | float | bool] = Field(default_factory=list)


class TimeRange(BaseModel):
    """Either relative ('last 3 months', 'this year') or absolute dates (YYYY-MM-DD, end exclusive)."""

    kind: Literal["relative", "absolute"] = "relative"
    unit: Literal["day", "week", "month", "quarter", "year"] | None = None
    count: int | None = Field(default=None, ge=1, le=1000)
    include_current: bool = False  # True: "this month" / year to date; False: complete periods only
    start: str | None = None
    end: str | None = None


class OrderBy(BaseModel):
    field: str  # a metric or dimension name used in the plan, or "time"
    direction: Literal["asc", "desc"] = "desc"


class QueryPlan(BaseModel):
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    time_grain: Grain | None = None  # None with a time_dimension = filter by time only, no time column
    time_range: TimeRange | None = None
    filters: list[PlanFilter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=500, ge=1)
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: str | None = None  # set instead of a plan when the question can't be answered


class JoinStep(BaseModel):
    name: str
    left: str
    right: str
    on: list[dict]
    relationship: str
    trust: str = "certified"
    key: str | None = None  # for confirming an inferred join


class PathStep(BaseModel):
    join: str
    from_table: str
    to_table: str
    fans_out: bool  # walking this join repeats rows of from_table


class MetricPath(BaseModel):
    """How one metric (or one part of a ratio) reaches the tables of the plan's dimensions and filters.
    Each metric is aggregated on its own table and then lined up by the dimensions, so every path has to be
    free of fan-out for the numbers to be right."""

    metric: str
    table: str
    agg: str
    steps: list[PathStep] = Field(default_factory=list)


class ResolvedPlan(BaseModel):
    """A plan checked against the ontology, with the tables and approved joins it needs."""

    plan: QueryPlan
    base_table: str
    tables: list[str]
    joins: list[JoinStep]
    metric_paths: list[MetricPath] = Field(default_factory=list)
    summary: str
