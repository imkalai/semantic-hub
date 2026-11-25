"""SQLite tables.

The ontology graph is stored as nodes and edges (the embedded replacement for Neo4j). Uniqueness and
foreign keys play the part of Neo4j's constraints: one node per id, one edge per (source, target, type),
and no edge can point at a node that doesn't exist.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OntologyNode(Base):
    __tablename__ = "ontology_nodes"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)  # e.g. "metric:revenue"
    kind: Mapped[str] = mapped_column(String(30), index=True)  # table | metric | dimension | synonym
    name: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(255), default="")
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("kind", "name", name="uq_node_kind_name"),)


class OntologyEdge(Base):
    __tablename__ = "ontology_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(ForeignKey("ontology_nodes.id", ondelete="CASCADE"))
    target: Mapped[str] = mapped_column(ForeignKey("ontology_nodes.id", ondelete="CASCADE"))
    rel: Mapped[str] = mapped_column(String(40))  # MEASURES | ATTRIBUTE_OF | JOINS | SYNONYM_OF
    props: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint("source", "target", "rel", name="uq_edge"),
        Index("ix_edge_target", "target"),
    )


class MetadataTable(Base):
    """SQLite metadata store: the latest BigQuery details of each catalog table (no table data)."""

    __tablename__ = "metadata_tables"

    full_name: Mapped[str] = mapped_column(String(400), primary_key=True)  # project.dataset.table
    meta: Mapped[dict] = mapped_column(JSON)  # TableMeta as JSON, columns included
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class IndexDocument(Base):
    """One row per document in the Chroma index, so reindexing only embeds what changed and the
    healthcheck can tell whether Chroma and SQLite agree."""

    __tablename__ = "index_documents"

    id: Mapped[str] = mapped_column(String(400), primary_key=True)  # e.g. "metric:revenue", "column:orders.amount"
    kind: Mapped[str] = mapped_column(String(30), index=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    embedded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class IndexRun(Base):
    __tablename__ = "index_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    embed_signature: Mapped[str] = mapped_column(String(200), default="")
    ok: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class QuestionRecord(Base):
    """Each question asked, with what retrieval found and the plan, so it can be reopened later."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # ready | clarify | invalid
    summary: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class BootstrapRun(Base):
    __tablename__ = "bootstrap_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    catalog_version: Mapped[str] = mapped_column(String(50), default="")
    catalog_hash: Mapped[str] = mapped_column(String(64), default="")
    ok: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
