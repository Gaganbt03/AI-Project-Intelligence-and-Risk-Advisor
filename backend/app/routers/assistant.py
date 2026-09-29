from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user
from app.models import User
from app.services.assistant import ask

router = APIRouter(prefix="/projects/{project_id}/assistant", tags=["assistant"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


class AskResponse(BaseModel):
    answer: str
    sources: list[dict] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    grounded: bool = False
    error: str | None = None


@router.post("", response_model=AskResponse)
def ask_question(project_id: int, payload: AskRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Project-scoped RAG question. Retrieval is strictly limited to the project's documents."""
    get_accessible_project(project_id, user, db)
    return ask(project_id, payload.question)