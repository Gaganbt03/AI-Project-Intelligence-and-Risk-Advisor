from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings
from app.services.ai import discovery

logger = logging.getLogger("ai.providers")

# Redaction used everywhere a credential would otherwise reach a response.
REDACTED = "***"


class AIProviderError(Exception):
    pass


def _mask_key(api_key: str | None) -> str:
    """Return a fixed-length redaction. No character of the secret is ever returned."""
    return REDACTED if (api_key or "").strip() else ""


# Public alias so routers can build masked status without importing a private.
mask_key = _mask_key


def _normalize_base(base: str) -> str:
    return (base or "").rstrip("/")


class BaseProvider(ABC):
    name = "base"
    kind = "base"
    key = "base"
    role = "extra"
    order = 0

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        timeout: float | None = None,
    ) -> str:
        """Synchronous generation; raises AIProviderError on failure.

        `timeout` is the per-attempt budget in seconds. When omitted the
        provider falls back to the total AI_REQUEST_TIMEOUT budget.
        """

    def health_check(self) -> tuple[bool, str]:
        """Returns (ok, detail). Subclasses implement or default to a generation probe."""
        try:
            sample = self.generate("Reply with the word OK.", temperature=0.0, timeout=self.probe_timeout())
            return ("OK" in (sample or "").upper()), "Responded"
        except AIProviderError as exc:
            return False, str(exc)

    def probe_timeout(self) -> float:
        settings = get_settings()
        return float(min(settings.AI_PROVIDER_ATTEMPT_TIMEOUT, 20))

    @property
    def model_available(self) -> bool:
        """True when a usable model id is known (pinned or already discovered)."""
        return bool((getattr(self, "model", "") or "").strip())

    @property
    def model_source(self) -> str:
        return "configured" if self.model_available else "none"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} {self.name}>"


class OllamaProvider(BaseProvider):
    kind = "ollama"
    key = "ollama"
    role = "primary"
    order = 1

    def __init__(self) -> None:
        self.settings = get_settings()
        self.name = "Ollama"
        self.base_url = self.settings.OLLAMA_BASE_URL.rstrip("/")
        self.model = self.settings.OLLAMA_MODEL
        self.index = 0
        self.configured = True
        self.api_key = ""

    def _post(self, url: str, payload: dict, timeout: float) -> dict:
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
                resp = client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Ollama request failed: {exc}") from exc

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        timeout: float | None = None,
    ) -> str:
        full = f"{system}\n\n{prompt}" if system else prompt
        temp = temperature if temperature is not None else self.settings.AI_TEMPERATURE
        payload = {
            "model": self.model,
            "prompt": full,
            "stream": False,
            "keep_alive": self.settings.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": temp,
                "num_ctx": self.settings.OLLAMA_NUM_CTX,
                "num_predict": self.settings.OLLAMA_NUM_PREDICT,
            },
        }
        if json_mode and self.settings.AI_JSON_MODE:
            payload["format"] = "json"
        data = self._post(
            f"{self.base_url}/api/generate",
            payload,
            timeout if timeout is not None else float(self.settings.AI_REQUEST_TIMEOUT),
        )
        text = (data.get("response") or "").strip()
        if not text:
            raise AIProviderError("Ollama returned an empty response.")
        return text

    def health_check(self) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=httpx.Timeout(5)) as client:
                resp = client.get(f"{self.base_url}/api/tags")
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            if not any(self.model in name for name in models):
                return False, f"Model '{self.model}' not pulled. Run: ollama pull {self.model}"
            return True, f"Connected · {self.model} available"
        except httpx.HTTPError as exc:
            return False, f"Unreachable: {exc}"


