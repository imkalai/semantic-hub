"""Chroma vector index, stored in the data folder. Vectors come from Cortex; Chroma never embeds by itself."""

import logging
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)
COLLECTION = "semantic_metadata"


class VectorIndexError(Exception):
    pass


@lru_cache
def _client(path: str):
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(path=path, settings=ChromaSettings(anonymized_telemetry=False))


def client():
    try:
        return _client(str(get_settings().chroma_dir))
    except ImportError as exc:
        raise VectorIndexError("chromadb isn't installed (pip install -r requirements.txt).") from exc
    except Exception as exc:  # noqa: BLE001
        raise VectorIndexError(f"Couldn't open the Chroma index: {exc}") from exc


class VectorIndex:
    """One collection. Its metadata records which embedding model made the vectors ('signature'), so vectors
    from a different model or size are never mixed: a changed signature means a full rebuild."""

    def __init__(self):
        self.client = client()

    def collection(self, signature: str | None = None, *, create: bool = True):
        try:
            col = self.client.get_collection(COLLECTION, embedding_function=None)
        except Exception:  # noqa: BLE001  (doesn't exist yet)
            if not create:
                return None
            return self.client.create_collection(COLLECTION, embedding_function=None,
                                                 metadata={"hnsw:space": "cosine", "signature": signature or ""})
        if signature is not None and (col.metadata or {}).get("signature") != signature:
            log.info("Embedding model changed; rebuilding the vector index")
            self.client.delete_collection(COLLECTION)
            return self.client.create_collection(COLLECTION, embedding_function=None,
                                                 metadata={"hnsw:space": "cosine", "signature": signature})
        return col

    def signature(self) -> str | None:
        col = self.collection(create=False)
        return (col.metadata or {}).get("signature") if col else None

    def count(self) -> int:
        col = self.collection(create=False)
        return col.count() if col else 0

    def ids(self) -> set[str]:
        col = self.collection(create=False)
        return set(col.get(include=[])["ids"]) if col else set()

    def reset(self) -> None:
        if self.collection(create=False) is not None:
            self.client.delete_collection(COLLECTION)

    def query(self, embedding: list[float], n: int, where: dict | None = None) -> list[dict]:
        col = self.collection(create=False)
        if col is None or col.count() == 0:
            return []
        r = col.query(query_embeddings=[embedding], n_results=min(n, col.count()), where=where,
                      include=["distances", "metadatas", "documents"])
        return [{"id": i, "similarity": 1.0 - d, "meta": m or {}, "text": t}
                for i, d, m, t in zip(r["ids"][0], r["distances"][0], r["metadatas"][0], r["documents"][0])]
