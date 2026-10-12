"""RAG relevance filtering and honest grounding (Section 19, Step 4).

Retrieval must not hand the model chunks that are unrelated to the question and
then call the answer "grounded". Chunks are scored by cosine similarity and the
ones below ``RAG_MIN_SIMILARITY`` are dropped before they reach any consumer.
"""

from __future__ import annotations

import pytest

from app.services import rag
from app.services.vector_store import similarity_from_distance


def test_similarity_from_distance_is_the_complement():
    assert similarity_from_distance(0.0) == pytest.approx(1.0)
    assert similarity_from_distance(1.0) == pytest.approx(0.0)
    assert similarity_from_distance(2.0) == pytest.approx(-1.0)
    assert similarity_from_distance(None) is None


def test_is_relevant_compares_score_to_the_floor():
    assert rag.is_relevant({"score": 0.8}, min_similarity=0.5) is True
    assert rag.is_relevant({"score": 0.5}, min_similarity=0.5) is True
    assert rag.is_relevant({"score": 0.2}, min_similarity=0.5) is False


def test_is_relevant_keeps_chunks_without_a_score():
    # An unscored chunk must not be discarded as if it were irrelevant.
    assert rag.is_relevant({"text": "no score"}) is True


def test_is_relevant_uses_configured_threshold(monkeypatch):
    monkeypatch.setattr(rag, "get_settings", lambda: type("S", (), {"RAG_MIN_SIMILARITY": 0.6})())
    assert rag.is_relevant({"score": 0.7}) is True
    assert rag.is_relevant({"score": 0.4}) is False


def test_retrieve_chunks_applies_threshold_and_keeps_order(monkeypatch):
    class _Provider:
        def embed_one(self, _query):
            return [0.0]

    class _Store:
        def query(self, _project_id, _embedding, top_k=6):
            return [
                {"text": "relevant", "score": 0.8, "distance": 0.2},
                {"text": "borderline", "score": 0.5, "distance": 0.5},
                {"text": "irrelevant", "score": 0.1, "distance": 0.9},
            ]

    monkeypatch.setattr(rag, "get_embedding_provider", lambda: _Provider())
    monkeypatch.setattr(rag, "get_vector_store", lambda: _Store())

    out = rag.retrieve_chunks(1, "question", min_similarity=0.5)
    assert [c["text"] for c in out] == ["relevant", "borderline"]


def test_retrieve_chunks_can_return_everything_when_filter_disabled(monkeypatch):
    class _Provider:
        def embed_one(self, _query):
            return [0.0]

    class _Store:
        def query(self, _project_id, _embedding, top_k=6):
            return [{"text": "only", "score": 0.05, "distance": 0.95}]

    monkeypatch.setattr(rag, "get_embedding_provider", lambda: _Provider())
    monkeypatch.setattr(rag, "get_vector_store", lambda: _Store())

    assert len(rag.retrieve_chunks(1, "question", min_similarity=-1.0)) == 1
