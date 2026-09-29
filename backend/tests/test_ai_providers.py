"""Provider chain, routing, model discovery and secret-handling tests.

Nothing here touches the network: provider `generate` calls and provider
model-list endpoints are replaced with stubs, so the deterministic fallback
order and every discovery path can be asserted exactly without real API keys.
"""

import json
import os

import httpx
import pytest

from app.config import Settings, get_settings
from app.services.ai import discovery
from app.services.ai import providers as P
from app.services.ai.discovery import ModelDiscoveryError, catalog
from app.services.ai.providers import (
    AIProviderError,
    OpenAICompatibleProvider,
    ProviderManager,
    build_provider,
    get_breaker,
    reset_provider_manager,
)

# Every secret used below. None of these strings may ever appear in a response.
SECRETS = {
    "GROQ_API_KEY": "gsk_TESTKEY_must_never_leak_AAA111",
    "GEMINI_API_KEY": "AIzaSyTESTKEY_must_never_leak_BBB222",
    "EXTERNAL_PROVIDER_1_API_KEY": "sk-or-v1-TESTKEY_must_never_leak_CCC333",
    "EXTERNAL_PROVIDER_2_API_KEY": "hf_TESTKEYmust_never_leak_DDD444",
}

PROVIDER_ENV = {
    # Fallback 1 - Groq. OLLAMA_* is deliberately left at the real project values
    # so the local embedding pipeline keeps working in later test modules.
    "GROQ_API_KEY": SECRETS["GROQ_API_KEY"],
    "GROQ_BASE_URL": "https://api.groq.com/openai/v1",
    "GROQ_MODEL": "openai/gpt-oss-20b",
    # fallback 2 - Gemini
    "GEMINI_API_KEY": SECRETS["GEMINI_API_KEY"],
    "GEMINI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai",
    "GEMINI_MODEL": "gemini-3.8-flash",
    # fallback 3 - OpenRouter
    "EXTERNAL_PROVIDER_1_NAME": "OpenRouter",
    "EXTERNAL_PROVIDER_1_BASE_URL": "https://openrouter.ai/api/v1",
    "EXTERNAL_PROVIDER_1_API_KEY": SECRETS["EXTERNAL_PROVIDER_1_API_KEY"],
    "EXTERNAL_PROVIDER_1_MODEL": "openai/gpt-4o-mini",
    # fallback 4 - Hugging Face
    "EXTERNAL_PROVIDER_2_NAME": "Hugging Face",
    "EXTERNAL_PROVIDER_2_BASE_URL": "https://router.huggingface.co/v1",
    "EXTERNAL_PROVIDER_2_API_KEY": SECRETS["EXTERNAL_PROVIDER_2_API_KEY"],
    "EXTERNAL_PROVIDER_2_MODEL": "openai/gpt-oss-120b",
    # generic extra slot stays unconfigured
    "EXTERNAL_PROVIDER_3_NAME": "OpenAI-Compatible",
    "EXTERNAL_PROVIDER_3_BASE_URL": "",
    "EXTERNAL_PROVIDER_3_API_KEY": "",
    "EXTERNAL_PROVIDER_3_MODEL": "",
    "AI_FALLBACK_ENABLED": "true",
    "AI_PROVIDER_ATTEMPT_TIMEOUT": "5",
    "AI_PROVIDER_BREAKER_COOLDOWN": "60",
    "AI_REQUEST_TIMEOUT": "180",
}

# The keys-only configuration: every model left blank, exactly what a user with
# API keys and no model names ends up with.
KEYS_ONLY_ENV = {
    **{k: v for k, v in PROVIDER_ENV.items() if not k.endswith("_MODEL")},
    "GROQ_MODEL": "",
    "GEMINI_MODEL": "",
    "EXTERNAL_PROVIDER_1_MODEL": "",
    "EXTERNAL_PROVIDER_2_MODEL": "",
    "EXTERNAL_PROVIDER_3_MODEL": "",
}


# provider key -> (name, model) as the chain must report it
EXPECTED_CHAIN = [
    ("ollama", "Ollama", "qwen2.5:3b"),
    ("groq", "Groq", "openai/gpt-oss-20b"),
    ("gemini", "Gemini", "gemini-3.8-flash"),
    ("external_1", "OpenRouter", "openai/gpt-4o-mini"),
    ("external_2", "Hugging Face", "openai/gpt-oss-120b"),
]


@pytest.fixture(autouse=True)
def _isolate_module_singletons():
    """Keep this module's env/settings patches from leaking into other test modules."""
    import app.services.embeddings as E

    E._provider = None
    yield
    E._provider = None
    get_breaker().reset()
    catalog.reset()
    get_settings.cache_clear()
    reset_provider_manager()


@pytest.fixture()
def chain(monkeypatch):
    """A fully configured five-provider chain with stubbed network calls."""
    for key, value in PROVIDER_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_provider_manager()
    get_breaker().reset()
    get_breaker().configure(60)

    manager = ProviderManager()
    calls: list[str] = []
    stub_all(manager, calls)
    manager.calls = calls  # type: ignore[attr-defined]
    yield manager


@pytest.fixture()
def keys_only(monkeypatch):
    """The supported keys-only setup: every API key present, every model blank."""
    for key, value in KEYS_ONLY_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_provider_manager()
    catalog.reset()
    get_breaker().reset()
    get_breaker().configure(60)
    yield
    catalog.reset()


STUB_BEHAVIOUR: dict[str, str] = {}


def configure(**behaviour: str) -> None:
    """Reset stub behaviour, e.g. configure(ollama='fail', groq='ok')."""
    STUB_BEHAVIOUR.clear()
    STUB_BEHAVIOUR.update(behaviour)


@pytest.fixture(autouse=True)
def _clear_behaviour():
    configure()
    yield
    configure()


