from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.ontology import get_ontology

router = APIRouter(prefix="/api/catalog", tags=["catalog"])
KINDS = ("metric", "dimension", "table")
# "catalog" = the curated catalog; "session:<id>" = a session's assembled catalog
SCOPE = Query("catalog", pattern=r"^(catalog|session:\d{1,9})$")


@router.get("")
def catalog(scope: str = SCOPE, db: Session = Depends(get_db)):
    """Every concept in the scope's ontology, grouped by kind, with where it was read from."""
    onto = get_ontology(db, scope)
    joins = [{"left": onto.g.nodes[d["left"]]["name"], "right": onto.g.nodes[d["right"]]["name"],
              "name": d.get("name", ""), "on": d.get("on", []), "relationship": d.get("relationship", ""),
              "trust": d.get("trust", "certified"), "key": d.get("approval_key")}
             for _, _, d in onto.joins.edges(data=True)]
    return {
        "scope": scope,
        "summary": onto.summary(),
        "metrics": onto.concepts("metric"),
        "dimensions": onto.concepts("dimension"),
        "tables": onto.concepts("table"),
        "joins": sorted(joins, key=lambda j: j["name"]),
    }


@router.get("/resolve")
def resolve(term: str = Query(..., min_length=1, max_length=200), scope: str = SCOPE, db: Session = Depends(get_db)):
    onto = get_ontology(db, scope)
    ids = onto.resolve_term(term)
    return {"term": term, "source": onto.source,
            "matches": [{"id": i, "kind": onto.g.nodes[i]["kind"], "label": onto.g.nodes[i]["label"],
                         "table": (onto.table_of(i) or "").removeprefix("table:") or None} for i in ids]}


@router.get("/join-path")
def join_path(from_table: str = Query(..., alias="from"), to_table: str = Query(..., alias="to"),
              scope: str = SCOPE, db: Session = Depends(get_db)):
    onto = get_ontology(db, scope)
    path = onto.join_path(from_table, to_table)
    if path is None:
        raise HTTPException(404, f"There's no approved join path between '{from_table}' and '{to_table}'.")
    return {"from": from_table, "to": to_table, "joins": path}
