"""Automatic model discovery for the external AI providers.

The user only supplies an API key. When the matching ``*_MODEL`` setting is
empty the model actually used for a request is looked up from the provider's own
model-list endpoint, filtered down to something that can do chat / text
generation, and cached for the lifetime of the backend process so the list is
never re-queried per request.

No model name is ever hardcoded here: every id returned by this module came
from a live provider API response.

Security: an API key is only ever placed in a request header (never in a URL,
so it cannot leak through a transport error message) and every error string is
scrubbed of the secret before it is raised or logged.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger("ai.discovery")


class ModelDiscoveryError(Exception):
    """No usable chat/text model could be discovered for a provider."""


# A model-list call is metadata only, so it gets a tighter budget than a
# generation call and never eats the whole per-attempt allowance.
MAX_DISCOVERY_TIMEOUT = 12.0

# Successful discoveries are reused for this long. Long enough that the list is
# effectively fetched once per process, short enough that a model retired
# upstream is eventually replaced.
MODEL_CACHE_TTL = 3600.0

# Failures are remembered briefly so a dead metadata endpoint is not hammered
# on every single request while the circuit breaker is open.
FAILURE_CACHE_TTL = 60.0

# A model catalogue can advertise models the caller cannot actually generate
# with, so the top candidates are each confirmed with one tiny request before
# being cached. This bounds the cost to a handful of near-zero-token calls, once
# per process per provider.
MAX_VALIDATION_PROBES = 4


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


@dataclass
class _Entry:
    model: str = ""
    error: str = ""
    stored_at: float = 0.0

    def expired(self, ttl: float) -> bool:
        return (time.monotonic() - self.stored_at) >= ttl


class ModelCatalog:
    """Process-wide cache of discovered models, keyed by a non-reversible
    fingerprint of (provider, base url, api key).

    The raw API key is never used as, or stored in, a cache key, and
    :meth:`stats` is safe to log or return over HTTP.
    """

    def __init__(
        self,
        ttl: float = MODEL_CACHE_TTL,
        failure_ttl: float = FAILURE_CACHE_TTL,
    ) -> None:
        self.ttl = float(ttl)
        self.failure_ttl = float(failure_ttl)
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self.list_calls = 0  # number of real model-list requests performed

    # -- keys -------------------------------------------------------------- #

    @staticmethod
    def fingerprint(provider_key: str, base_url: str, api_key: str) -> str:
        digest = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]
        return f"{provider_key}|{(base_url or '').rstrip('/').lower()}|{digest}"

    # -- reads / writes ----------------------------------------------------- #

    def get_model(self, fingerprint: str) -> str:
        """Cached model id, or ''. Reads never mutate the cache."""
        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None or not entry.model or entry.expired(self.ttl):
                return ""
            return entry.model

    def get_error(self, fingerprint: str) -> str:
        """Remembered discovery failure, or ''. Reads never mutate the cache."""
        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None or not entry.error or entry.expired(self.failure_ttl):
                return ""
            return entry.error

    def put_model(self, fingerprint: str, model: str) -> None:
        with self._lock:
            self._entries[fingerprint] = _Entry(model=model, stored_at=time.monotonic())

    def put_error(self, fingerprint: str, message: str) -> None:
        with self._lock:
            self._entries[fingerprint] = _Entry(error=message, stored_at=time.monotonic())

    def invalidate(self, fingerprint: str) -> None:
        with self._lock:
            self._entries.pop(fingerprint, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self.list_calls = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "cached": len(self._entries),
                "list_calls": self.list_calls,
                "ttl_seconds": self.ttl,
            }


catalog = ModelCatalog()


def peek_model(provider_key: str, base_url: str, api_key: str) -> str:
    """Return an already-discovered model without any network access."""
    if not (base_url or "").strip() or not (api_key or "").strip():
        return ""
    return catalog.get_model(ModelCatalog.fingerprint(provider_key, base_url, api_key))


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def _scrub(text: str, api_key: str) -> str:
    """Remove the API key (and anything that looks like one) from ``text``."""
    out = str(text or "")
    if api_key:
        out = out.replace(api_key, "***")
    for marker in ("gsk_", "sk-or-v1-", "sk-proj-", "hf_", "AIzaSy", "Bearer "):
        out = out.replace(marker, "***")
    return out



def _get_json(
    url: str,
    headers: dict[str, str],
    api_key: str,
    timeout: float,
) -> dict:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.get(url, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as exc:
        raise ModelDiscoveryError(_scrub(f"model list request failed: {exc}", api_key)) from None
    except ValueError:
        raise ModelDiscoveryError("model list response was not valid JSON") from None
    if not isinstance(payload, dict):
        raise ModelDiscoveryError("model list response had an unexpected shape")
    return payload


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _effective_timeout(timeout: float | None) -> float:
    try:
        value = float(timeout) if timeout is not None else MAX_DISCOVERY_TIMEOUT
    except (TypeError, ValueError):
        value = MAX_DISCOVERY_TIMEOUT
    return max(1.0, min(value, MAX_DISCOVERY_TIMEOUT))


def _openai_models_url(base_url: str) -> str:
    """``<base>/models`` for an OpenAI-compatible base URL."""
    return f"{(base_url or '').rstrip('/')}/models"


# --------------------------------------------------------------------------- #
# Candidate filtering / ranking
# --------------------------------------------------------------------------- #

# Model families that cannot serve a text/chat completion.
_BLOCKED = (
    "embed", "bge-", "e5-", "gte-", "minilm",
    "tts", "text-to-speech", "whisper", "transcri", "speech",
    "imagen", "dall-e", "stable-diffusion", "sdxl",
    "rerank", "reranker", "moderation", "guard", "safety-filter",
    "musicgen",
)

# Markers of a plain chat/text model. Absence is not disqualifying, it only
# pushes a model down the ranking.
_CHAT_HINTS = (
    "instruct", "-it", "chat", "gpt", "llama", "qwen", "mistral", "mixtral",
    "gemma", "gemini", "flash", "pro", "mini", "haiku", "sonnet", "opus",
    "nova", "command", "gpt-oss", "nemotron",
)

# Prefer stable, cheap, low-latency chat models for this application's
# short JSON-ish completions.
_UNSTABLE_HINTS = (
    "preview", "experimental", "-exp", "deprecated", "thinking", "reasoning",
    "-vl", "vision", "-image", "405b", "70b", "671b", "236b", "120b",
)


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _text_output_ok(entry: dict) -> bool:
    """True unless the payload explicitly says the model does not output text."""
    arch = entry.get("architecture")
    arch = arch if isinstance(arch, dict) else {}
    out = arch.get("output_modalities") or arch.get("output_modalities")
    if isinstance(out, list) and out:
        return any(str(m).lower() == "text" for m in out)
    return True


def _text_input_ok(entry: dict) -> bool:
    arch = entry.get("architecture")
    arch = arch if isinstance(arch, dict) else {}
    inp = arch.get("input_modalities") or arch.get("input_modalities")
    if isinstance(inp, list) and inp:
        return any(str(m).lower() == "text" for m in inp)
    return True


def _rank(model_id: str, extra: str = "") -> int:
    """Lower is better. 1000 means unusable."""
    text = f"{model_id} {extra}".lower()
    if any(bad in text for bad in _BLOCKED):
        return 1000
    score = 0
    if not any(hint in text for hint in _CHAT_HINTS):
        score += 2
    if any(hint in text for hint in _UNSTABLE_HINTS):
        score += 1
    # A moving alias ("...-latest") always tracks a model the provider still
    # serves, so it is the most durable pick when one is offered.
    if model_id.lower().endswith("-latest"):
        score -= 1
    return score


def _ranked(candidates: list[tuple[int, float, str]], context: str) -> list[str]:
    """Sort `(score, tiebreak, model_id)` rows, best first, dropping unusable ids."""
    usable = [row for row in candidates if row[0] < 1000]
    if not usable:
        raise ModelDiscoveryError(f"no text/chat capable model advertised by {context}")
    usable.sort()
    return [row[2] for row in usable]


# --------------------------------------------------------------------------- #
# Provider fetchers
# --------------------------------------------------------------------------- #


def discover_openai_style(base_url: str, api_key: str, timeout: float | None = None) -> list[str]:
    """Generic OpenAI-compatible ``GET <base>/models`` discovery.

    Used for Groq, OpenRouter and any extra OpenAI-compatible slot: all three
    answer ``{"object": "list", "data": [{"id": ...}, ...]}``. Returns the
    chat-capable model ids, best first.
    """
    url = _openai_models_url(base_url)
    payload = _get_json(url, _bearer(api_key), api_key, _effective_timeout(timeout))
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ModelDiscoveryError("model list endpoint returned no models")

    candidates: list[tuple[int, float, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        if not (_text_output_ok(entry) and _text_input_ok(entry)):
            continue
        pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
        cost = _as_float(pricing.get("prompt")) if pricing else 0.0
        score = _rank(model_id, str(entry.get("name") or ""))
        if score >= 1000:
            continue
        # Prefer chat models, then paid (more reliable) models, then cheap ones.
        free = 0 if cost > 0 else 1
        candidates.append((score, free * 1_000_000 + cost, model_id))

    return _ranked(candidates, "the provider")


def discover_gemini(base_url: str, api_key: str, timeout: float | None = None) -> list[str]:
    """Google's native ``GET <version-root>/models`` discovery.

    ``GEMINI_BASE_URL`` points at the OpenAI-compatible surface
    (``.../v1beta/openai``); the model list lives one level up, on the native
    ``v1beta`` root. The key travels in the ``x-goog-api-key`` header so it can
    never end up in a URL or a transport error message.

    Note: this catalogue advertises models the caller may not actually be
    allowed to generate with, so every candidate still has to be probed.
    """
    root = (base_url or "").rstrip("/")
    if root.endswith("/openai"):
        root = root[: -len("/openai")]
    url = f"{root}/models"
    headers = {"x-goog-api-key": api_key, "Accept": "application/json"}
    payload = _get_json(url, headers, api_key, _effective_timeout(timeout))

    models = payload.get("models")
    if not isinstance(models, list) or not models:
        raise ModelDiscoveryError("model list endpoint returned no models")

    candidates: list[tuple[int, float, str]] = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        full_name = str(entry.get("name") or "").strip()
        if not full_name:
            continue
        methods = entry.get("supportedGenerationMethods")
        if isinstance(methods, list) and methods and "generateContent" not in methods:
            continue  # embedding-only / specialised model
        model_id = full_name[len("models/") :] if full_name.startswith("models/") else full_name
        score = _rank(model_id, str(entry.get("displayName") or ""))
        if score >= 1000:
            continue
        candidates.append((score, 0.0, model_id))

    return _ranked(candidates, "Google AI Studio")


def discover_huggingface(base_url: str, api_key: str, timeout: float | None = None) -> list[str]:
    """Hugging Face Inference Providers discovery via ``GET <base>/models``.

    Only models with at least one ``live`` provider that emits text are usable;
    latency from the router payload breaks the tie so the fastest live
    provider is preferred.
    """
    url = _openai_models_url(base_url)
    payload = _get_json(url, _bearer(api_key), api_key, _effective_timeout(timeout))
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ModelDiscoveryError("model list endpoint returned no models")

    candidates: list[tuple[int, float, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        if not (_text_output_ok(entry) and _text_input_ok(entry)):
            continue
        providers = entry.get("providers")
        providers = providers if isinstance(providers, list) else []
        live = [
            p for p in providers
            if isinstance(p, dict) and str(p.get("status") or "live").lower() == "live"
        ]
        if providers and not live:
            continue  # every upstream is currently down for this model
        latencies = [_as_float(p.get("first_token_latency_ms")) for p in live]
        latency = min([x for x in latencies if x > 0], default=0.0)
        score = _rank(model_id)
        if score >= 1000:
            continue
        candidates.append((score, latency, model_id))

    return _ranked(candidates, "the Hugging Face router")


# Provider key -> fetcher, so ProviderManager can hand the right strategy to
# each slot without knowing anything about the individual vendor.
FETCHERS: dict[str, Callable[[str, str, float | None], list[str]]] = {
    "gemini": discover_gemini,
    "hugging_face": discover_huggingface,
}

# Slots whose env-configured base URL tells us which vendor they are, used to
# pick a fetcher for the free-form EXTERNAL_PROVIDER_<n> slots.
_VENDOR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("generativelanguage.googleapis.com", "aiplatform.googleapis.com"), "gemini"),
    (("router.huggingface.co", "api-inference.huggingface.co"), "hugging_face"),
)


def vendor_for(provider_key: str, base_url: str) -> str:
    """Best-effort vendor id for a slot, from the chain key then the base URL."""
    if provider_key in FETCHERS:
        return provider_key
    lowered = (base_url or "").lower()
    for needles, vendor in _VENDOR_HINTS:
        if any(n in lowered for n in needles):
            return vendor
    return "openai_style"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def resolve_model(
    provider_key: str,
    base_url: str,
    api_key: str,
    explicit_model: str,
    timeout: float | None = None,
    validate: Callable[[str], bool] | None = None,
) -> str:
    """Return the model to use for ``provider_key``.

    Explicit configuration always wins. Otherwise the provider's own model list
    is consulted (once per process) and the best chat/text model is returned.

    `validate` is called with each candidate model and must return True if the
    provider can actually serve it. A catalogue is not proof of availability -
    Google in particular advertises models that generation rejects - so
    candidates are probed in rank order and the first one that works is cached.
    """
    explicit = (explicit_model or "").strip()
    if explicit:
        return explicit

    if not (base_url or "").strip():
        raise ModelDiscoveryError("no base url configured")
    if not (api_key or "").strip():
        raise ModelDiscoveryError("no api key configured")

    fingerprint = ModelCatalog.fingerprint(provider_key, base_url, api_key)
    cached = catalog.get_model(fingerprint)
    if cached:
        return cached

    remembered = catalog.get_error(fingerprint)
    if remembered:
        raise ModelDiscoveryError(remembered)

    fetcher = FETCHERS.get(vendor_for(provider_key, base_url), discover_openai_style)
    try:
        candidates = fetcher(base_url, api_key, timeout)
    except ModelDiscoveryError as exc:
        return _remember_failure(fingerprint, provider_key, api_key, str(exc))
    except Exception as exc:  # noqa: BLE001 - discovery must never crash the app
        return _remember_failure(
            fingerprint, provider_key, api_key, f"unexpected error during model discovery: {exc}"
        )

    if not candidates:
        return _remember_failure(
            fingerprint, provider_key, api_key, "no text/chat capable model advertised by the provider"
        )

    model = _first_working(
        candidates, provider_key, api_key, validate, timeout or MAX_DISCOVERY_TIMEOUT
    )
    if model is None:
        return _remember_failure(
            fingerprint,
            provider_key,
            api_key,
            f"none of the {len(candidates)} advertised models could serve a request",
        )

    catalog.put_model(fingerprint, model)
    logger.info("Model discovery for %s selected: %s", provider_key, model)
    return model


def _first_working(
    candidates: list[str],
    provider_key: str,
    api_key: str,
    validate: Callable[[str], bool] | None,
    timeout: float,
) -> str | None:
    """Return the first candidate the provider actually serves, or None."""
    if validate is None:
        return candidates[0]
    for model in candidates[:MAX_VALIDATION_PROBES]:
        try:
            if validate(model):
                return model
            logger.info(
                "Model discovery for %s skipped %s: provider rejected it", provider_key, model
            )
        except Exception as exc:  # noqa: BLE001 - a probe must never crash the app
            logger.info(
                "Model discovery for %s probe of %s failed: %s",
                provider_key, model, _scrub(str(exc), api_key),
            )
    return None


def _remember_failure(fingerprint: str, provider_key: str, api_key: str, message: str) -> str:
    """Cache a scrubbed failure and return the reason (callers raise with it)."""
    safe = _scrub(message, api_key)
    catalog.put_error(fingerprint, safe)
    logger.warning("Model discovery failed for %s: %s", provider_key, safe)
    raise ModelDiscoveryError(safe) from None


def invalidate(provider_key: str, base_url: str, api_key: str) -> None:
    """Drop a cached model so the next attempt re-queries the provider."""
    catalog.invalidate(ModelCatalog.fingerprint(provider_key, base_url, api_key))


__all__ = [
    "ModelCatalog",
    "ModelDiscoveryError",
    "catalog",
    "discover_gemini",
    "discover_huggingface",
    "discover_openai_style",
    "invalidate",
    "peek_model",
    "resolve_model",
    "vendor_for",
]