def stub_all(manager: ProviderManager, calls: list[str] | None = None) -> None:
    """Replace every provider's network call with a stub driven by STUB_BEHAVIOUR."""
    for provider in manager.chain():
        def _generate(prompt, system=None, temperature=None, json_mode=False, timeout=None, _p=provider):
            if calls is not None:
                calls.append(_p.key)
            behaviour = STUB_BEHAVIOUR.get(_p.key, "ok")
            if behaviour == "fail":
                raise AIProviderError(f"{_p.name} simulated failure")
            if behaviour == "timeout":
                raise AIProviderError(f"{_p.name} timed out")
            return f"answer from {_p.key}"

        provider.generate = _generate


@pytest.fixture()
def offline_health(monkeypatch):
    """Make every health probe deterministic and offline."""
    monkeypatch.setattr(P.OllamaProvider, "health_check", lambda self: (True, "Connected · qwen2.5:3b available"))
    monkeypatch.setattr(
        P.OpenAICompatibleProvider,
        "health_check",
        lambda self: (True, f"Connected · {self.model}") if self.configured else (False, "Not Configured"),
    )
    monkeypatch.setattr(
        "app.services.embeddings.OllamaEmbeddingProvider.embed_one", lambda self, text: [0.0] * 768
    )
    return True


# --------------------------------------------------------------------------- #
# 1. Ollama primary
# --------------------------------------------------------------------------- #


def test_ollama_is_primary_and_first_in_chain(chain):
    rows = chain.describe_chain()
    assert [r["key"] for r in rows] == ["ollama", "groq", "gemini", "external_1", "external_2", "external_3"]
    assert rows[0]["role"] == "primary"
    assert rows[0]["name"] == "Ollama"
    assert chain.primary.key == "ollama"
    assert [p.key for p in chain.chain()][:5] == [k for k, _, _ in EXPECTED_CHAIN]
    # roles in order
    assert [r["role"] for r in rows[:5]] == ["primary", "fallback_1", "fallback_2", "fallback_3", "fallback_4"]
    assert [r["order"] for r in rows[:5]] == [1, 2, 3, 4, 5]


def test_ollama_answers_when_healthy(chain):
    configure(ollama="ok")
    text, provider, model = chain.generate("hello")
    assert text == "answer from ollama"
    assert provider == "Ollama"
    assert model == "qwen2.5:3b"
    # only the primary was contacted - fallbacks are never called in parallel
    assert chain.calls == ["ollama"]


# --------------------------------------------------------------------------- #
# 2-5. Each fallback step
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "down,expected",
    [
        (["ollama"], ("answer from groq", "Groq", "openai/gpt-oss-20b")),
        (["ollama", "groq"], ("answer from gemini", "Gemini", "gemini-3.8-flash")),
        (["ollama", "groq", "gemini"], ("answer from external_1", "OpenRouter", "openai/gpt-4o-mini")),
        (["ollama", "groq", "gemini", "external_1"], ("answer from external_2", "Hugging Face", "openai/gpt-oss-120b")),
    ],
)
def test_ordered_fallback_reaches_each_provider(chain, down, expected):
    configure(**{key: "fail" for key in down})
    assert chain.generate("hello") == expected


def test_fallback_stops_at_first_healthy_provider(chain):
    configure(ollama="fail", groq="ok")
    chain.generate("hello")
    assert chain.calls == ["ollama", "groq"]


# --------------------------------------------------------------------------- #
# 6. Provider unavailable -> next provider
# --------------------------------------------------------------------------- #


def test_unavailable_provider_moves_to_next_provider(chain):
    """Timeout, connection error and provider error all advance the chain."""
    configure(ollama="timeout", groq="fail", gemini="ok")
    text, provider, model = chain.generate("hello")
    assert (text, provider, model) == ("answer from gemini", "Gemini", "gemini-3.8-flash")
    assert chain.calls == ["ollama", "groq", "gemini"]