class OpenAICompatibleProvider(BaseProvider):
    """Generic OpenAI-compatible chat completions provider (Groq, Gemini, OpenRouter, HF, ...).

    A provider only needs a base URL and an API key. When its ``*_MODEL``
    setting is empty the model is discovered once from the provider's own
    model-list endpoint and cached for the process, so the user never has to
    look up a model id. An explicitly configured model always wins.
    """

    kind = "external"

    def __init__(
        self,
        index: int | None = None,
        *,
        key: str | None = None,
        role: str = "extra",
        order: int = 0,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        discoverer: Callable[[str, str, float | None], list[str]] | None = None,
    ) -> None:
        """Either pass a legacy EXTERNAL_PROVIDER_<index> slot, or explicit values."""
        self.settings = get_settings()
        self.index = index

        if index is not None:
            index_key = str(index)
            self.name = name or self.settings.__getattribute__(f"EXTERNAL_PROVIDER_{index_key}_NAME") or f"External {index}"
            self.raw_base = base_url if base_url is not None else self.settings.__getattribute__(f"EXTERNAL_PROVIDER_{index_key}_BASE_URL")
            self.api_key = api_key if api_key is not None else self.settings.__getattribute__(f"EXTERNAL_PROVIDER_{index_key}_API_KEY")
            self.model = model if model is not None else self.settings.__getattribute__(f"EXTERNAL_PROVIDER_{index_key}_MODEL")
            self.key = key or f"external_{index_key}"
        else:
            self.name = name or "External"
            self.raw_base = base_url or ""
            self.api_key = api_key or ""
            self.model = model or ""
            self.key = key or "external"

        self.role = role
        self.order = order
        self.base_url = _normalize_base(self.raw_base)
        self.endpoint = f"{self.base_url}/chat/completions" if self.base_url else ""
        # The model id the user pinned, if any. Empty means "discover one".
        self.explicit_model = (self.model or "").strip()
        self.discoverer = discoverer or self._default_discoverer()
        # A missing model is NOT a misconfiguration: discovery fills it in.
        self.configured = bool(self.base_url and (self.api_key or "").strip())

    def _default_discoverer(self) -> Callable[[str, str, float | None], list[str]]:
        vendor = discovery.vendor_for(self.key, self.base_url)
        return discovery.FETCHERS.get(vendor, discovery.discover_openai_style)

    # -- model resolution --------------------------------------------------- #

    @property
    def model_available(self) -> bool:
        """True when a model is pinned or has already been discovered."""
        return bool((self.model or "").strip() or self.discovered_model())

    @property
    def model_source(self) -> str:
        if self.explicit_model:
            return "configured"
        return "discovered" if self.discovered_model() else "none"

    def discovered_model(self) -> str:
        """Cached discovered model, if one was already resolved. No network."""
        if self.explicit_model:
            return ""
        return discovery.peek_model(self.key, self.base_url, self.api_key)

    def resolve_model(self, timeout: float | None = None) -> str:
        """Return the model id to call, discovering one when none is configured.

        A discovered model is confirmed with a single tiny request first: a
        provider's catalogue can list models it will not actually serve, so the
        catalogue alone is not trusted.
        """
        if self.explicit_model:
            self.model = self.explicit_model
            return self.explicit_model
        try:
            model = discovery.resolve_model(
                self.key,
                self.base_url,
                self.api_key,
                "",
                timeout=timeout,
                validate=lambda candidate: self._probe_model(
                    candidate, min(float(timeout or self.probe_timeout()), self.probe_timeout())
                ),
            )
        except discovery.ModelDiscoveryError as exc:
            raise AIProviderError(f"{self.name} model discovery failed: {exc}") from None
        self.model = model
        return model

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        timeout: float | None = None,
    ) -> str:
        if not self.configured:
            raise AIProviderError(f"{self.name} is not configured.")
        budget = timeout if timeout is not None else float(self.settings.AI_REQUEST_TIMEOUT)
        model = (self.model or "").strip() or self.resolve_model(budget)
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        temp = temperature if temperature is not None else self.settings.AI_TEMPERATURE
        payload = {"model": model, "messages": messages, "temperature": temp, "stream": False}
        if json_mode and self.settings.AI_JSON_MODE:
            payload["response_format"] = {"type": "json_object"}
        return self._chat(payload, budget, forget_on_error=True)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _chat(self, payload: dict, budget: float, *, forget_on_error: bool, require_content: bool = True) -> str:
        """POST an OpenAI-style chat payload and return the message content."""
        try:
            with httpx.Client(timeout=httpx.Timeout(budget)) as client:
                resp = client.post(self.endpoint, json=payload, headers=self._headers())
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            if forget_on_error:
                self._forget_discovered_model()
            raise AIProviderError(f"{self.name} request failed: {exc}") from None
        except ValueError as exc:
            raise AIProviderError(f"{self.name} returned a non-JSON response: {exc}") from None
        choices = body.get("choices") or [{}]
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = (content or "").strip()
        if not content and require_content:
            raise AIProviderError(f"{self.name} returned an empty response.")
        return content

    def _probe_model(self, model: str, budget: float) -> bool:
        """One-token confirmation that the provider will actually serve `model`.

        Only the HTTP outcome matters here: a reasoning model can legitimately
        spend a tiny `max_tokens` budget without emitting any text, which says
        nothing about whether the model id is usable.
        """
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "temperature": 0.0,
            "max_tokens": 8,
            "stream": False,
        }
        try:
            self._chat(payload, budget, forget_on_error=False, require_content=False)
            return True
        except AIProviderError:
            return False

    def _forget_discovered_model(self) -> None:
        """A discovered model that errors may have been retired upstream, so the
        next request re-queries the model list instead of reusing it."""
        if self.explicit_model:
            return
        discovery.invalidate(self.key, self.base_url, self.api_key)
        self.model = ""

    def health_check(self) -> tuple[bool, str]:
        if not self.configured:
            return False, "Not Configured"
        try:
            self.resolve_model(self.probe_timeout())
            sample = self.generate("Reply with the word OK.", temperature=0.0, timeout=self.probe_timeout())
            return ("OK" in (sample or "").upper()), f"Connected · {self.model}"
        except AIProviderError as exc:
            return False, f"Error: {exc}"


