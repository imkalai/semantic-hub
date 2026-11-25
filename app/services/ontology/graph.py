"""In-memory ontology graph (NetworkX), built from the graph store or straight from the JSON catalog."""

from typing import Literal

import networkx as nx

from app.schemas.catalog import normalize_term
from app.services.catalog import GraphSpec, node_id
from app.services.text import singular

Source = Literal["graph_store", "json_fallback"]


class Ontology:
    def __init__(self, spec: GraphSpec, source: Source, note: str = ""):
        self.spec = spec
        self.source = source
        self.note = note  # why the fallback is in use, if it is
        self.g = nx.MultiDiGraph()
        for nid, n in spec.nodes.items():
            self.g.add_node(nid, **n)
        for e in spec.edges:
            self.g.add_edge(e["source"], e["target"], key=e["rel"], rel=e["rel"], **e["props"])
        # Approved joins as an undirected graph, for finding join paths between tables.
        self.joins = nx.Graph()
        self.joins.add_nodes_from(n for n, d in self.g.nodes(data=True) if d.get("kind") == "table")
        for s, t, d in self.g.edges(data=True):
            if d["rel"] == "JOINS":
                self.joins.add_edge(s, t, left=s, right=t, **{k: v for k, v in d.items() if k != "rel"})

    # ---- lookups ----
    def node(self, nid: str) -> dict | None:
        return self.g.nodes[nid] if nid in self.g else None

    def concepts(self, kind: str) -> list[dict]:
        out = []
        for nid, d in self.g.nodes(data=True):
            if d.get("kind") == kind:
                out.append({"id": nid, **d, "synonyms": self.synonyms_of(nid)})
        return sorted(out, key=lambda x: x["label"].lower())

    def synonyms_of(self, nid: str) -> list[str]:
        return sorted(self.g.nodes[src]["name"] for src, _, d in self.g.in_edges(nid, data=True)
                      if d["rel"] == "SYNONYM_OF")

    def resolve_term(self, term: str) -> list[str]:
        """Concept ids a business term refers to: by synonym, by name or by label. Exact matches, also
        trying the singular form ("refund reasons" -> "refund reason")."""
        t = normalize_term(term)
        if not t:
            return []
        found = self._resolve_exact(t)
        if not found:
            single = " ".join(singular(w) for w in t.split())
            if single != t:
                found = self._resolve_exact(single)
        return found

    def _resolve_exact(self, t: str) -> list[str]:
        sid = node_id("synonym", t)
        if sid in self.g:
            return [tgt for _, tgt, d in self.g.out_edges(sid, data=True) if d["rel"] == "SYNONYM_OF"]
        return [nid for nid, d in self.g.nodes(data=True)
                if d.get("kind") in ("metric", "dimension", "table")
                and t in (normalize_term(d["name"]), normalize_term(d.get("label", "")))]

    def table_of(self, concept_id: str) -> str | None:
        for _, tgt, d in self.g.out_edges(concept_id, data=True):
            if d["rel"] in ("MEASURES", "ATTRIBUTE_OF"):
                return tgt
        return None

    def join_path(self, from_table: str, to_table: str) -> list[dict] | None:
        """Approved joins linking two tables (fewest hops), or None if they can't be joined.

        Each step says which way it is walked ("from"/"to" table names) and whether walking it that way
        fans out: going from the "one" side to the "many" side repeats rows, so sums and counts taken on
        the starting table would be double counted."""
        a, b = (x if x.startswith("table:") else node_id("table", x) for x in (from_table, to_table))
        if a not in self.joins or b not in self.joins:
            return None
        try:
            path = nx.shortest_path(self.joins, a, b)
        except nx.NetworkXNoPath:
            return None
        steps = []
        for u, v in zip(path, path[1:]):
            edge = dict(self.joins.edges[u, v])
            forward = edge["left"] == u  # walking the join the way the catalog wrote it (left -> right)
            rel = edge.get("relationship", "many_to_one")
            fans_out = (rel == "one_to_many" and forward) or (rel == "many_to_one" and not forward)
            steps.append({**edge, "from": self.g.nodes[u]["name"], "to": self.g.nodes[v]["name"], "fans_out": fans_out})
        return steps

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for _, d in self.g.nodes(data=True):
            kinds[d.get("kind", "?")] = kinds.get(d.get("kind", "?"), 0) + 1
        return {
            "source": self.source, "note": self.note,
            "tables": kinds.get("table", 0), "metrics": kinds.get("metric", 0),
            "dimensions": kinds.get("dimension", 0), "synonyms": kinds.get("synonym", 0),
            "joins": self.joins.number_of_edges(),
        }