def test_unconfigured_providers_are_skipped_without_a_request(chain, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    manager = ProviderManager()
    assert [p.key for p in manager.chain()] == ["ollama", "external_1", "external_2"]
    calls: list[str] = []
    stub_all(manager, calls)
    configure(ollama="fail")
    text, provider, model = manager.generate("hi")
    assert provider == "OpenRouter"
    assert calls == ["ollama", "external_1"]  # groq/gemini never contacted


def test_circuit_breaker_fast_fails_a_dead_provider(chain):
    configure(ollama="fail", groq="ok")
    chain.generate("first")
    assert chain.calls == ["ollama", "groq"]
    assert get_breaker().is_open("ollama")

    # Second request: the dead primary is skipped without waiting for a timeout.
    chain.calls.clear()
    text, provider, _ = chain.generate("second")
    assert provider == "Groq"
    assert chain.calls == ["groq"]

    get_breaker().reset("ollama")
    chain.calls.clear()
    configure(ollama="ok")
    assert chain.generate("third")[1] == "Ollama"
    assert chain.calls == ["ollama"]


def test_per_attempt_timeout_is_bounded(chain):
    """A dead provider cannot consume the whole AI_REQUEST_TIMEOUT budget."""
    seen_timeouts: list[float] = []

    for provider in chain.chain():
        def _generate(prompt, system=None, temperature=None, json_mode=False, timeout=None, _p=provider):
            seen_timeouts.append(timeout)
            raise AIProviderError(f"{_p.name} down")

        provider.generate = _generate

    with pytest.raises(AIProviderError):
        chain.generate("hello")

    assert seen_timeouts, "expected at least one attempt"
    # AI_PROVIDER_ATTEMPT_TIMEOUT=5, AI_REQUEST_TIMEOUT=180
    assert all(0 < t <= 5 for t in seen_timeouts)
    # total budget is never exceeded
    assert sum(seen_timeouts) <= 180


def test_fallback_kill_switch_pins_to_primary(chain, monkeypatch):
    monkeypatch.setenv("AI_FALLBACK_ENABLED", "false")
    get_settings.cache_clear()
    manager = ProviderManager()
    assert [p.key for p in manager.chain()] == ["ollama"]
    stub_all(manager)
    assert manager.generate("hi") == ("answer from ollama", "Ollama", "qwen2.5:3b")


# --------------------------------------------------------------------------- #
# 7. All providers unavailable -> clear error
# --------------------------------------------------------------------------- #


def test_all_providers_unavailable_raises_clear_error(chain):
    configure(**{key: "fail" for key, _, _ in EXPECTED_CHAIN})
    with pytest.raises(AIProviderError) as excinfo:
        chain.generate("hello")
    message = str(excinfo.value)
    assert message.startswith("All AI providers failed.")
    for name in ("Ollama", "Groq", "Gemini", "OpenRouter", "Hugging Face"):
        assert name in message, f"{name} missing from the error report"
    assert chain.calls == ["ollama", "groq", "gemini", "external_1", "external_2"]


def test_error_message_never_contains_secrets(chain):
    configure(**{key: "fail" for key, _, _ in EXPECTED_CHAIN})
    with pytest.raises(AIProviderError) as excinfo:
        chain.generate("hello")
    for secret in SECRETS.values():
        assert secret not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 8. API keys are never returned
# --------------------------------------------------------------------------- #


def test_status_payload_never_exposes_api_keys(chain, offline_health):
    body = chain.status()
    assert body, "expected a status payload"

    forbidden_fields = {"api_key", "apiKey", "api-key", "token", "secret", "authorization", "password"}
    secret_prefixes = ("sk-or-v1", "gsk_", "AIzaSy", "hf_TEST", "sk-")

    def walk(node):
        if isinstance(node, dict):
            for field, value in node.items():
                lowered = field.lower()
                assert lowered not in forbidden_fields, f"credential field leaked: {field}"
                assert "apikey" not in lowered.replace("_", ""), f"credential field leaked: {field}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            for secret in SECRETS.values():
                assert secret not in node, "raw API key present in status payload"
            for prefix in secret_prefixes:
                assert prefix not in node, f"key-like prefix {prefix!r} present in status payload"

    walk(body)

    by_key = {row["key"]: row for row in body}
    for key in ("groq", "gemini", "external_1", "external_2"):
        assert by_key[key]["key_configured"] is True, f"{key} should report a configured key"
        assert by_key[key]["key_masked"] == "***", f"{key} key must be masked"
    for row in body:
        for field in ("configured", "healthy", "role", "order", "model", "name", "detail", "key_masked"):
            assert field in row
        # Ollama needs no key; unconfigured slots report no key.
        assert row["key_masked"] in ("***", "")


def test_build_provider_keeps_secrets_out_of_repr_and_str(chain):
    for provider in chain.chain():
        endpoint = getattr(provider, "endpoint", "")
        text = f"{provider!r} {provider.name} {provider.model} {endpoint}"
        for secret in SECRETS.values():
            assert secret not in text


def test_ai_providers_endpoint_never_returns_api_keys(client, admin_headers, chain, offline_health):
    r = client.get("/api/admin/settings/ai-providers", headers=admin_headers)
    assert r.status_code == 200
    raw = r.text
    for secret in SECRETS.values():
        assert secret not in raw
    for needle in ("sk-or-v1", "gsk_", "AIzaSy", "hf_TEST", "api_key", "apiKey", "Authorization", "Bearer"):
        assert needle not in raw
    body = r.json()
    names = [p["name"] for p in body["providers"]]
    for expected in ("Ollama", "Groq", "Gemini", "OpenRouter", "Hugging Face"):
        assert expected in names
    roles = [p["role"] for p in body["providers"][:5]]
    assert roles == ["primary", "fallback_1", "fallback_2", "fallback_3", "fallback_4"]


def test_test_endpoint_accepts_every_chain_key(client, admin_headers, chain, offline_health):
    for key in ("ollama", "groq", "gemini", "external_1", "external_2"):
        r = client.post(f"/api/admin/settings/ai-providers/{key}/test", headers=admin_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["key"] == key
        assert "healthy" in body and "detail" in body
        for secret in SECRETS.values():
            assert secret not in r.text
    bad = client.post("/api/admin/settings/ai-providers/external_99/test", headers=admin_headers)
    assert bad.status_code == 400
    worse = client.post("/api/admin/settings/ai-providers/not-a-provider/test", headers=admin_headers)
    assert worse.status_code == 400


# --------------------------------------------------------------------------- #
# 9. Embeddings remain Ollama / nomic-embed-text
# --------------------------------------------------------------------------- #


def test_embeddings_stay_on_ollama_nomic_embed_text(chain):
    from app.services.embeddings import OllamaEmbeddingProvider, get_embedding_provider

    settings = get_settings()
    assert settings.EMBEDDING_PROVIDER == "ollama"
    assert settings.EMBEDDING_MODEL == "nomic-embed-text"

    provider = get_embedding_provider()
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.name == "ollama"
    assert provider.model == "nomic-embed-text"
    assert provider.base_url == settings.OLLAMA_BASE_URL.rstrip("/")


def test_no_cloud_embedding_provider_is_reachable(chain, monkeypatch):
    """Even with every cloud provider configured, nothing embeds via them."""
    import app.services.embeddings as E

    E._provider = None
    try:
        provider = E.get_embedding_provider()
        assert provider.name == "ollama"
        assert provider.model == "nomic-embed-text"
        assert not isinstance(provider, E.OpenAICompatibleEmbeddingProvider)
        assert settings_has_no_cloud_embedding_route()
    finally:
        E._provider = None


def settings_has_no_cloud_embedding_route() -> bool:
    """Guard rail: the LLM chain must not have leaked into embedding config."""
    settings = get_settings()
    assert settings.EMBEDDING_PROVIDER.lower() in ("ollama", "sentence_transformers")
    return True


def test_embedding_status_endpoint_reports_ollama(client, admin_headers, chain, offline_health):
    r = client.get("/api/admin/settings/ai-providers", headers=admin_headers)
    assert r.status_code == 200
    embedding = r.json()["embedding"]
    assert embedding["provider"] == "ollama"
    assert embedding["model"] == "nomic-embed-text"
    assert embedding["dimension"] == 768

    assert embedding["dimension"] == 768


# =========================================================================== #
# 10-13. Automatic model discovery
#
# Every model-list endpoint and every chat call below is served by an
# in-process httpx mock, so these tests need no real API keys and no network.
# =========================================================================== #

# Canned provider catalogues. Each mirrors the real payload shape of that
# provider, including entries that MUST be filtered out.
GROQ_CATALOGUE = {
    "object": "list",
    "data": [
        {"id": "whisper-large-v3", "object": "model"},                 # speech only
        {"id": "meta-llama/llama-guard-4-12b", "object": "model"},     # guardrail
        {"id": "openai/gpt-oss-120b", "object": "model"},              # 120b -> deprioritised
        {"id": "llama-3.3-70b-versatile", "object": "model"},           # 70b  -> deprioritised
        {"id": "qwen/qwen3-32b", "object": "model"},                    # best chat model
    ],
}

GEMINI_CATALOGUE = {
    "models": [
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent", "countTokens"]},
        {
            "name": "models/gemini-2.0-flash",
            "displayName": "Gemini 2.0 Flash",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {
            "name": "models/gemini-2.5-pro",
            "displayName": "Gemini 2.5 Pro",
            "supportedGenerationMethods": ["generateContent"],
        },
    ]
}

OPENROUTER_CATALOGUE = {
    "data": [
        {
            "id": "img/vision-only",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
            "pricing": {"prompt": "0.001", "completion": "0.002"},
        },
        {
            "id": "vec/embedding-thing",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["embedding"]},
            "pricing": {"prompt": "0.0000001", "completion": "0"},
        },
        {"id": "cheap/reranker-v2", "pricing": {"prompt": "0", "completion": "0"}},
        {
            "id": "free/chat-free",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "vendor/paid-chat-instruct",
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.000002", "completion": "0.000004"},
        },
    ]
}

HUGGINGFACE_CATALOGUE = {
    "object": "list",
    "data": [
        {
            "id": "owner/embed-model",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["embedding"]},
            "providers": [{"provider": "novita", "status": "live"}],
        },
        {
            "id": "owner/all-down-chat",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "providers": [{"provider": "novita", "status": "down"}],
        },
        {
            "id": "owner/slow-chat-instruct",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "providers": [{"provider": "novita", "status": "live", "first_token_latency_ms": 900}],
        },
        {
            "id": "owner/fast-chat-instruct",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "providers": [{"provider": "novita", "status": "live", "first_token_latency_ms": 120}],
        },
    ]
}

CATALOGUES = {
    "groq": GROQ_CATALOGUE,
    "gemini": GEMINI_CATALOGUE,
    "external_1": OPENROUTER_CATALOGUE,
    "external_2": HUGGINGFACE_CATALOGUE,
}

# The model each provider must select out of the catalogue above.
DISCOVERED = {
    "groq": "qwen/qwen3-32b",
    "gemini": "gemini-2.0-flash",
    "external_1": "vendor/paid-chat-instruct",
    "external_2": "owner/fast-chat-instruct",
}

# chain key -> the env var that pins a model for that slot
MODEL_ENV = {
    "groq": "GROQ_MODEL",
    "gemini": "GEMINI_MODEL",
    "external_1": "EXTERNAL_PROVIDER_1_MODEL",
    "external_2": "EXTERNAL_PROVIDER_2_MODEL",
}

# chain key -> that provider's own secret
SECRETS_OF = {
    "groq": SECRETS["GROQ_API_KEY"],
    "gemini": SECRETS["GEMINI_API_KEY"],
    "external_1": SECRETS["EXTERNAL_PROVIDER_1_API_KEY"],
    "external_2": SECRETS["EXTERNAL_PROVIDER_2_API_KEY"],
}


class FakeProviderAPI:
    """In-process stand-in for one provider's OpenAI-compatible API."""

    def __init__(self, catalogue, *, list_status=200, chat_status=200, chat_text="OK"):
        self.catalogue = catalogue
        self.list_status = list_status
        self.chat_status = chat_status
        self.chat_text = chat_text
        self.list_requests = []
        self.chat_requests = []

    def __call__(self, request):
        if request.method == "GET":
            self.list_requests.append(request)
            return httpx.Response(self.list_status, json=self.catalogue)
        self.chat_requests.append(request)
        return httpx.Response(
            self.chat_status, json={"choices": [{"message": {"content": self.chat_text}}]}
        )

    # -- assertion helpers -------------------------------------------------- #

    @property
    def list_urls(self):
        return [str(r.url) for r in self.list_requests]

    def carries(self, secret):
        """True when `secret` was sent in a header of any model-list request."""
        return any(
            secret in value
            for request in self.list_requests
            for value in request.headers.values()
        )

    @property
    def body_models(self):
        return [json.loads(r.content)["model"] for r in self.chat_requests]


# Captured before any monkeypatching so patched factories never recurse.
_REAL_HTTPX_CLIENT = httpx.Client


def install_api(monkeypatch, api):
    """Route every httpx client the app builds to a single `api`, no real sockets."""
    _install_transport(monkeypatch, api)
    return api


def install_api_timed(monkeypatch, api):
    """Same as install_api but also returns the list of timeouts passed to clients."""
    return _install_transport(monkeypatch, api)


def _install_transport(monkeypatch, *apis, **by_key):
    """One mock transport that dispatches by host to the right FakeProviderAPI."""
    by_host = {}
    for key, api in by_key.items():
        by_host[httpx.URL(_base_for(key)).host] = api
    for api in apis:
        for key in CATALOGUES:
            by_host.setdefault(httpx.URL(_base_for(key)).host, api)

    timeouts = []

    def handler(request):
        api = by_host.get(request.url.host)
        if api is None:
            return httpx.Response(404, json={"error": "unmocked host"})
        return api(request)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        if "timeout" in kwargs:
            timeouts.append(kwargs["timeout"])
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return timeouts


def _base_for(key):
    settings = get_settings()
    return {
        "groq": settings.GROQ_BASE_URL,
        "gemini": settings.GEMINI_BASE_URL,
        "external_1": settings.EXTERNAL_PROVIDER_1_BASE_URL,
        "external_2": settings.EXTERNAL_PROVIDER_2_BASE_URL,
    }[key]


def _always_fail(*args, **kwargs):
    raise AIProviderError("simulated failure")


def slot(key):
    provider = build_provider(key)
    assert provider is not None, key
    return provider


def _stub_ollama(manager):
    """Replace the primary with a deterministic stub so the cloud slots are reached."""
    for provider in manager.chain():
        if provider.key == "ollama":
            provider.generate = _always_fail


# --------------------------------------------------------------------------- #
# 16. Empty model fields are accepted (they are not "Not Configured")
# --------------------------------------------------------------------------- #


def test_blank_model_fields_are_accepted_as_configured(keys_only):
    manager = ProviderManager()
    rows = {r["key"]: r for r in manager.describe_chain()}

    for key in ("groq", "gemini", "external_1", "external_2"):
        row = rows[key]
        assert row["configured"] is True, f"{key} must be configured with only an API key"
        assert row["key_configured"] is True
        assert row["model_available"] is False
        assert row["model_source"] == "none"
        # The placeholder is the backend's "no model yet" marker.
        assert row["model"].strip() not in ("", "0", "None")

    # And they are all part of the live chain, still in the required order.
    assert [p.key for p in manager.chain()] == [
        "ollama", "groq", "gemini", "external_1", "external_2",
    ]


def test_blank_model_chain_reports_discovering_not_not_configured(
    client, admin_headers, monkeypatch, keys_only
):
    monkeypatch.setattr(
        discovery, "resolve_model", _refuse_discovery
    )
    r = client.get("/api/admin/settings/ai-providers", headers=admin_headers)
    assert r.status_code == 200
    by_key = {p["key"]: p for p in r.json()["providers"]}
    for key in ("groq", "gemini", "external_1", "external_2"):
        assert by_key[key]["configured"] is True
        assert by_key[key]["state"] == "discovering", by_key[key]
        assert by_key[key]["state"] != "not_configured"
    assert by_key["external_3"]["state"] == "not_configured"  # no key at all


def _refuse_discovery(*args, **kwargs):
    raise ModelDiscoveryError("catalogued offline in this test")


# --------------------------------------------------------------------------- #
# 3 / 5 / 7 / 9. Per-provider automatic discovery
# --------------------------------------------------------------------------- #


def _assert_discovery(key, expected_list_url):
    """Discover, validate, cache, then use the discovered id for a real call."""
    def run(monkeypatch, keys_only):
        api = install_api(monkeypatch, FakeProviderAPI(CATALOGUES[key]))
        provider = slot(key)
        assert provider.model == "", "precondition: the model starts empty"
        assert provider.configured is True, "precondition: key + base url is enough"

        model = provider.resolve_model()
        assert model == DISCOVERED[key]
        # The provider's own catalogue endpoint, and nothing else.
        assert api.list_urls == [expected_list_url]
        # The key travels in a header - never in the URL, never in the body.
        assert api.carries(SECRETS_OF[key])
        assert SECRETS_OF[key] not in expected_list_url

        assert provider.model == DISCOVERED[key]
        assert provider.model_available is True
        assert provider.model_source == "discovered"
        # Discovery confirmed the model with one tiny request before caching it.
        assert api.body_models == [DISCOVERED[key]]

        # A real generation reuses the cached model.
        assert provider.generate("hello") == "OK"
        assert api.body_models == [DISCOVERED[key], DISCOVERED[key]]
        return api, provider

    return run


def test_groq_discovers_a_model_when_none_is_configured(monkeypatch, keys_only):
    _assert_discovery("groq", "https://api.groq.com/openai/v1/models")(monkeypatch, keys_only)


def test_gemini_discovers_a_model_from_the_native_list_endpoint(monkeypatch, keys_only):
    """GEMINI_BASE_URL is the /openai surface; the catalogue is one level up."""
    _assert_discovery("gemini", "https://generativelanguage.googleapis.com/v1beta/models")(
        monkeypatch, keys_only
    )


def test_openrouter_discovers_a_model_when_none_is_configured(monkeypatch, keys_only):
    _assert_discovery("external_1", "https://openrouter.ai/api/v1/models")(monkeypatch, keys_only)


def test_huggingface_discovers_a_model_with_a_live_text_provider(monkeypatch, keys_only):
    _assert_discovery("external_2", "https://router.huggingface.co/v1/models")(monkeypatch, keys_only)


def test_gemini_key_is_sent_in_the_goog_header_not_the_url(monkeypatch, keys_only):
    api = install_api(monkeypatch, FakeProviderAPI(GEMINI_CATALOGUE))
    slot("gemini").resolve_model()
    request = api.list_requests[0]
    assert request.headers["x-goog-api-key"] == SECRETS["GEMINI_API_KEY"]
    assert "authorization" not in request.headers
    assert SECRETS["GEMINI_API_KEY"] not in str(request.url)


def test_discovery_filters_out_non_chat_models(monkeypatch, keys_only):
    """Speech, guardrail, embedding, reranker and image models are never picked."""
    for key in CATALOGUES:
        install_api(monkeypatch, FakeProviderAPI(CATALOGUES[key]))
        model = slot(key).resolve_model()
        lowered = model.lower()
        for banned in ("whisper", "guard", "embed", "rerank", "vision", "tts"):
            assert banned not in lowered, f"{key} picked {model}"


@pytest.mark.parametrize("key", sorted(CATALOGUES))
def test_discovery_result_is_cached_for_the_process(key, monkeypatch, keys_only):
    """One catalogue call per process, no matter how many generations run."""
    api = install_api(monkeypatch, FakeProviderAPI(CATALOGUES[key]))
    provider = slot(key)
    for _ in range(4):
        assert provider.generate("hello") == "OK"
    # 1 catalogue call + 1 validation probe + 4 real calls, then nothing more.
    assert len(api.list_requests) == 1
    assert len(api.chat_requests) == 5

    # A freshly built provider (as the status endpoints do) reuses the cache.
    assert slot(key).resolve_model() == DISCOVERED[key]
    assert len(api.list_requests) == 1
    assert len(api.chat_requests) == 5


@pytest.mark.parametrize("key", sorted(CATALOGUES))
def test_a_broken_discovered_model_reports_cleanly_and_is_forgotten(key, monkeypatch, keys_only):
    """A model retired after discovery must not poison the process."""
    api = install_api(monkeypatch, FakeProviderAPI(CATALOGUES[key]))
    provider = slot(key)
    assert provider.resolve_model() == DISCOVERED[key]

    # Upstream retires the model right after it was discovered.
    api.chat_status = 500
    with pytest.raises(AIProviderError) as excinfo:
        provider.generate("hello")
    assert provider.name in str(excinfo.value)
    for secret in SECRETS.values():
        assert secret not in str(excinfo.value)
    # The retired model is dropped, so the next request re-queries the catalogue.
    assert not provider.model_available
    assert len(api.list_requests) == 1

    # Upstream serves it again -> the provider heals itself.
    api.chat_status = 200
    assert provider.generate("hello") == "OK"
    assert len(api.list_requests) == 2
    assert provider.model == DISCOVERED[key]
    assert provider.model_source == "discovered"


# --------------------------------------------------------------------------- #
# 3 / 5 / 7 / 9 (cont). Discovery validates the candidate it picks
# --------------------------------------------------------------------------- #

# Mirrors the real Gemini behaviour: the catalogue advertises models the key is
# no longer allowed to generate with, and only the later ones actually work.
RETIRED_THEN_LIVE_CATALOGUE = {
    "models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    ]
}


class RetiringAPI(FakeProviderAPI):
    """Rejects a set of model ids at generation time, like Google does."""

    def __init__(self, catalogue, rejected, **kwargs):
        super().__init__(catalogue, **kwargs)
        self.rejected = set(rejected)
        self.probed = []

    def __call__(self, request):
        if request.method != "GET":
            payload = json.loads(request.content)
            model = payload["model"]
            if "max_tokens" in payload:  # a discovery probe, not a real call
                self.probed.append(model)
                if model in self.rejected:
                    return httpx.Response(404, json={"error": {"message": "no longer available"}})
        return super().__call__(request)


# Model ids as the chat endpoint sees them (the catalogue's `models/` prefix stripped).
RETIRED_THEN_LIVE_MODEL_IDS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-3.1-flash-lite",
]