# --------------------------------------------------------------------------- #
# Ordered fallback chain: Ollama -> Groq -> Gemini -> OpenRouter -> Hugging Face
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProviderSlot:
    """One position in the deterministic fallback chain."""

    key: str
    label: str
    role: str
    order: int
    kind: str  # "ollama" | "external"
    index: int  # legacy EXTERNAL_PROVIDER_<index> slot, 0 for Ollama


CHAIN_SLOTS: tuple[ProviderSlot, ...] = (
    ProviderSlot("ollama", "Ollama", "primary", 1, "ollama", 0),
    ProviderSlot("groq", "Groq", "fallback_1", 2, "external", 1),
    ProviderSlot("gemini", "Gemini", "fallback_2", 3, "external", 2),
    ProviderSlot("external_1", "OpenRouter", "fallback_3", 4, "external", 1),
    ProviderSlot("external_2", "Hugging Face", "fallback_4", 5, "external", 2),
)

# EXTERNAL_PROVIDER_3 stays available as a free-form generic endpoint and is
# appended after the five named slots so nothing that worked before breaks.
EXTRA_SLOTS: tuple[ProviderSlot, ...] = (
    ProviderSlot("external_3", "OpenAI-Compatible", "extra", 6, "external", 3),
)

# Legacy external-slot index -> provider label, used to give the generic slots a
# friendly name when the user kept the shipped defaults in .env.
_DEFAULT_EXTERNAL_NAMES = {1: "OpenRouter", 2: "Hugging Face", 3: "OpenAI-Compatible"}


