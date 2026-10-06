"""Documentation Generation Agent (Milestone 3.1)."""

from app.services.milestone3.documentation.action_items import build_action_items
from app.services.milestone3.documentation.generator import (
    DocumentationError,
    generate_all,
    generate_document,
    get_document,
    list_documents,
    regenerate,
    summarize,
)
from app.services.milestone3.documentation.renderer import render
from app.services.milestone3.documentation.risk_register import build_risk_register
from app.services.milestone3.documentation.schemas import (
    DocumentationContext,
    EvidenceItem,
    UserStory,
    UserStoryResult,
)
from app.services.milestone3.documentation.user_stories import generate_user_stories

__all__ = [
    "DocumentationError",
    "generate_document",
    "generate_all",
    "regenerate",
    "list_documents",
    "get_document",
    "summarize",
    "render",
    "build_risk_register",
    "build_action_items",
    "generate_user_stories",
    "UserStory",
    "UserStoryResult",
    "EvidenceItem",
    "DocumentationContext",
]