def test_discovery_skips_catalogue_entries_the_provider_refuses(monkeypatch, keys_only):
    """A catalogue entry is a candidate, not a promise: the model must be probed."""
    api = install_api(
        monkeypatch,
        RetiringAPI(
            RETIRED_THEN_LIVE_CATALOGUE,
            rejected=set(RETIRED_THEN_LIVE_MODEL_IDS[:3]),
        ),
    )
    provider = slot("gemini")
    assert provider.resolve_model() == "gemini-3.1-flash-lite"
    # Ranked order, the three retired ones rejected, the fourth accepted.
    assert api.probed == RETIRED_THEN_LIVE_MODEL_IDS

    # Only the winner is cached, so no further probing happens.
    assert provider.generate("hello") == "OK"
    assert len(api.probed) == 4


def test_discovery_stops_probing_at_the_configured_limit(monkeypatch, keys_only):
    api = install_api(
        monkeypatch,
        RetiringAPI(RETIRED_THEN_LIVE_CATALOGUE, rejected=set(RETIRED_THEN_LIVE_MODEL_IDS)),
    )
    provider = slot("gemini")
    with pytest.raises(AIProviderError) as excinfo:
        provider.resolve_model()
    assert "model discovery failed" in str(excinfo.value)
    assert len(api.probed) == discovery.MAX_VALIDATION_PROBES
    assert not provider.model_available


