"""Where a session's concepts come from. Each source returns catalog items with their provenance; the
assembler merges them, strongest first: curated catalog > Looker > declared in BigQuery > inferred.

- CuratedSource: the hand-written catalog file, for the tables it covers (certified).
- BigQuerySource: tables, declared primary/foreign keys and personal-data tags (declared).
- InferenceSource: metrics, dimensions and joins guessed from names and types (inferred).
- LookerSource: placeholder, see looker.py.
"""

import re
from dataclasses import dataclass, field

from app.schemas.catalog import (CatalogDimension, CatalogJoin, CatalogMetric, CatalogTable, JoinKey,
                                 SemanticCatalog)
from app.schemas.meta import ColumnMeta, KeyConstraint, TableMeta
from app.services.text import humanize, singular, snake

TIME_GRAINS = ["date", "week", "month", "quarter", "year"]
MONEY = re.compile(r"(^|_)(amount|amt|revenue|fee|fees|price|cost|value|balance|refund|sales|spend|income|salary|"
                   r"gbp|usd|eur|total)($|_)")
PERCENT = re.compile(r"(^|_)(pct|percent|percentage|rate|ratio)($|_)")
AVERAGE = re.compile(r"(^|_)(price|rate|ratio|pct|percent|percentage|score|age|avg|average|mean)($|_)")
NOT_A_MEASURE = re.compile(r"(^|_)(year|month|day|week|quarter|hour|lat|lon|lng|latitude|longitude|zip|postcode|"
                           r"code|number|no|num|version|rank|order|sequence|seq)($|_)")


@dataclass
class SourceItems:
    tables: list[CatalogTable] = field(default_factory=list)
    metrics: list[CatalogMetric] = field(default_factory=list)
    dimensions: list[CatalogDimension] = field(default_factory=list)
    joins: list[CatalogJoin] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------- approval keys: stable across sessions, built from BigQuery names ----------

def metric_key(table_full: str, column: str | None, agg: str) -> str:
    return f"metric:{table_full}:{column or '*'}:{agg}"


def dimension_key(table_full: str, column: str) -> str:
    return f"dimension:{table_full}:{column}"


def join_key(left_full: str, right_full: str, on: list[JoinKey]) -> str:
    return f"join:{left_full}:{right_full}:" + ",".join(f"{k.left}={k.right}" for k in on)


# ---------- naming ----------

def table_names(full_names: list[str], curated: SemanticCatalog) -> dict[str, str]:
    """A concept name per table: the curated name if the catalog has one, else the table name, with the
    dataset added when two datasets have a table of the same name."""
    curated_names = {t.table: t.name for t in curated.tables}
    out: dict[str, str] = {}
    short = [f.split(".")[2] for f in full_names]
    for full in full_names:
        if full in curated_names:
            out[full] = curated_names[full]
            continue
        _, dataset, table = full.split(".")
        name = snake(table) if short.count(table) == 1 else snake(f"{dataset}_{table}")
        if not name[0].isalpha():
            name = f"t_{name}"
        while name in out.values() or (name in curated_names.values() and full not in curated_names):
            name += "_2"
        out[full] = name
    return out


def _label(table_label: str, column: str) -> str:
    """orders + status -> 'Order status'; products + product_name -> 'Product name';
    stock levels + snapshot_date -> 'Stock level snapshot date'."""
    parts = table_label.lower().split()
    thing = " ".join(parts[:-1] + [singular(parts[-1])])
    words = column.replace("_", " ")
    return humanize(words if words.startswith(thing) else f"{thing} {words}")


# ---------- curated ----------

class CuratedSource:
    """Concepts from the curated catalog whose tables are all in the session."""

    def items(self, curated: SemanticCatalog, in_scope: set[str]) -> SourceItems:
        tables = [t for t in curated.tables if t.table in in_scope]
        names = {t.name for t in tables}
        metrics = [m for m in curated.metrics if m.table in names]
        by_name = {m.name: m for m in metrics}

        def complete(m: CatalogMetric, seen=frozenset()) -> bool:  # ratio parts must be in the session too
            if m.agg != "ratio":
                return True
            parts = [by_name.get(m.numerator), by_name.get(m.denominator)]
            return all(p is not None and p.name not in seen and complete(p, seen | {m.name}) for p in parts)

        metrics = [m for m in metrics if complete(m)]
        full = {t.name: t.table for t in tables}
        out = SourceItems(
            tables=[t.model_copy(update={"trust": "certified", "source": "catalog", "key": f"table:{t.table}"}) for t in tables],
            metrics=[m.model_copy(update={"trust": "certified", "source": "catalog",
                                          "key": metric_key(full[m.table], m.column, m.agg)}) for m in metrics],
            dimensions=[d.model_copy(update={"trust": "certified", "source": "catalog",
                                             "key": dimension_key(full[d.table], d.column)})
                        for d in curated.dimensions if d.table in names],
            joins=[j.model_copy(update={"trust": "certified", "source": "catalog",
                                        "key": join_key(full[j.left], full[j.right], j.on)})
                   for j in curated.joins if j.left in names and j.right in names and j.approved],
        )
        if tables:
            out.notes.append(f"{len(tables)} table(s) are in the curated catalog.")
        return out


# ---------- BigQuery: tables, declared keys ----------

