from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings


class VectorStore:
    """Persistent ChromaDB wrapper.

    One collection per project (`{prefix}_{project_id}`) which, together with
    document_id metadata, enforces project-level RAG isolation.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.path = settings.VECTOR_DB_PATH
        self.prefix = settings.VECTOR_COLLECTION_PREFIX
        self._client: Any | None = None

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self.path, settings=ChromaSettings(anonymized_telemetry=False)
            )
        return self._client

    def _collection_name(self, project_id: int) -> str:
        return f"{self.prefix}_{project_id}"

    def get_or_create(self, project_id: int):
        name = self._collection_name(project_id)
        return self.client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

    def add_document_chunks(
        self,
        project_id: int,
        document_id: int,
        original_name: str,
        chunks: list,
        embeddings: list[list[float]],
    ) -> int:
        """Insert chunks with their precomputed embeddings. Returns number added."""
        if not chunks:
            return 0
        coll = self.get_or_create(project_id)
        ids = [f"doc{document_id}_c{i + 1}" for i in range(len(chunks))]
        metadatas = [
            {
                "document_id": str(document_id),
                "project_id": str(project_id),
                "original_name": original_name or "",
                "chunk_index": str(c.chunk_index),
                "page": str(c.page_number) if c.page_number is not None else "",
                "section": (c.section or "")[:500],
                "row": str(c.row_number) if c.row_number is not None else "",
            }
            for c in chunks
        ]
        coll.add(ids=ids, documents=[c.text for c in chunks], embeddings=embeddings, metadatas=metadatas)
        return len(chunks)

    def delete_document(self, project_id: int, document_id: int) -> None:
        try:
            coll = self.get_or_create(project_id)
            coll.delete(where={"document_id": str(document_id)})
        except Exception:  # noqa: BLE001 - collection may not exist yet
            return

    def query(
        self, project_id: int, query_embedding: list[float], top_k: int = 6
    ) -> list[dict]:
        coll = self.get_or_create(project_id)
        result = coll.query(query_embeddings=[query_embedding], n_results=top_k)
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]
        out = []
        for i, doc_text in enumerate(documents):
            meta = metadatas[i] or {}
            out.append(
                {
                    "text": doc_text,
                    "score": float(distances[i]) if i < len(distances) else None,
                    "document_id": meta.get("document_id", ""),
                    "original_name": meta.get("original_name", ""),
                    "page": meta.get("page", ""),
                    "section": meta.get("section", ""),
                    "row": meta.get("row", ""),
                    "chunk_index": meta.get("chunk_index", ""),
                    "vector_id": ids[i] if i < len(ids) else "",
                }
            )
        return out

    def count(self, project_id: int) -> int:
        try:
            return self.get_or_create(project_id).count()
        except Exception:  # noqa: BLE001
            return 0

    def document_vector_count(self, project_id: int, document_id: int) -> int:
        coll = self.get_or_create(project_id)
        try:
            return coll.count(where={"document_id": str(document_id)})
        except Exception:  # noqa: BLE001
            return 0

    def reset_project(self, project_id: int) -> None:
        name = self._collection_name(project_id)
        try:
            self.client.delete_collection(name)
        except Exception:  # noqa: BLE001
            pass


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store