def test_a_probe_that_returns_no_text_still_counts_as_usable(monkeypatch, keys_only):
    """A reasoning model can burn a tiny token budget without emitting text."""

    class SilentButOK(FakeProviderAPI):
        def __call__(self, request):
            if request.method != "GET":
                self.chat_requests.append(request)
                return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
            return super().__call__(request)

    api = install_api(monkeypatch, SilentButOK(CATALOGUES["groq"]))
    provider = slot("groq")
    assert provider.resolve_model() == DISCOVERED["groq"]
    assert len(api.chat_requests) == 1


def test_discovery_prefers_a_moving_latest_alias(monkeypatch, keys_only):
    """A '-latest' alias tracks whatever the provider still serves."""
    catalogue = {
        "object": "list",
        "data": [
            {"id": "vendor/old-chat-model", "architecture": {"output_modalities": ["text"]}},
            {"id": "vendor/new-chat-latest", "architecture": {"output_modalities": ["text"]}},
        ],
    }
    monkeypatch.setenv("EXTERNAL_PROVIDER_1_BASE_URL", "https://openrouter.ai/api/v1")
    get_settings.cache_clear()
    api = install_api(monkeypatch, FakeProviderAPI(catalogue))
    assert slot("external_1").resolve_model() == "vendor/new-chat-latest"
    assert len(api.list_requests) == 1


