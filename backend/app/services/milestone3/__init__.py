"""Milestone 3 — Documentation Generation, Health Scoring, Conversational
Assistant and Upload Validation.

Layout::

    milestone3/
      common.py              shared placeholders + deterministic ladders
      documentation/         3.1 documentation generation agent
      health/                3.2 deterministic health scoring
      assistant/             3.3 conversational project assistant
      validation/            3.4 upload validation

Every module here is additive: it reuses the Milestone 1/2 RAG pipeline, AI
provider manager, models and tables, and never modifies uploaded originals.
"""

from app.services.milestone3.common import (
    DOC_ACTION_ITEMS,
    DOC_RISK_REGISTER,
    DOC_TITLES,
    DOC_TYPES,
    DOC_USER_STORIES,
    INSUFFICIENT_EVIDENCE,
    NOT_SPECIFIED,
)

__all__ = [
    "INSUFFICIENT_EVIDENCE",
    "NOT_SPECIFIED",
    "DOC_TYPES",
    "DOC_TITLES",
    "DOC_USER_STORIES",
    "DOC_RISK_REGISTER",
    "DOC_ACTION_ITEMS",
]
