"""Metadata retrieval: which metrics, dimensions, tables and columns a question is about.

Three signals, strongest first:
1. Exact terms: phrases in the question that are a concept's name, label or synonym (longest phrase wins,
   so "number of orders" is the metric, not the Orders table).
2. Values: words that are known values of a dimension ("north" -> Customer region = North).
3. Meaning: vector search over the Chroma index, for words the catalog doesn't list.

If the vector index can't be used, retrieval still answers from 1 and 2 and says so.
"""

import logging

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.schemas.catalog import normalize_term
from app.services.cortex import get_cortex
from app.services.ontology import get_ontology
from app.services.retrieval.metadata_store import load_metadata
from app.services.retrieval.vector import VectorIndex

log = logging.getLogger(__name__)
MAX_NGRAM = 4
UNGOVERNED_WEIGHT = 0.8  # raw columns rank below catalog concepts with the same similarity
STOP = {"a", "an", "the", "of", "by", "per", "for", "in", "on", "and", "or", "to", "what", "was", "is", "are",
        "were", "show", "me", "give", "list", "how", "many", "much", "which", "with", "from", "each", "every",
        "last", "this", "over", "between", "vs", "versus", "top", "all", "our", "my", "we", "did", "do", "does"}
# Time words are handled by query planning (grain and date range), so they aren't reported as unmatched.
TIME_WORDS = {"daily", "weekly", "monthly", "quarterly", "yearly", "annual", "annually", "day", "days", "week",
              "weeks", "month", "months", "quarter", "quarters", "year", "years", "today", "yesterday", "ytd",
              "mtd", "trend", "over", "time", "since", "until", "ago", "previous", "prior", "current", "next"}


class Candidate(BaseModel):
    id: str
    kind: str
    label: str
    table: str | None = None
    score: float
    governed: bool = True
    pii: bool = False
    reasons: list[str] = Field(default_factory=list)


class ValueMatch(BaseModel):
    dimension: str
    label: str
    value: str
    term: str


class RetrievalResult(BaseModel):
    question: str
    candidates: list[Candidate]
    values: list[ValueMatch]
    unmatched: list[str]  # words that matched nothing exactly
    mode: str  # "vector+exact" | "exact_only"
    ontology_source: str
    note: str = ""


def _ngrams(tokens: list[str], covered: set[int]):
    for n in range(min(MAX_NGRAM, len(tokens)), 0, -1):
        for i in range(len(tokens) - n + 1):
            span = set(range(i, i + n))
            if not span & covered:
                yield i, n, " ".join(tokens[i:i + n])


def retrieve(db: Session, question: str, k: int = 8) -> RetrievalResult:
    onto = get_ontology(db)
    tokens = normalize_term(question).split()
    found: dict[str, Candidate] = {}

    def add(cid: str, score: float, reason: str, **extra) -> None:
        node = onto.node(cid) or {}
        c = found.get(cid)
        if c is None:
            table = (onto.table_of(cid) or "").removeprefix("table:") or (node.get("name") if node.get("kind") == "table" else None)
            c = found[cid] = Candidate(id=cid, kind=node.get("kind", extra.get("kind", "column")),
                                       label=node.get("label") or extra.get("label", cid),
                                       table=table or extra.get("table"), score=score,
                                       governed=extra.get("governed", True),
                                       pii=bool((node.get("props") or {}).get("pii") or extra.get("pii")))
        c.score = max(c.score, score)
        if reason not in c.reasons:
            c.reasons.append(reason)

    # 1. exact terms, longest first
    covered: set[int] = set()
    for i, n, phrase in _ngrams(tokens, covered):
        if n == 1 and phrase in STOP:
            continue
        if set(range(i, i + n)) & covered:
            continue
        ids = onto.resolve_term(phrase)
        if ids:
            covered.update(range(i, i + n))
            for cid in ids:
                add(cid, 1.0, f"matches “{phrase}”")

    # 2. dimension values (from the saved BigQuery metadata; personal-data columns are never sampled)
    values: list[ValueMatch] = []
    metadata = load_metadata(db)
    for d in onto.concepts("dimension"):
        p = d["props"]
        if p.get("pii") or p.get("type") != "string":
            continue
        table = onto.node(f"table:{p['table']}")
        meta = metadata.get(table["props"]["table"]) if table else None
        col = meta.column(p["column"]) if meta else None
        for raw in (col.sample_values if col and not col.is_pii else []):
            v = normalize_term(str(raw)).split()
            if not v:
                continue
            for i in range(len(tokens) - len(v) + 1):
                span = set(range(i, i + len(v)))
                if tokens[i:i + len(v)] == v and not span & covered:
                    covered.update(span)
                    values.append(ValueMatch(dimension=d["id"], label=d["label"], value=str(raw), term=" ".join(v)))
                    add(d["id"], 0.9, f"“{' '.join(v)}” is a {d['label'].lower()} value")

    # 3. meaning, from the vector index
    mode, note = "vector+exact", ""
    if tokens:
        try:
            hits = VectorIndex().query(get_cortex().embed([question])[0], n=k * 3)
            if not hits:
                mode, note = "exact_only", "The vector index is empty. Run: python -m app.cli index"
            min_similarity = get_settings().retrieval_min_similarity
            for h in hits:
                sim = h["similarity"]
                if sim < min_similarity:
                    continue
                governed = bool(h["meta"].get("governed", True))
                add(h["id"], round(sim * (1.0 if governed else UNGOVERNED_WEIGHT), 3), f"similar meaning ({sim:.2f})",
                    kind=h["meta"].get("kind"), label=h["meta"].get("label"), table=h["meta"].get("table"),
                    governed=governed, pii=h["meta"].get("pii", False))
        except Exception as exc:  # noqa: BLE001  (Chroma or Cortex unavailable: exact matching still works)
            log.warning("Vector search unavailable", exc_info=True)
            mode, note = "exact_only", f"Vector search is unavailable ({exc}); showing exact matches only."

    ranked = sorted(found.values(), key=lambda c: (-c.score, c.kind != "metric", c.label))
    exact = [c for c in ranked if c.score >= 0.9]
    rest = [c for c in ranked if c.score < 0.9][: max(0, k - len(exact))]
    unmatched = [t for i, t in enumerate(tokens)
                 if i not in covered and t not in STOP and t not in TIME_WORDS and not t.isdigit()]
    return RetrievalResult(question=question, candidates=exact + rest, values=values, unmatched=unmatched,
                           mode=mode, ontology_source=onto.source, note=note or onto.note)