# --------------------------------------------------------------------------- #
# 17. An explicit model always beats discovery
# --------------------------------------------------------------------------- #


@pytest.fixture()
def pinned_model(monkeypatch):
    """Keys-only env, then one slot pinned to an explicit model id."""
    for env_key, value in KEYS_ONLY_ENV.items():
        monkeypatch.setenv(env_key, value)
    get_settings.cache_clear()
    reset_provider_manager()
    catalog.reset()

    def pin(key, model):
        monkeypatch.setenv(MODEL_ENV[key], model)
        get_settings.cache_clear()
        reset_provider_manager()
        catalog.reset()

    yield pin


@pytest.mark.parametrize("key", sorted(CATALOGUES))
def test_explicit_model_takes_priority_over_discovery(key, pinned_model, monkeypatch):
    explicit = "vendor/pinned-model"
    pinned_model(key, explicit)
    api = install_api(monkeypatch, FakeProviderAPI(CATALOGUES[key]))

    provider = slot(key)
    assert provider.generate("hello") == "OK"

    # The catalogue was never queried and the pinned id was used verbatim.
    assert api.list_requests == []
    assert api.body_models == [explicit]
    assert provider.model_source == "configured"


def test_explicit_model_is_reported_in_status_without_any_discovery_call(
    client, admin_headers, chain, offline_health
):
    r = client.get("/api/admin/settings/ai-providers", headers=admin_headers)
    by_key = {p["key"]: p for p in r.json()["providers"]}
    for key, (_, _, model) in zip(("groq", "gemini", "external_1", "external_2"), EXPECTED_CHAIN[1:]):
        assert by_key[key]["model"] == model
        assert by_key[key]["model_source"] == "configured"
        assert by_key[key]["model_available"] is True
        assert by_key[key]["state"] == "online"


