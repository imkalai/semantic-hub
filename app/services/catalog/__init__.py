"""Load and check the JSON semantic catalog, and describe the graph it should produce."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.catalog import SemanticCatalog, normalize_term
from app.services.text import humanize


class CatalogError(Exception):
    """The catalog file is missing or inconsistent. The message lists what to fix."""


def node_id(kind: str, name: str) -> str:
    return f"{kind}:{name}"


@dataclass
class LoadedCatalog:
    catalog: SemanticCatalog
    path: Path
    sha256: str


def load_catalog(path: Path | None = None) -> LoadedCatalog:
    path = Path(path or get_settings().catalog_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"Couldn't read the catalog at {path}: {exc.strerror or exc}") from exc
    try:
        catalog = SemanticCatalog.model_validate(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"The catalog at {path} isn't valid JSON (line {exc.lineno}): {exc.msg}") from exc
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10])
        raise CatalogError(f"The catalog at {path} has errors: {problems}") from exc
    problems = validate_catalog(catalog)
    if problems:
        raise CatalogError(f"The catalog at {path} is inconsistent: " + "; ".join(problems))
    return LoadedCatalog(catalog=catalog, path=path, sha256=hashlib.sha256(raw).hexdigest())


def validate_catalog(c: SemanticCatalog) -> list[str]:
    """Cross-reference checks the schema alone can't do. Returns readable problems (empty = fine)."""
    problems: list[str] = []
    for kind, items in (("table", c.tables), ("metric", c.metrics), ("dimension", c.dimensions), ("join", c.joins)):
        seen: set[str] = set()
        for item in items:
            if item.name in seen:
                problems.append(f"{kind} '{item.name}' is defined twice")
            seen.add(item.name)

    tables = {t.name for t in c.tables}
    metrics = {m.name for m in c.metrics}
    for m in c.metrics:
        if m.table not in tables:
            problems.append(f"metric '{m.name}' uses unknown table '{m.table}'")
        if m.agg == "ratio":
            for part in ("numerator", "denominator"):
                ref = getattr(m, part)
                if not ref or ref not in metrics:
                    problems.append(f"metric '{m.name}' needs a {part} that is another metric")
                elif ref == m.name:
                    problems.append(f"metric '{m.name}' can't use itself as its {part}")
        elif m.agg != "count" and not m.column:
            problems.append(f"metric '{m.name}' ({m.agg}) needs a column")
    for d in c.dimensions:
        if d.table not in tables:
            problems.append(f"dimension '{d.name}' uses unknown table '{d.table}'")
        if d.type == "time" and not d.time_grains:
            problems.append(f"time dimension '{d.name}' needs at least one time grain")
    for j in c.joins:
        for side in (j.left, j.right):
            if side not in tables:
                problems.append(f"join '{j.name}' uses unknown table '{side}'")
        if j.left == j.right:
            problems.append(f"join '{j.name}' joins a table to itself")
        if not j.on:
            problems.append(f"join '{j.name}' has no join keys")

    concepts = set(concept_ids(c))
    owner: dict[str, str] = {}
    for term, concept in synonym_pairs(c):
        if concept not in concepts:
            problems.append(f"synonym '{term}' points at unknown concept '{concept}'")
            continue
        if not term:
            problems.append(f"an empty synonym is attached to '{concept}'")
            continue
        if owner.get(term, concept) != concept:
            problems.append(f"synonym '{term}' is used for both '{owner[term]}' and '{concept}'")
        owner[term] = concept
    return problems


def concept_ids(c: SemanticCatalog) -> list[str]:
    return ([node_id("table", t.name) for t in c.tables] + [node_id("metric", m.name) for m in c.metrics]
            + [node_id("dimension", d.name) for d in c.dimensions])


def synonym_pairs(c: SemanticCatalog) -> list[tuple[str, str]]:
    """(normalized term, concept id) for every synonym, including each concept's own name and label."""
    pairs: list[tuple[str, str]] = []
    for kind, items in (("table", c.tables), ("metric", c.metrics), ("dimension", c.dimensions)):
        for item in items:
            for term in item.synonyms:
                pairs.append((normalize_term(term), node_id(kind, item.name)))
    pairs += [(normalize_term(s.term), s.concept) for s in c.synonyms]
    return list(dict.fromkeys(pairs))


@dataclass
class GraphSpec:
    """The nodes and edges a catalog should produce in the graph store."""

    nodes: dict[str, dict] = field(default_factory=dict)  # id -> {kind, name, label, props}
    edges: list[dict] = field(default_factory=list)  # {source, target, rel, props}

    def edge_keys(self) -> set[tuple[str, str, str]]:
        return {(e["source"], e["target"], e["rel"]) for e in self.edges}


def graph_spec(c: SemanticCatalog) -> GraphSpec:
    spec = GraphSpec()

    def add(kind: str, name: str, label: str, props: dict) -> str:
        nid = node_id(kind, name)
        spec.nodes[nid] = {"kind": kind, "name": name, "label": label or humanize(name), "props": props}
        return nid

    for t in c.tables:
        add("table", t.name, t.label, t.model_dump(exclude={"name", "label", "synonyms"}))
    for m in c.metrics:
        nid = add("metric", m.name, m.label, m.model_dump(exclude={"name", "label", "synonyms"}))
        spec.edges.append({"source": nid, "target": node_id("table", m.table), "rel": "MEASURES", "props": {}})
        if m.agg == "ratio":
            for part in ("numerator", "denominator"):
                spec.edges.append({"source": nid, "target": node_id("metric", getattr(m, part)),
                                   "rel": "DERIVED_FROM", "props": {"part": part}})
    for d in c.dimensions:
        nid = add("dimension", d.name, d.label, d.model_dump(exclude={"name", "label", "synonyms"}))
        spec.edges.append({"source": nid, "target": node_id("table", d.table), "rel": "ATTRIBUTE_OF", "props": {}})
    for j in c.joins:
        if j.approved:
            spec.edges.append({"source": node_id("table", j.left), "target": node_id("table", j.right), "rel": "JOINS",
                               "props": {"name": j.name, "on": [k.model_dump() for k in j.on],
                                         "relationship": j.relationship, "trust": j.trust, "approval_key": j.key}})
    for term, concept in synonym_pairs(c):
        sid = add("synonym", term, term, {})
        spec.edges.append({"source": sid, "target": concept, "rel": "SYNONYM_OF", "props": {}})
    return spec


__all__ = ["CatalogError", "LoadedCatalog", "GraphSpec", "load_catalog", "validate_catalog", "graph_spec",
           "concept_ids", "synonym_pairs", "node_id"]
