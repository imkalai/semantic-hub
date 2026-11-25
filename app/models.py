"""SQLite tables.

The ontology graph is stored as nodes and edges (the embedded replacement for Neo4j). Uniqueness and
foreign keys play the part of Neo4j's constraints: one node per id, one edge per (source, target, type),
and no edge can point at a node that doesn't exist.

Every graph and index row belongs to a scope: "catalog" for the curated catalog, "session:<id>" for a
session built from the tables someone picked. Scopes never see each other's rows.
"""

from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, ForeignKeyConstraint, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CATALOG_SCOPE = "catalog"


def session_scope(session_id: int) -> str:
    return f"session:{session_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OntologyNode(Base):
    __tablename__ = "ontology_nodes"

    scope: Mapped[str] = mapped_column(String(40), primary_key=True, default=CATALOG_SCOPE)
    id: Mapped[str] = mapped_column(String(300), primary_key=True)  # e.g. "metric:revenue"
    kind: Mapped[str] = mapped_column(String(30), index=True)  # table | metric | dimension | synonym
    name: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(255), default="")
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("scope", "kind", "name", name="uq_node_kind_name"),)


class OntologyEdge(Base):
    __tablename__ = "ontology_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(40), default=CATALOG_SCOPE)
    source: Mapped[str] = mapped_column(String(300))
    target: Mapped[str] = mapped_column(String(300))
    rel: Mapped[str] = mapped_column(String(40))  # MEASURES | ATTRIBUTE_OF | JOINS | SYNONYM_OF | DERIVED_FROM
    props: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        ForeignKeyConstraint(["scope", "source"], ["ontology_nodes.scope", "ontology_nodes.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["scope", "target"], ["ontology_nodes.scope", "ontology_nodes.id"], ondelete="CASCADE"),
        UniqueConstraint("scope", "source", "target", "rel", name="uq_edge"),
        Index("ix_edge_target", "scope", "target"),
    )


class MetadataTable(Base):
    """SQLite metadata store: the latest BigQuery details of each table in use (no table data).
    Shared by all scopes: a table is described once however many sessions use it."""

    __tablename__ = "metadata_tables"

    full_name: Mapped[str] = mapped_column(String(400), primary_key=True)  # project.dataset.table
    meta: Mapped[dict] = mapped_column(JSON)  # TableMeta as JSON, columns included
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class IndexDocument(Base):
    """One row per document in a scope's Chroma collection, so reindexing only embeds what changed and the
    healthcheck can tell whether Chroma and SQLite agree."""

    __tablename__ = "index_documents"

    scope: Mapped[str] = mapped_column(String(40), primary_key=True, default=CATALOG_SCOPE)
    id: Mapped[str] = mapped_column(String(400), primary_key=True)  # e.g. "metric:revenue", "column:orders.amount"
    kind: Mapped[str] = mapped_column(String(30), index=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    embedded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class IndexRun(Base):
    __tablename__ = "index_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(40), default=CATALOG_SCOPE)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    embed_signature: Mapped[str] = mapped_column(String(200), default="")
    ok: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class HubSession(Base):
    """A session: the BigQuery tables someone picked, and the catalog assembled for them.

    `catalog` is the merged catalog (curated + declared + inferred + approvals). It is what the session's
    graph is built from, and the session's fallback when its graph can't be used."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    tables: Mapped[list] = mapped_column(JSON)  # ["project.dataset.table", ...]
    catalog: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | ready | failed
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def scope(self) -> str:
        return session_scope(self.id)


class Approval(Base):
    """A person's decision on an inferred concept or join. Keyed by BigQuery names, not by session, so it
    applies to every session that uses the same tables, now and later."""

    __tablename__ = "approvals"

    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # metric | dimension | table | join
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected
    changes: Mapped[dict] = mapped_column(JSON, default=dict)  # label, synonyms, agg, relationship, ...
    tables: Mapped[list] = mapped_column(JSON, default=list)  # full table names involved
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class QuestionRecord(Base):
    """Each question asked, with what retrieval found and the plan, so it can be reopened later."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(40), default=CATALOG_SCOPE, index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # ready | clarify | invalid | blocked
    summary: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class BootstrapRun(Base):
    __tablename__ = "bootstrap_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(40), default=CATALOG_SCOPE)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    catalog_version: Mapped[str] = mapped_column(String(50), default="")
    catalog_hash: Mapped[str] = mapped_column(String(64), default="")
    ok: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
