"""Anonymous workspace session routes.

The login-free entry point: a browser calls ``POST /api/session/start`` once,
receives an HttpOnly cookie, and from then on every request is silently scoped
to its own private workspace. ``/current`` lets the frontend discover whether it
already has a live workspace; ``/heartbeat`` keeps the idle TTL alive.
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_session_workspace
from app.models_sessions import WorkspaceSession
from app.services import sessions as session_service

router = APIRouter(prefix="/session", tags=["session"])


def _payload(session: WorkspaceSession) -> dict:
    return {
        "session_id": session.id,
        "project_id": session.project_id,
        "status": session.status,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
    }


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.WORKSPACE_COOKIE_NAME,
        value=token,
        max_age=settings.WORKSPACE_SESSION_TTL_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=settings.WORKSPACE_COOKIE_SECURE,
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.WORKSPACE_COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=settings.WORKSPACE_COOKIE_SECURE,
    )


@router.post("/start")
def start_session(request: Request, response: Response, db: Session = Depends(get_db)):
    """Return a live workspace, creating one only when the caller has none.

    Reusing an existing cookie makes reloads cheap; a brand-new cookie is what
    gives every other visitor an independent, empty workspace. A returning
    request also cancels any pending browser-close (the grace window).
    """
    settings = get_settings()
    token = request.cookies.get(settings.WORKSPACE_COOKIE_NAME)
    session = session_service.resolve_session(db, token) if token else None
    if session is None:
        label = (request.headers.get("user-agent") or "")[:255]
        session, new_token = session_service.create_session(db, owner_label=label)
        _set_cookie(response, new_token)
    else:
        session_service.touch_session(db, session)
    return _payload(session)


@router.get("/current")
def current_session(session: WorkspaceSession = Depends(get_session_workspace)):
    return _payload(session)


@router.post("/heartbeat")
def heartbeat(session: WorkspaceSession = Depends(get_session_workspace), db: Session = Depends(get_db)):
    session_service.touch_session(db, session)
    return _payload(session)


@router.post("/close")
def close_session(request: Request, db: Session = Depends(get_db)):
    """Best-effort browser-close signal (``navigator.sendBeacon``).

    Marks the workspace ``closing``; the background sweeper purges it once the
    grace window elapses. Any request inside that window revives it, so a quick
    refresh never loses work. Never raises: beacons cannot handle errors.
    """
    settings = get_settings()
    token = request.cookies.get(settings.WORKSPACE_COOKIE_NAME)
    session = session_service.resolve_session(db, token) if token else None
    if session is not None and session.status != session_service.CLOSING:
        session_service.schedule_close(db, session)
    return {"ok": True}


@router.post("/finish")
def finish_session(request: Request, response: Response, db: Session = Depends(get_db)):
    """Explicitly finish and permanently delete the current workspace.

    Purges every row, file and vector owned by the session, then clears the
    cookie. Idempotent: a missing/expired session still clears the cookie.
    """
    settings = get_settings()
    token = request.cookies.get(settings.WORKSPACE_COOKIE_NAME)
    session = session_service.resolve_session(db, token) if token else None
    counts = session_service.purge_workspace_session(db, session) if session else {}
    _clear_cookie(response)
    return {"ok": True, "purged": counts}
