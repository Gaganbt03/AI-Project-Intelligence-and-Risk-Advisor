from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import require_admin
from app.models import SystemSetting, User
from app.services.ai.providers import build_provider, get_breaker, get_provider_manager, mask_key
from app.services.embeddings import get_embedding_provider

router = APIRouter(prefix="/settings", tags=["settings"])
admin_router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

_GENERAL_KEYS = ("CHUNK_SIZE", "CHUNK_OVERLAP", "AI_MAX_CHUNKS", "AI_TEMPERATURE")


class GeneralSettings(BaseModel):
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    ai_max_chunks: int | None = None
    ai_temperature: float | None = None


@router.get("/general")
def get_general_settings():
    settings = get_settings()
    return {
        "chunk_size": _db_int_or_default("CHUNK_SIZE", settings.CHUNK_SIZE),
        "chunk_overlap": _db_int_or_default("CHUNK_OVERLAP", settings.CHUNK_OVERLAP),
        "ai_max_chunks": _db_int_or_default("AI_MAX_CHUNKS", settings.AI_MAX_CHUNKS),
        "ai_temperature": settings.AI_TEMPERATURE,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    }


@admin_router.get("/ai-providers")
def provider_status(user: User = Depends(require_admin)):
    """Live status of the ordered provider chain + the embedding provider.

    API keys are never returned: only whether one is set and a fixed redaction.
    """
    manager = get_provider_manager()
    providers = manager.status()
    embedding_provider = get_embedding_provider()
    embed_name = getattr(embedding_provider, "name", "unknown")
    embed_model = getattr(embedding_provider, "model", "")
    embed_status = {"provider": embed_name, "model": embed_model}
    try:
        dim = len(embedding_provider.embed_one("probe"))
        embed_status["dimension"] = dim
        embed_status["healthy"] = True
    except Exception as exc:  # noqa: BLE001
        embed_status["healthy"] = False
        embed_status["detail"] = str(exc)
    return {"providers": providers, "embedding": embed_status}


@admin_router.post("/ai-providers/{provider_key}/test")
def test_provider(provider_key: str, user: User = Depends(require_admin)):
    """Test Connection for one chain slot: ollama | groq | gemini | external_1..3.

    Clears that provider's circuit breaker first so the button always performs
    a real probe instead of instantly short-circuiting. When the slot has no
    model configured the probe discovers one first. The API key is never
    returned - only `key_configured` and a fixed redaction.
    """
    manager = get_provider_manager()
    provider = build_provider(provider_key)
    if provider is None:
        known = [row["key"] for row in manager.describe_chain()]
        raise HTTPException(status_code=400, detail=f"Unknown provider key. Expected one of: {', '.join(known)}")
    get_breaker().reset(provider.key)
    ok, detail = provider.health_check()
    model_available = provider.model_available
    if model_available:
        state = "online" if ok else "offline"
    else:
        state = "not_configured" if not provider.configured else "discovering"
    return {
        "key": provider.key,
        "name": provider.name,
        "role": provider.role,
        "model": provider.model or "—",
        "model_available": model_available,
        "model_source": provider.model_source,
        "configured": provider.configured,
        "healthy": ok,
        "state": state,
        "key_configured": bool((getattr(provider, "api_key", "") or "").strip()),
        "key_masked": mask_key(getattr(provider, "api_key", "")),
        "detail": detail,
    }


@admin_router.get("/general")
def admin_get_general(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    settings = get_settings()
    return {
        "chunk_size": _db_int_or_default("CHUNK_SIZE", settings.CHUNK_SIZE, db),
        "chunk_overlap": _db_int_or_default("CHUNK_OVERLAP", settings.CHUNK_OVERLAP, db),
        "ai_max_chunks": _db_int_or_default("AI_MAX_CHUNKS", settings.AI_MAX_CHUNKS, db),
        "ai_temperature": settings.AI_TEMPERATURE,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "ollama_base_url": settings.OLLAMA_BASE_URL,
        "ollama_model": settings.OLLAMA_MODEL,
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
    }


@admin_router.put("/general")
def admin_update_general(payload: GeneralSettings, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    values = {
        "CHUNK_SIZE": payload.chunk_size,
        "CHUNK_OVERLAP": payload.chunk_overlap,
        "AI_MAX_CHUNKS": payload.ai_max_chunks,
    }
    for key, val in values.items():
        if val is None:
            continue
        if val <= 0:
            raise HTTPException(status_code=400, detail=f"{key} must be positive.")
        _set_db_setting(db, key, str(int(val)))
    if payload.ai_temperature is not None:
        if not (0.0 <= payload.ai_temperature <= 1.5):
            raise HTTPException(status_code=400, detail="ai_temperature must be between 0 and 1.5.")
        _set_db_setting(db, "AI_TEMPERATURE", str(payload.ai_temperature))
    db.commit()
    return admin_get_general(user, db)


def _set_db_setting(db: Session, key: str, value: str) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


def _db_int_or_default(key: str, default: int, db: Session | None = None) -> int:
    if db is None:
        return default
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default