class ProviderBreaker:
    """Per-provider circuit breaker.

    After a failure the provider is fast-failed for `cooldown` seconds so a dead
    upstream does not consume the per-attempt budget on every single request.
    Cleared automatically once the cooldown expires.
    """

    def __init__(self, cooldown: float = 60.0) -> None:
        self.cooldown = float(cooldown)
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def configure(self, cooldown: float) -> None:
        with self._lock:
            self.cooldown = float(cooldown)

    def is_open(self, key: str) -> bool:
        with self._lock:
            opened = self._opened_at.get(key)
            if opened is None:
                return False
            if (time.monotonic() - opened) >= self.cooldown:
                self._opened_at.pop(key, None)
                return False
            return True

    def seconds_until_retry(self, key: str) -> int:
        with self._lock:
            opened = self._opened_at.get(key)
            if opened is None:
                return 0
            remaining = self.cooldown - (time.monotonic() - opened)
            return max(0, int(remaining) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._opened_at[key] = time.monotonic()

    def record_success(self, key: str) -> None:
        with self._lock:
            self._opened_at.pop(key, None)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._opened_at.clear()
            else:
                self._opened_at.pop(key, None)


_breaker = ProviderBreaker()


def get_breaker() -> ProviderBreaker:
    return _breaker


def reset_breaker(key: str | None = None) -> None:
    _breaker.reset(key)


def _slot_provider(slot: ProviderSlot, settings: Settings) -> BaseProvider:
    """Always builds the provider for a slot so the UI can show its model even
    when the slot is not configured yet."""
    if slot.key == "ollama":
        return OllamaProvider()
    if slot.key == "groq":
        return OpenAICompatibleProvider(
            key="groq",
            role=slot.role,
            order=slot.order,
            name="Groq",
            base_url=settings.GROQ_BASE_URL,
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
        )
    if slot.key == "gemini":
        return OpenAICompatibleProvider(
            key="gemini",
            role=slot.role,
            order=slot.order,
            name="Gemini",
            base_url=settings.GEMINI_BASE_URL,
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
        )
    # legacy EXTERNAL_PROVIDER_<n>_* slot
    provider = OpenAICompatibleProvider(slot.index, key=slot.key, role=slot.role, order=slot.order)
    provider.name = _display_name(slot.index, provider.name)
    return provider


def _display_name(idx: int, configured_name: str) -> str:
    """Keep a user-supplied EXTERNAL_PROVIDER_<n>_NAME, otherwise use the slot label."""
    if (configured_name or "").strip() and configured_name != f"External {idx}":
        return configured_name
    return _DEFAULT_EXTERNAL_NAMES.get(idx, configured_name or f"External {idx}")


# Frontend-facing provider states. A provider with a key and a base URL but no
# model yet is "discovering", never "not_configured".
PROVIDER_STATES = ("not_configured", "discovering", "online", "offline", "unavailable")


def _state(row: dict, healthy: bool, breaker_open: bool) -> str:
    if not row.get("configured"):
        return "not_configured"
    if not row.get("model_available"):
        return "discovering"
    if breaker_open:
        return "unavailable"
    return "online" if healthy else "offline"


def build_provider(key: str, settings: Settings | None = None) -> BaseProvider | None:
    """Build a single provider by chain key ('ollama', 'groq', 'gemini', 'external_1', ...).

    Returns None for an unknown key.
    """
    settings = settings or get_settings()
    for slot in CHAIN_SLOTS + EXTRA_SLOTS:
        if slot.key == key:
            return _slot_provider(slot, settings)
    return None


class ProviderManager:
    """Builds the ordered provider chain with automatic failover.

    Order: Ollama (primary) -> Groq -> Gemini -> OpenRouter -> Hugging Face.
    Slots without configuration are skipped, and a failing provider moves the
    request on to the next one. Requests are issued strictly sequentially -
    providers are never called in parallel for a normal request.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._breaker = get_breaker()
        self._breaker.configure(self.settings.AI_PROVIDER_BREAKER_COOLDOWN)
        self.fallback_enabled = bool(self.settings.AI_FALLBACK_ENABLED)

        self.providers: list[BaseProvider] = [OllamaProvider()]
        self.slot_keys: list[str] = ["ollama"]
        seen_endpoints = {_normalize_base(self.settings.OLLAMA_BASE_URL).lower()}

        candidates: list[BaseProvider] = []
        for slot in CHAIN_SLOTS[1:] + EXTRA_SLOTS:
            provider = _slot_provider(slot, self.settings)
            if provider.configured:
                candidates.append(provider)

        for provider in candidates:
            endpoint = _normalize_base(getattr(provider, "base_url", "")).lower()
            if endpoint and endpoint in seen_endpoints:
                # Same endpoint already in the chain - don't call (or bill) it twice.
                logger.info("Skipping %s: duplicate base url already in chain", provider.name)
                continue
            seen_endpoints.add(endpoint)
            self.providers.append(provider)
            self.slot_keys.append(provider.key)

        if not self.fallback_enabled:
            # Kill switch: primary only, no failover.
            self.providers = self.providers[:1]
            self.slot_keys = self.slot_keys[:1]

        self.primary = self.providers[0]

    # -- chain ------------------------------------------------------------- #

    def chain(self) -> list[BaseProvider]:
        return self.providers

    def get(self, key: str) -> BaseProvider | None:
        for provider in self.providers:
            if provider.key == key:
                return provider
        return None

    def describe_chain(self) -> list[dict]:
        """Full ordered chain (including unconfigured slots) for the settings UI.

        A slot with a key and a base URL but no model is reported as
        ``configured`` with ``model_available: false`` - its model is discovered
        on first use, not declared missing.
        """
        rows: list[dict] = []
        for slot in CHAIN_SLOTS + EXTRA_SLOTS:
            provider = _slot_provider(slot, self.settings)
            model = (provider.model or "").strip() or provider.discovered_model() or "—"
            rows.append(
                {
                    "key": slot.key,
                    "name": slot.label,
                    "kind": slot.kind,
                    "index": slot.index,
                    "role": slot.role,
                    "order": slot.order,
                    "model": model,
                    "model_available": provider.model_available,
                    "model_source": provider.model_source,
                    "configured": provider.configured,
                    "in_chain": slot.key in self.slot_keys,
                    "key_configured": bool((getattr(provider, "api_key", "") or "").strip()),
                    "key_masked": _mask_key(getattr(provider, "api_key", "")),
                }
            )
        return rows

    # -- generation -------------------------------------------------------- #

    def _attempt_timeout(self) -> float:
        return max(1.0, float(self.settings.AI_PROVIDER_ATTEMPT_TIMEOUT))

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> tuple[str, str, str]:
        """Returns (text, provider_name, model). Tries each provider in order."""
        settings = self.settings
        fallback_on = self.fallback_enabled
        # AI_REQUEST_TIMEOUT stays the total budget for this logical request.
        deadline = time.monotonic() + float(settings.AI_REQUEST_TIMEOUT)
        attempt_timeout = self._attempt_timeout()

        errors: list[str] = []
        for provider in self.chain():
            if fallback_on and self._breaker.is_open(provider.key):
                wait = self._breaker.seconds_until_retry(provider.key)
                logger.warning("Provider %s skipped: circuit open, retry in ~%ss", provider.name, wait)
                errors.append(f"{provider.name}: unavailable (retrying in ~{wait}s)")
                continue

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append("request time budget exhausted")
                break

            timeout = min(attempt_timeout, remaining) if fallback_on else remaining
            try:
                text = provider.generate(
                    prompt,
                    system=system,
                    temperature=temperature,
                    json_mode=json_mode,
                    timeout=timeout,
                )
            except AIProviderError as exc:
                logger.warning("Provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                if fallback_on:
                    self._breaker.record_failure(provider.key)
                continue

            self._breaker.record_success(provider.key)
            return text, provider.name, provider.model

        raise AIProviderError("All AI providers failed. | " + "; ".join(errors))

    # -- status ------------------------------------------------------------ #

    def status(self) -> list[dict]:
        out: list[dict] = []
        for row in self.describe_chain():
            provider = self.get(row["key"])
            if provider is None:
                # Configured but kept out of the chain (duplicate endpoint or the
                # fallback kill switch): probe a throwaway instance so the UI can
                # still show it, including its discovered model.
                provider = build_provider(row["key"], self.settings) if row["configured"] else None
            if provider is None:
                ok, detail = False, "Not Configured"
            else:
                ok, detail = provider.health_check()
                # A model discovered during the probe must be reflected in the row.
                row["model"] = (provider.model or "").strip() or row["model"]
                row["model_available"] = provider.model_available
                row["model_source"] = provider.model_source
            breaker_open = self._breaker.is_open(row["key"])
            if breaker_open:
                detail = f"Circuit open · retry in ~{self._breaker.seconds_until_retry(row['key'])}s"
            out.append(
                {
                    "key": row["key"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "index": row["index"],
                    "role": row["role"],
                    "order": row["order"],
                    "model": row["model"],
                    "model_available": row["model_available"],
                    "model_source": row["model_source"],
                    "configured": row["configured"],
                    "in_chain": row["in_chain"],
                    "key_configured": row["key_configured"],
                    "key_masked": row["key_masked"],
                    "healthy": ok,
                    "breaker_open": breaker_open,
                    "state": _state(row, ok, breaker_open),
                    "detail": detail,
                    "primary": row["role"] == "primary",
                }
            )
        return out


_manager: ProviderManager | None = None
_manager_lock = threading.Lock()


def get_provider_manager() -> ProviderManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ProviderManager()
    return _manager


def reset_provider_manager() -> None:
    """Drop the cached manager so the next call re-reads settings (.env / tests)."""
    global _manager
    with _manager_lock:
        _manager = None
    reset_discovery()


def reset_discovery() -> None:
    """Forget every discovered model (settings changed, or test isolation)."""
    discovery.catalog.reset()
