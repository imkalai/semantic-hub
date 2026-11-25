"""Merge the sources into one session catalog.

Strongest first: curated catalog, Looker, declared in BigQuery, inferred. The same thing (a metric on a
table column with an aggregation, a dimension on a column, a join between two tables) is kept once, from the
strongest source. Then people's approvals are applied: an approved item becomes certified (with any edits),
a rejected one is dropped. Curated items always win over approvals.
"""

from app.schemas.catalog import (CatalogDimension, CatalogJoin, CatalogMetric, CatalogTable, SemanticCatalog,
                                 Synonym, normalize_term)
from app.schemas.meta import KeyConstraint, TableMeta
from app.services.catalog import concept_ids, validate_catalog
from app.services.sessions.looker import LookerSource
from app.services.sessions.sources import (BigQuerySource, CuratedSource, InferenceSource, SourceItems,
                                           primary_keys, table_names)

RANK = {"certified": 0, "declared": 1, "inferred": 2}
EDITABLE = {
    "metric": {"label", "description", "synonyms", "agg", "format"},
    "dimension": {"label", "description", "synonyms", "pii"},
    "table": {"label", "description", "synonyms", "primary_key"},
    "join": {"relationship"},
}


class AssemblyError(Exception):
    pass


def _apply(item, approval: dict | None):
    """An item after a person's decision: None if rejected, certified (with edits) if approved."""
    if approval is None or item.source == "catalog":
        return item
    if approval["decision"] == "rejected":
        return None
    kind = item.key.split(":", 1)[0]
    changes = {k: v for k, v in (approval.get("changes") or {}).items() if k in EDITABLE.get(kind, set())}
    return item.model_copy(update={**changes, "trust": "certified", "source": "approved"})


def assemble(curated: SemanticCatalog, metas: dict[str, TableMeta], constraints: list[KeyConstraint],
             approvals: dict[str, dict], *, version: str = "session") -> tuple[SemanticCatalog, dict]:
    in_scope = set(metas)
    names = table_names(list(metas), curated)
    pks = primary_keys(metas, constraints)
    for t in curated.tables:  # the curated catalog knows its tables' keys
        if t.table in pks and t.primary_key:
            pks[t.table] = (t.primary_key, "declared")
    for full in pks:  # a person may have confirmed or corrected a key
        a = approvals.get(f"table:{full}")
        if a and a["decision"] == "approved" and a.get("changes", {}).get("primary_key"):
            pks[full] = (a["changes"]["primary_key"], "declared")

    layers: list[tuple[str, SourceItems]] = [
        ("Curated catalog", CuratedSource().items(curated, in_scope)),
        ("Looker", LookerSource().items([], in_scope)),
        ("BigQuery", BigQuerySource().items(metas, constraints, names, pks)),
        ("Inferred", InferenceSource().items(metas, names, pks)),
    ]

    tables: dict[str, CatalogTable] = {}
    metrics: dict[tuple, CatalogMetric] = {}
    dims: dict[tuple, CatalogDimension] = {}
    joins: dict[frozenset, CatalogJoin] = {}
    used_names: dict[str, set[str]] = {"metric": set(), "dimension": set(), "join": set()}

    def unique(kind: str, name: str) -> str:
        n, i = name, 2
        while n in used_names[kind]:
            n, i = f"{name}_{i}", i + 1
        used_names[kind].add(n)
        return n

    for _, items in layers:
        for t in items.tables:
            tables.setdefault(t.table, t)
        for m in items.metrics:
            ident = (m.table, m.column, m.agg) if m.agg != "ratio" else ("ratio", m.name)
            if ident not in metrics:
                metrics[ident] = m.model_copy(update={"name": unique("metric", m.name)}) if m.source != "catalog" else m
                if m.source == "catalog":
                    used_names["metric"].add(m.name)
        for d in items.dimensions:
            ident = (d.table, d.column)
            if ident not in dims:
                dims[ident] = d.model_copy(update={"name": unique("dimension", d.name)}) if d.source != "catalog" else d
                if d.source == "catalog":
                    used_names["dimension"].add(d.name)
        for j in items.joins:
            ident = frozenset({j.left, j.right})
            if ident not in joins:
                joins[ident] = j.model_copy(update={"name": unique("join", j.name)})

    # people's decisions
    final_tables = [_apply(t, approvals.get(t.key)) or t for t in tables.values()]  # tables can't be rejected
    final_metrics = [m for m in (_apply(m, approvals.get(m.key)) for m in metrics.values()) if m]
    final_dims = [d for d in (_apply(d, approvals.get(d.key)) for d in dims.values()) if d]
    final_joins = [j for j in (_apply(j, approvals.get(j.key)) for j in joins.values()) if j]
    names_left = {m.name for m in final_metrics}  # a ratio whose part was rejected goes too
    final_metrics = [m for m in final_metrics if m.agg != "ratio" or {m.numerator, m.denominator} <= names_left]

    catalog = SemanticCatalog(version=version, name="Session catalog", tables=final_tables, metrics=final_metrics,
                              dimensions=final_dims, joins=final_joins)
    _resolve_synonyms(catalog)
    in_scope_ids = set(concept_ids(catalog))
    catalog.synonyms = [Synonym(term=s.term, concept=s.concept) for s in curated.synonyms if s.concept in in_scope_ids]
    problems = validate_catalog(catalog)
    if problems:
        raise AssemblyError("The assembled catalog is inconsistent: " + "; ".join(problems[:10]))
    return catalog, _report(catalog, layers)


def _resolve_synonyms(c: SemanticCatalog) -> None:
    """A term may mean one concept only. Stronger concepts keep their terms; weaker ones lose clashing ones."""
    concepts = sorted([*c.tables, *c.metrics, *c.dimensions], key=lambda x: RANK[x.trust])
    owner: set[str] = set()
    for x in concepts:
        kept = []
        for term in x.synonyms:
            t = normalize_term(term)
            if t and t not in owner:
                owner.add(t)
                kept.append(term)
        x.synonyms = kept


def _report(c: SemanticCatalog, layers) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for kind, items in (("tables", c.tables), ("metrics", c.metrics), ("dimensions", c.dimensions), ("joins", c.joins)):
        counts[kind] = {t: sum(1 for i in items if i.trust == t) for t in RANK}
    looker = LookerSource()
    return {
        "counts": counts,
        "to_review": sum(v["inferred"] for v in counts.values()),
        "sources": [{"name": name, "items": len(i.tables) + len(i.metrics) + len(i.dimensions) + len(i.joins)}
                    for name, i in layers if name != "Looker"]
        + [{"name": "Looker", "items": 0, "status": looker.health()[1]}],
        "notes": [n for _, i in layers for n in i.notes],
    }
