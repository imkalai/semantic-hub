"""Turn the ontology and the BigQuery metadata into short text documents for the vector index.

Governed concepts (metrics, dimensions, tables) get one document each, written the way a business user
would describe them. Raw columns of catalog tables get documents too, marked as not governed, so retrieval
can say "this exists but isn't in the catalog yet". Personal-data columns never include sample values.
"""

import hashlib
from dataclasses import dataclass, field

from app.schemas.meta import TableMeta
from app.services.ontology.graph import Ontology

MAX_SAMPLES = 8


@dataclass
class Doc:
    id: str
    kind: str  # metric | dimension | table | column
    text: str
    meta: dict = field(default_factory=dict)  # stored with the vector; flat str/int/float/bool values only

    def hash(self, signature: str) -> str:
        return hashlib.sha256(f"{signature}\n{self.text}".encode()).hexdigest()


def _samples(meta: TableMeta | None, column: str) -> list[str]:
    col = meta.column(column) if meta else None
    if not col or col.is_pii:
        return []
    return [str(v) for v in col.sample_values[:MAX_SAMPLES]]


def _syn(c: dict) -> str:
    return f" Also called: {', '.join(c['synonyms'])}." if c["synonyms"] else ""


def build_documents(onto: Ontology, metadata: dict[str, TableMeta]) -> list[Doc]:
    tables = {t["name"]: t for t in onto.concepts("table")}

    def table_label(name: str) -> str:
        return tables[name]["label"] if name in tables else name

    def table_meta(name: str) -> TableMeta | None:
        t = tables.get(name)
        return metadata.get(t["props"]["table"]) if t else None

    docs: list[Doc] = []
    used: set[tuple[str, str]] = set()  # (table, column) covered by a concept

    for m in onto.concepts("metric"):
        p = m["props"]
        if p["agg"] == "ratio":
            how = f"{p['numerator']} divided by {p['denominator']}"
        elif p["agg"] == "count":
            how = f"count of {table_label(p['table']).lower()}"
        else:
            how = f"{p['agg'].replace('_', ' ')} of {p['column']}"
            used.add((p["table"], p["column"]))
        docs.append(Doc(m["id"], "metric",
                        f"Metric: {m['label']}. {p.get('description', '')}{_syn(m)} "
                        f"Calculated as {how} on {table_label(p['table'])}. Format: {p['format']}.",
                        {"label": m["label"], "table": p["table"], "governed": True}))

    for d in onto.concepts("dimension"):
        p = d["props"]
        used.add((p["table"], p["column"]))
        samples = _samples(table_meta(p["table"]), p["column"])
        extra = f" Time grains: {', '.join(p['time_grains'])}." if p.get("time_grains") else ""
        values = f" Example values: {', '.join(samples)}." if samples else ""
        docs.append(Doc(d["id"], "dimension",
                        f"Dimension: {d['label']} ({p['type']}). {p.get('description', '')}{_syn(d)} "
                        f"Splits {table_label(p['table'])} by {p['column']}.{extra}{values}",
                        {"label": d["label"], "table": p["table"], "governed": True, "pii": bool(p.get("pii"))}))

    for name, t in tables.items():
        p = t["props"]
        meta = metadata.get(p["table"])
        cols = ", ".join(c.name for c in meta.columns) if meta else "unknown (metadata not loaded)"
        docs.append(Doc(t["id"], "table",
                        f"Table: {t['label']} ({p['table']}). {p.get('description') or (meta.description if meta else '')}"
                        f"{_syn(t)} Columns: {cols}.",
                        {"label": t["label"], "table": name, "governed": True}))

    # Keys are plumbing, not things people ask about: leave them out of the column documents.
    for name, t in tables.items():
        used.add((name, t["props"]["primary_key"]))
    for _, _, j in onto.joins.edges(data=True):
        left, right = onto.g.nodes[j["left"]]["name"], onto.g.nodes[j["right"]]["name"]
        for k in j.get("on", []):
            used.update({(left, k["left"]), (right, k["right"])})

    for name, t in tables.items():
        meta = metadata.get(t["props"]["table"])
        if not meta:
            continue
        for c in meta.columns:
            if (name, c.name) in used or not c.is_supported:
                continue
            samples = [] if c.is_pii else [str(v) for v in c.sample_values[:MAX_SAMPLES]]
            values = f" Example values: {', '.join(samples)}." if samples else ""
            docs.append(Doc(f"column:{name}.{c.name}", "column",
                            f"Column {c.name} ({c.type}) in {t['label']}. {c.description}{values} "
                            f"Not in the semantic catalog.",
                            {"label": f"{t['label']} · {c.name}", "table": name, "governed": False, "pii": c.is_pii}))
    return docs