# --------------------------------------------------------------------------- #
# 10. Discovery failure never crashes and never blocks the chain
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(CATALOGUES))
def test_discovery_failure_marks_the_provider_unavailable(key, monkeypatch, keys_only):
    install_api(monkeypatch, FakeProviderAPI(CATALOGUES[key], list_status=401))
    provider = slot(key)
    with pytest.raises(AIProviderError) as excinfo:
        provider.resolve_model()
    message = str(excinfo.value)
    assert "model discovery failed" in message
    assert provider.name in message
    assert not provider.model_available
    for secret in SECRETS.values():
        assert secret not in message


def test_discovery_failure_advances_to_the_next_provider(monkeypatch, keys_only):
    """Groq cannot list models -> Gemini answers instead. No crash."""
    apis = {
        "groq": FakeProviderAPI(CATALOGUES["groq"], list_status=500),
        "gemini": FakeProviderAPI(CATALOGUES["gemini"], chat_text="from gemini"),
        "external_1": FakeProviderAPI(CATALOGUES["external_1"], chat_text="from openrouter"),
        "external_2": FakeProviderAPI(CATALOGUES["external_2"], chat_text="from hf"),
    }
    _install_transport(monkeypatch, **apis)

    manager = ProviderManager()
    _stub_ollama(manager)

    text, name, model = manager.generate("hello")
    assert (text, name, model) == ("from gemini", "Gemini", DISCOVERED["gemini"])
    assert apis["groq"].list_requests, "Groq discovery was attempted"
    assert apis["groq"].chat_requests == [], "Groq was never used for the actual call"
    assert get_breaker().is_open("groq")


def test_all_providers_failing_discovery_raises_a_clear_error(monkeypatch, keys_only):
    apis = {key: FakeProviderAPI(CATALOGUES[key], list_status=500) for key in CATALOGUES}
    _install_transport(monkeypatch, **apis)
    manager = ProviderManager()
    _stub_ollama(manager)

    with pytest.raises(AIProviderError) as excinfo:
        manager.generate("hello")
    message = str(excinfo.value)
    assert message.startswith("All AI providers failed.")
    for name in ("Ollama", "Groq", "Gemini", "OpenRouter", "Hugging Face"):
        assert name in message
    for secret in SECRETS.values():
        assert secret not in message
    # Every provider really was attempted, in order.
    assert all(apis[key].list_requests for key in ("groq", "gemini", "external_1", "external_2"))


def test_failed_discovery_is_remembered_instead_of_hammered(monkeypatch, keys_only):
    api = install_api(monkeypatch, FakeProviderAPI(CATALOGUES["groq"], list_status=500))
    provider = slot("groq")
    for _ in range(3):
        with pytest.raises(AIProviderError):
            provider.resolve_model()
    assert len(api.list_requests) == 1, "a dead catalogue must not be re-queried every request"
    catalog.reset()
    with pytest.raises(AIProviderError):
        provider.resolve_model()
    assert len(api.list_requests) == 2, "the negative cache must expire / be resettable"


def test_health_check_reports_discovery_problems_without_leaking(monkeypatch, keys_only):
    install_api(monkeypatch, FakeProviderAPI(CATALOGUES["groq"], list_status=500))
    ok, detail = slot("groq").health_check()
    assert ok is False
    assert "Error:" in detail
    for secret in SECRETS.values():
        assert secret not in detail


# --------------------------------------------------------------------------- #
# 12. Timeouts
# --------------------------------------------------------------------------- #


def _timeout_seconds(value):
    """Seconds represented by an httpx.Timeout (or a plain number)."""
    if isinstance(value, (int, float)):
        return float(value)
    read = getattr(value, "read", None)
    return float(read if read is not None else value)


def test_discovery_timeout_is_capped_even_if_a_larger_budget_is_offered(monkeypatch, keys_only):
    timeouts = install_api_timed(monkeypatch, FakeProviderAPI(CATALOGUES["groq"]))
    provider = slot("groq")
    # A catalogue call is metadata: it must never take the whole AI budget.
    provider.resolve_model(timeout=900)
    assert timeouts, "expected a model-list request"
    assert all(_timeout_seconds(t) <= discovery.MAX_DISCOVERY_TIMEOUT for t in timeouts)


def test_discovery_timeout_respects_the_per_attempt_budget(monkeypatch, keys_only):
    timeouts = install_api_timed(monkeypatch, FakeProviderAPI(CATALOGUES["groq"]))
    # AI_PROVIDER_ATTEMPT_TIMEOUT is 5 in the test env.
    slot("groq").resolve_model(timeout=5)
    assert all(0 < _timeout_seconds(t) <= 5 for t in timeouts)


def test_a_slow_catalogue_cannot_blow_the_attempt_budget(monkeypatch, keys_only):
    def slow(request):
        raise httpx.TimeoutException("read timeout", request=request)

    install_api(monkeypatch, slow)
    provider = slot("groq")
    with pytest.raises(AIProviderError) as excinfo:
        provider.resolve_model(timeout=5)
    assert "model discovery failed" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 13. Circuit breaker around discovery
