from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

logger = logging.getLogger("ai.embeddings")


class EmbeddingError(Exception):
    pass


class EmbeddingProvider(ABC):
    name = "base"
    dimension = 0

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.OLLAMA_BASE_URL.rstrip("/")
        self.model = self.settings.EMBEDDING_MODEL

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts, "truncate": False}
        try:
            with httpx.Client(timeout=httpx.Timeout(120)) as client:
                resp = client.post(f"{self.base_url}/api/embed", json=payload)
            resp.raise_for_status()
            embeddings = resp.json().get("embeddings") or []
            if not embeddings:
                raise EmbeddingError("Ollama embedding returned no vectors.")
            return embeddings
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Ollama embedding failed: {exc}") from exc

    def health_check(self) -> tuple[bool, str]:
        try:
            got = self.embed_batch(["ok"])
            self.dimension = len(got[0])
            return True, f"Connected · {self.model} (dim {self.dimension})"
        except EmbeddingError as exc:
            return False, str(exc)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    name = "external"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.EXTERNAL_PROVIDER_1_BASE_URL.rstrip("/")
        self.api_key = self.settings.EXTERNAL_PROVIDER_1_API_KEY
        self.model = self.settings.EMBEDDING_MODEL
        self.configured = bool(self.base_url and self.api_key)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.configured:
            raise EmbeddingError("External embedding provider is not configured.")
        payload = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=httpx.Timeout(120)) as client:
                resp = client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda d: d.get("index", 0))
            return [item["embedding"] for item in data]
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise EmbeddingError(f"External embedding failed: {exc}") from exc


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    kind = (settings.EMBEDDING_PROVIDER or "ollama").strip().lower()
    if kind == "external":
        _provider = OpenAICompatibleEmbeddingProvider()
    elif kind == "sentence_transformers":
        # Optional local alternative; requires sentence-transformers to be installed.
        _provider = _build_sentence_transformers()
    else:
        provider = OllamaEmbeddingProvider()
        ok, detail = provider.health_check()
        if not ok:
            logger.warning("Embedding model unavailable: %s", detail)
        _provider = provider
    return _provider


def _build_sentence_transformers():
    try:
        from sentence_transformers import SentenceTransformer, models  # type: ignore

        settings = get_settings()
        model = SentenceTransformer(settings.EMBEDDING_MODEL or "all-MiniLM-L6-v2")

        class STProvider(EmbeddingProvider):
            name = "sentence_transformers"

            def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return model.encode(texts).tolist()

        return STProvider()
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(f"Cannot start sentence-transformers provider: {exc}") from exc


def health_status() -> dict:
    provider = get_embedding_provider()
    try:
        dims = len(provider.embed_one("health probe"))
        return {"provider": provider.name, "model": getattr(provider, "model", ""), "healthy": True, "dimension": dims}
    except EmbeddingError as exc:
        return {"provider": provider.name, "healthy": False, "detail": str(exc)}