def primary_keys(metas: dict[str, TableMeta], constraints: list[KeyConstraint]) -> dict[str, tuple[str | None, str]]:
    """table -> (primary key column, 'declared' | 'inferred'). Composite keys aren't used for joins."""
    out: dict[str, tuple[str | None, str]] = {}
    declared = {c.table: c.columns for c in constraints if c.kind == "primary"}
    for full, meta in metas.items():
        cols = [c.name for c in meta.columns]
        if full in declared and len(declared[full]) == 1:
            out[full] = (declared[full][0], "declared")
            continue
        table = full.split(".")[2].lower()
        guesses = [f"{singular(table)}_id", f"{table}_id", "id"]
        found = next((c for g in guesses for c in cols if c.lower() == g), None)
        if found is None and cols and cols[0].lower() == singular(table):  # regions.region
            found = cols[0]
        out[full] = (found, "inferred" if found else "none")
    return out


class BigQuerySource:
    def items(self, metas: dict[str, TableMeta], constraints: list[KeyConstraint], names: dict[str, str],
              pks: dict[str, tuple[str | None, str]]) -> SourceItems:
        out = SourceItems()
        for full, meta in metas.items():
            time_col = meta.partition_field or next((c.name for c in meta.columns if c.is_time), None)
            out.tables.append(CatalogTable(
                name=names[full], label=humanize(names[full]), table=full, description=meta.description,
                primary_key=pks[full][0], time_column=time_col, trust="declared", source="bigquery",
                key=f"table:{full}"))
        for c in constraints:
            if c.kind != "foreign" or c.table not in metas or c.ref_table not in metas or len(c.columns) != len(c.ref_columns):
                continue
            on = [JoinKey(left=a, right=b) for a, b in zip(c.columns, c.ref_columns)]
            out.joins.append(CatalogJoin(
                name=f"{names[c.table]}_to_{names[c.ref_table]}", left=names[c.table], right=names[c.ref_table], on=on,
                relationship="many_to_one", trust="declared", source="bigquery", key=join_key(c.table, c.ref_table, on)))
        declared = sum(1 for v in pks.values() if v[1] == "declared")
        out.notes.append(f"{declared} declared primary key(s), {len(out.joins)} declared foreign key(s) in BigQuery.")
        return out


# ---------- inference: what names and types suggest ----------

class InferenceSource:
    def items(self, metas: dict[str, TableMeta], names: dict[str, str],
              pks: dict[str, tuple[str | None, str]]) -> SourceItems:
        out = SourceItems()
        keys = {full: {c.name for c in m.columns if _is_key(c, pks[full][0])} for full, m in metas.items()}
        for full, meta in metas.items():
            t = names[full]
            t_label = humanize(t)
            out.metrics.append(CatalogMetric(
                name=f"{t}_count", label=f"Number of {t_label.lower()}", table=t, agg="count",
                description=f"How many rows {t_label} has.", trust="inferred", source="inferred",
                key=metric_key(full, None, "count")))
            for c in meta.columns:
                if not c.is_supported or c.name in keys[full]:
                    continue
                col = c.name.lower()
                if c.is_numeric and not NOT_A_MEASURE.search(col):
                    agg = "avg" if AVERAGE.search(col) else "sum"
                    fmt = "percent" if PERCENT.search(col) else "currency" if MONEY.search(col) else "number"
                    label = _label(t_label, col)
                    out.metrics.append(CatalogMetric(
                        name=snake(f"{t}_{col}_{'average' if agg == 'avg' else 'total'}"),
                        label=f"{'Average' if agg == 'avg' else 'Total'} {label[0].lower()}{label[1:]}", table=t, agg=agg,
                        column=c.name, format=fmt, description=c.description, trust="inferred", source="inferred",
                        key=metric_key(full, c.name, agg)))
                elif c.is_string or c.is_bool or c.is_time:
                    kind = "time" if c.is_time else "boolean" if c.is_bool else "string"
                    label = _label(t_label, col)
                    plain = humanize(col.replace("_", " "))
                    out.dimensions.append(CatalogDimension(
                        name=snake(f"{t}_{col}"), label=label, table=t, column=c.name, type=kind,
                        time_grains=TIME_GRAINS if kind == "time" else [], pii=c.is_pii, description=c.description,
                        synonyms=[plain.lower()] if plain.lower() != label.lower() else [],
                        trust="inferred", source="inferred", key=dimension_key(full, c.name)))
        out.joins = self._joins(metas, names, pks)
        missing_pk = [names[f] for f, (pk, _) in pks.items() if pk is None]
        if missing_pk:
            out.notes.append(f"No primary key found for {', '.join(missing_pk)}: other tables can't be joined to them "
                             f"until one is declared or confirmed.")
        return out

    def _joins(self, metas, names, pks) -> list[CatalogJoin]:
        joins: list[CatalogJoin] = []
        seen: set[frozenset] = set()
        for a, meta in metas.items():
            for c in meta.columns:
                for b, (pk_b, _) in pks.items():
                    if b == a or pk_b is None:
                        continue
                    b_table = b.split(".")[2].lower()
                    matches = c.name.lower() == pk_b.lower() or (pk_b.lower() == "id" and c.name.lower() == f"{singular(b_table)}_id")
                    if not matches:
                        continue
                    pair = frozenset({a, b})
                    if pair in seen:
                        continue
                    seen.add(pair)
                    one_to_one = (pks[a][0] or "").lower() == c.name.lower()
                    on = [JoinKey(left=c.name, right=pk_b)]
                    joins.append(CatalogJoin(
                        name=f"{names[a]}_to_{names[b]}", left=names[a], right=names[b], on=on,
                        relationship="one_to_one" if one_to_one else "many_to_one",
                        trust="inferred", source="inferred", key=join_key(a, b, on)))
        return joins


def _is_key(c: ColumnMeta, pk: str | None) -> bool:
    n = c.name.lower()
    return n == (pk or "").lower() or n == "id" or n.endswith("_id") or n.endswith("_key")
