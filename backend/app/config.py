from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    # App
    APP_ENV: str = "development"
    APP_NAME: str = "AI Project Intelligence & Risk Advisor"
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # Database
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"

    # Storage
    UPLOAD_DIR: str = str(BASE_DIR / "data" / "uploads")
    MAX_UPLOAD_MB: int = 25

    # Embeddings
    EMBEDDING_PROVIDER: str = "ollama"  # ollama | external
    EMBEDDING_MODEL: str = "nomic-embed-text"
    # Alternative local approach (if sentence-transformers installed): EMBEDDING_PROVIDER=sentence_transformers

    # Vector DB
    VECTOR_DB_PATH: str = str(BASE_DIR / "data" / "vector_db")
    VECTOR_COLLECTION_PREFIX: str = "project"

    # Chunking (runtime-configurable)
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 150

    # Ollama (primary)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"
    OLLAMA_NUM_CTX: int = 4096
    OLLAMA_NUM_PREDICT: int = 2048
    OLLAMA_KEEP_ALIVE: str = "30m"
    AI_JSON_MODE: bool = True

    # Provider selection
    AI_PROVIDER: str = "ollama"

    # Ordered fallback chain.
    # Ollama (primary) -> Groq -> Gemini -> OpenRouter -> Hugging Face.
    # A provider is skipped when it is not configured, when its circuit breaker
    # is open, or when its call errors out / times out.
    AI_FALLBACK_ENABLED: bool = True
    # Per-attempt budget. AI_REQUEST_TIMEOUT stays the TOTAL budget for one
    # logical request (all attempts combined); this caps any single attempt so
    # one dead provider cannot burn the whole budget before the next one runs.
    AI_PROVIDER_ATTEMPT_TIMEOUT: int = 20
    # How long a failed provider is fast-failed before it is retried (seconds).
    AI_PROVIDER_BREAKER_COOLDOWN: int = 60

    # Groq (fallback 1) - OpenAI-compatible endpoint documented at
    # https://console.groq.com/docs/openai-compatibility
    # Only the API key is required: an empty GROQ_MODEL makes the app discover a
    # chat model from GET <GROQ_BASE_URL>/models once per process.
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = ""

    # Gemini (fallback 2) - OpenAI-compatible endpoint documented at
    # https://ai.google.dev/gemini-api/docs/openai
    # An empty GEMINI_MODEL makes the app discover a model that supports
    # generateContent from GET <version-root>/models.
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = ""

    # External providers (optional OpenAI-compatible)
    # EXTERNAL_PROVIDER_1 is the OpenRouter slot (fallback 3) and
    # EXTERNAL_PROVIDER_2 is the Hugging Face slot (fallback 4) when their
    # default names are kept. EXTERNAL_PROVIDER_3 stays a free-form generic
    # OpenAI-compatible endpoint and is appended after the five named slots.
    # Every external slot also accepts an empty *_MODEL: one is then discovered
    # from that endpoint's own /models listing.
    EXTERNAL_PROVIDER_1_NAME: str = "OpenRouter"
    EXTERNAL_PROVIDER_1_BASE_URL: str = "https://openrouter.ai/api/v1"
    EXTERNAL_PROVIDER_1_API_KEY: str = ""
    EXTERNAL_PROVIDER_1_MODEL: str = ""

    EXTERNAL_PROVIDER_2_NAME: str = "Hugging Face"
    EXTERNAL_PROVIDER_2_BASE_URL: str = "https://router.huggingface.co/v1"
    EXTERNAL_PROVIDER_2_API_KEY: str = ""
    EXTERNAL_PROVIDER_2_MODEL: str = ""

    EXTERNAL_PROVIDER_3_NAME: str = "OpenAI-Compatible"
    EXTERNAL_PROVIDER_3_BASE_URL: str = ""
    EXTERNAL_PROVIDER_3_API_KEY: str = ""
    EXTERNAL_PROVIDER_3_MODEL: str = ""

    # AI generation
    AI_TEMPERATURE: float = 0.1
    AI_MAX_CHUNKS: int = 6  # chunks retrieved per RAG query
    AI_REQUEST_TIMEOUT: int = 180

    # Retrieval relevance. A retrieved chunk is only treated as evidence when its
    # cosine similarity (1 - cosine distance) reaches this floor; anything less
    # relevant is dropped so the model is never grounded on unrelated text.
    # 0.0 keeps anything not anti-correlated, 1.0 keeps only near-identical text,
    # and -1.0 disables the filter entirely (tests use -1.0; production must not).
    #
    # The default was measured against the configured model ("nomic-embed-text")
    # by embedding this project's own requirement/meeting text and comparing
    # on-topic retrieval queries with off-topic ones. With that model, unrelated
    # text still scores ~0.38-0.48 (its similarity baseline is high), while
    # on-topic queries reach ~0.45-0.80. 0.4 sits just above the unrelated
    # baseline: it removes the least-related chunks without starving retrieval.
    # Re-measure and tune whenever EMBEDDING_MODEL or CHUNK_SIZE changes.
    RAG_MIN_SIMILARITY: float = 0.4

    # Anonymous temporary workspace sessions
    # Each browser gets its own isolated workspace (a private project) delivered
    # by an opaque, server-stored session token in an HttpOnly cookie. The TTL is
    # an idle timeout: it is refreshed on activity and, together with the
    # background sweeper, is the server-side fallback for missed close signals,
    # crashes and abandoned sessions.
    WORKSPACE_SESSION_TTL_MINUTES: int = 60
    WORKSPACE_COOKIE_NAME: str = "apir_ws"
    WORKSPACE_COOKIE_SECURE: bool = False  # set True when served over HTTPS
    WORKSPACE_FINISH_GRACE_SECONDS: int = 10  # grace before a browser-close purge
    WORKSPACE_SWEEP_INTERVAL_SECONDS: int = 15  # background purge/expiry cadence

    # First administrator (environment-based bootstrap; never inserted by us)
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""
    ADMIN_NAME: str = "Administrator"

    # AI run concurrency (number of documents processed in parallel during analysis)
    AGENT_CONCURRENCY: int = 1

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        for d in (self.UPLOAD_DIR, self.VECTOR_DB_PATH, f"{self.DATABASE_URL}" and Path(self.DATABASE_URL.replace("sqlite:///", "")).parent):
            p = Path(d)
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()