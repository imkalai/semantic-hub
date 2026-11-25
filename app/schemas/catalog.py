"""The semantic catalog: tables, metrics, dimensions, approved joins and synonyms.

It is the source of truth for the ontology. Bootstrap copies it into the graph store, the healthcheck
compares the graph against it, and the app falls back to it when the graph store can't be used.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def normalize_term(term: str) -> str:
    """'Sales  Revenue' -> 'sales revenue'. Synonyms are matched on this form."""
    return " ".join(re.findall(r"[a-z0-9]+", term.lower()))


class _Concept(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError(f"'{v}' must be lower_snake_case")
        return v


class CatalogTable(_Concept):
    table: str  # project.dataset.table in BigQuery
    primary_key: str
    time_column: str | None = None

    @field_validator("table")
    @classmethod
    def _three_parts(cls, v: str) -> str:
        if len(v.split(".")) != 3:
            raise ValueError(f"'{v}' must be project.dataset.table")
        return v

    @property
    def project(self) -> str:
        return self.table.split(".")[0]

    @property
    def dataset(self) -> str:
        return self.table.split(".")[1]

    @property
    def table_name(self) -> str:
        return self.table.split(".")[2]


class CatalogMetric(_Concept):
    table: str
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max", "ratio"]
    column: str | None = None  # not needed for count (rows) or ratio
    numerator: str | None = None  # ratio only: metric names
    denominator: str | None = None
    format: Literal["number", "currency", "percent"] = "number"


class CatalogDimension(_Concept):
    table: str
    column: str
    type: Literal["string", "number", "time", "boolean"] = "string"
    time_grains: list[Literal["date", "week", "month", "quarter", "year"]] = Field(default_factory=list)
    pii: bool = False


class JoinKey(BaseModel):
    left: str
    right: str


class CatalogJoin(BaseModel):
    name: str
    left: str  # table names from the catalog
    right: str
    on: list[JoinKey]
    relationship: Literal["many_to_one", "one_to_many", "one_to_one"] = "many_to_one"
    approved: bool = True


class Synonym(BaseModel):
    term: str
    concept: str  # "metric:revenue", "dimension:customer_region" or "table:orders"


class SemanticCatalog(BaseModel):
    version: str
    name: str = ""
    description: str = ""
    tables: list[CatalogTable] = Field(default_factory=list)
    metrics: list[CatalogMetric] = Field(default_factory=list)
    dimensions: list[CatalogDimension] = Field(default_factory=list)
    joins: list[CatalogJoin] = Field(default_factory=list)
    synonyms: list[Synonym] = Field(default_factory=list)  # extra terms, on top of each concept's own list

    def table(self, name: str) -> CatalogTable | None:
        return next((t for t in self.tables if t.name == name), None)

    def metric(self, name: str) -> CatalogMetric | None:
        return next((m for m in self.metrics if m.name == name), None)