# --------------------------------------------------------------------------- #


def test_discovery_failure_opens_the_breaker_and_skips_the_provider(monkeypatch, keys_only):
    apis = {
        "groq": FakeProviderAPI(CATALOGUES["groq"], list_status=500),
        "gemini": FakeProviderAPI(CATALOGUES["gemini"], chat_text="from gemini"),
    }
    _install_transport(monkeypatch, **apis)
    monkeypatch.setattr(P.OllamaProvider, "health_check", lambda self: (False, "down"))

    manager = ProviderManager()
    _stub_ollama(manager)

    for _ in range(3):
        assert manager.generate("hi")[1] == "Gemini"

    # Groq was probed once; afterwards the breaker fast-fails it.
    assert len(apis["groq"].list_requests) == 1
    assert get_breaker().is_open("groq")

    # Status reports the open circuit without touching the catalogue again.
    row = {r["key"]: r for r in manager.status()}["groq"]
    assert row["breaker_open"] is True
    assert row["state"] in ("unavailable", "discovering")
    assert "Circuit open" in row["detail"]


# --------------------------------------------------------------------------- #
# 14 / 15. Keys stay masked everywhere, including logs
# --------------------------------------------------------------------------- #


def test_discovery_never_logs_an_api_key(caplog, monkeypatch, keys_only):
    caplog.set_level("DEBUG")
    install_api(monkeypatch, FakeProviderAPI(GROQ_CATALOGUE))
    slot("groq").resolve_model()
    logging_text = "\n".join(record.getMessage() for record in caplog.records)
    assert logging_text, "discovery is expected to log something"
    for secret in SECRETS.values():
        assert secret not in logging_text
    assert "Authorization" not in logging_text


def test_discovery_failure_log_is_scrubbed(caplog, monkeypatch, keys_only):
    caplog.set_level("DEBUG")
    install_api(monkeypatch, FakeProviderAPI(GROQ_CATALOGUE, list_status=403))
    with pytest.raises(AIProviderError):
        slot("groq").resolve_model()
    logging_text = "\n".join(record.getMessage() for record in caplog.records)
    for secret in SECRETS.values():
        assert secret not in logging_text


def test_status_payload_with_discovered_models_never_exposes_keys(
    client, admin_headers, monkeypatch, keys_only
):
    apis = {key: FakeProviderAPI(CATALOGUES[key]) for key in CATALOGUES}
    _install_transport(monkeypatch, **apis)
    monkeypatch.setattr(P.OllamaProvider, "health_check", lambda self: (True, "Connected"))
    monkeypatch.setattr(
        "app.services.embeddings.OllamaEmbeddingProvider.embed_one", lambda self, text: [0.0] * 768
    )

    r = client.get("/api/admin/settings/ai-providers", headers=admin_headers)
    assert r.status_code == 200
    raw = r.text
    for secret in SECRETS.values():
        assert secret not in raw
    for needle in ("sk-or-v1", "gsk_", "AIzaSy", "hf_TEST", "api_key", "Authorization", "Bearer"):
        assert needle not in raw
    by_key = {p["key"]: p for p in r.json()["providers"]}
    assert by_key["groq"]["key_masked"] == "***"
    assert by_key["groq"]["model"] == DISCOVERED["groq"]
    assert by_key["groq"]["model_source"] == "discovered"


def test_test_endpoint_discovers_a_model_and_masks_the_key(
    client, admin_headers, monkeypatch, keys_only
):
    apis = {key: FakeProviderAPI(CATALOGUES[key]) for key in CATALOGUES}
    _install_transport(monkeypatch, **apis)
    r = client.post("/api/admin/settings/ai-providers/external_1/test", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == DISCOVERED["external_1"]
    assert body["model_source"] == "discovered"
    assert body["model_available"] is True
    assert body["key_masked"] == "***"
    assert body["key_configured"] is True
    assert body["state"] == "online"
    for secret in SECRETS.values():
        assert secret not in r.text
    assert len(apis["external_1"].list_requests) == 1


def test_a_key_is_only_ever_sent_to_its_own_provider(monkeypatch, keys_only):
    """Groq's key must not appear on Gemini/OpenRouter/HF requests, or vice versa."""
    apis = {key: FakeProviderAPI(CATALOGUES[key]) for key in CATALOGUES}
    _install_transport(monkeypatch, **apis)
    for key in CATALOGUES:
        slot(key).resolve_model()
    for key, api in apis.items():
        for other, secret in SECRETS_OF.items():
            if other == key:
                assert api.carries(secret), f"{key} must authenticate with its own key"
            else:
                assert not api.carries(secret), f"{key} must never see {other}'s key"


# --------------------------------------------------------------------------- #
# 10 (cont). Nothing model-related is hardcoded
# --------------------------------------------------------------------------- #


def test_config_defaults_do_not_pin_any_external_model():
    """Blanking the model settings is the shipped default: discovery is the path."""
    defaults = {name: f.default for name, f in Settings.model_fields.items()}
    for env_key in MODEL_ENV.values():
        assert defaults.get(env_key) == "", f"{env_key} must ship blank so discovery runs"
    # The four cloud slots may still be keyed in from a local .env; that is fine.
    assert defaults["OLLAMA_MODEL"] == "qwen2.5:3b"
    assert defaults["EMBEDDING_MODEL"] == "nomic-embed-text"


def test_ollama_and_embeddings_are_untouched_by_discovery():
    """Discovery covers the four cloud slots only."""
    settings = get_settings()
    assert settings.OLLAMA_BASE_URL == "http://localhost:11434"
    assert settings.OLLAMA_MODEL == "qwen2.5:3b"
    assert settings.EMBEDDING_PROVIDER == "ollama"
    assert settings.EMBEDDING_MODEL == "nomic-embed-text"
    assert build_provider("ollama").model == "qwen2.5:3b"
    assert build_provider("ollama").model_source == "configured"
