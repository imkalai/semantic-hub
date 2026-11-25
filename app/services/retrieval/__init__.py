"""Metadata retrieval (diagram: Metadata Retrieval -> Chroma vector index, SQLite metadata store, Cortex embeddings)."""

from app.services.retrieval.indexer import IndexResult, build_index, embed_signature, index_status
from app.services.retrieval.retriever import Candidate, RetrievalResult, ValueMatch, retrieve
from app.services.retrieval.vector import VectorIndex, VectorIndexError

__all__ = ["IndexResult", "build_index", "embed_signature", "index_status", "Candidate", "RetrievalResult",
           "ValueMatch", "retrieve", "VectorIndex", "VectorIndexError"]
