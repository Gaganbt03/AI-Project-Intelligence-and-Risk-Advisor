import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.services.bootstrap import bootstrap_first_user, ensure_roles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

settings = get_settings()
settings.ensure_dirs()


def _sweep_workspaces_once() -> dict:
    """Purge closing/expired workspaces in its own DB session (threadpool)."""
    from app.services import sessions as session_service

    with SessionLocal() as db:
        return session_service.sweep_workspaces(db)


async def _workspace_sweeper() -> None:
    """Background loop reclaiming abandoned workspaces.

    Covers missed browser-close signals and crashes, which no client can report.
    """
    interval = max(5, settings.WORKSPACE_SWEEP_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(interval)
        try:
            result = await run_in_threadpool(_sweep_workspaces_once)
            if result.get("closed") or result.get("expired"):
                logger.info("Workspace sweeper purged: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed sweep must not kill the loop
            logger.exception("Workspace sweeper iteration failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as db:
        ensure_roles(db)
        first_user = bootstrap_first_user(db)
        if first_user:
            logger.info("First account created from environment: %s", first_user.email)
        else:
            logger.info("No environment-based account created (existing users or unset env).")
    sweeper = asyncio.create_task(_workspace_sweeper())
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    application = FastAPI(
        title="AI Project Intelligence & Risk Advisor API",
        description=(
            "RAG-powered multi-agent project document intelligence. Covers document "
            "ingestion, multi-agent analysis, documentation generation, deterministic "
            "health scoring, a conversational assistant and upload validation."
        ),
        version="0.3.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.routers import (
        assistant,
        audit,
        auth,
        blockers,
        dashboard,
        documents,
        insights,
        milestone3,
        projects,
        risks,
        settings as settings_router,
        sessions,
        tasks,
        users,
    )

    api_prefix = "/api"
    application.include_router(auth.router, prefix=api_prefix)
    application.include_router(sessions.router, prefix=api_prefix)
    application.include_router(users.router, prefix=api_prefix)
    application.include_router(projects.router, prefix=api_prefix)
    application.include_router(documents.router, prefix=api_prefix)
    application.include_router(risks.router, prefix=api_prefix)
    application.include_router(blockers.router, prefix=api_prefix)
    application.include_router(tasks.router, prefix=api_prefix)
    application.include_router(insights.router, prefix=api_prefix)
    application.include_router(assistant.router, prefix=api_prefix)
    application.include_router(audit.router, prefix=api_prefix)
    application.include_router(dashboard.router, prefix=api_prefix)
    application.include_router(settings_router.router, prefix=api_prefix)
    application.include_router(settings_router.admin_router, prefix=api_prefix)
    # Milestone 3: documentation generation, health scoring, conversational
    # assistant and upload validation. Mounted last so its project-scoped
    # paths cannot shadow the Milestone 1/2 routes above.
    application.include_router(milestone3.router, prefix=api_prefix)

    @application.get("/")
    def root():
        return {
            "name": settings.APP_NAME,
            "version": "0.3.0",
            "milestones": ["1 - document intelligence", "2 - multi-agent analysis",
                           "3 - documentation, health, assistant, validation"],
            "docs": "/docs",
            "frontend": "http://localhost:5173",
        }

    @application.get("/api/health")
    def health():
        return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}

    return application


# `app` is used by uvicorn: `uvicorn app.main:app`
app = create_app()