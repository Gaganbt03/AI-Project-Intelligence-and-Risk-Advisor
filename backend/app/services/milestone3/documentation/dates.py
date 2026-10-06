"""Due-date precedence — an explicit date in a source document always wins.

The rule this module implements, verbatim from the specification:

    Priority 1  an explicit date in the uploaded source document
    Priority 2  the administrator-entered date
    Priority 3  "Not specified in project data."

A document date therefore overrides a *different* admin date, but the admin value
is never destroyed: both are retained on the decision so the comparison stays
visible and auditable, and the user interface only surfaces it when the two
actually disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.services.milestone3.common import NOT_SPECIFIED
from app.services.milestone3.documentation.extraction import DateHit, coerce_date

#: ``DateSource`` values, exposed to the UI so it can label the origin of a date.
SOURCE_DOCUMENT = "Document"
SOURCE_ADMIN = "Admin"
SOURCE_NONE = "Not specified"


@dataclass
class DateDecision:
    """The resolved due date plus full traceability back to both inputs."""

    effective: str = NOT_SPECIFIED
    effective_date: Optional[date] = None
    admin_date: str = NOT_SPECIFIED
    document_date: str = NOT_SPECIFIED
    source: str = SOURCE_NONE
    evidence: str = ""
    document: str = ""
    conflict: bool = False
    #: ``True`` when the documents mention a deadline but only in relative terms.
    vague_in_source: bool = False

    @property
    def is_specified(self) -> bool:
        return self.effective_date is not None

    def to_dict(self) -> dict:
        """Serializable shape stored on the artifact and consumed by the UI/DOCX."""
        return {
            "effective_date": self.effective,
            "admin_date": self.admin_date,
            "document_date": self.document_date,
            "source": self.source,
            "evidence": self.evidence or NOT_SPECIFIED,
            "document": self.document,
            "conflict": self.conflict,
            "vague_in_source": self.vague_in_source,
        }

    def comparison(self) -> str:
        """Human-readable traceability block, e.g. for the date-conflict tooltip.

        Returns an empty string when there is nothing worth showing, so the
        normal document view is never cluttered.
        """
        if not self.conflict:
            return ""
        return (
            f"Admin date: {self.admin_date} · "
            f"Document date: {self.document_date} · "
            f"Effective date: {self.effective} · "
            f"Source: {self.source}"
        )


def resolve_due_date(
    admin_value,
    document_hit: DateHit | None = None,
    *,
    document_vague: bool = False,
) -> DateDecision:
    """Apply the document-first precedence rule.

    ``admin_value``      whatever the administrator entered (date, datetime, ISO
                         string) — ``None``/empty when they entered nothing.
    ``document_hit``     a :class:`DateHit` produced by
                         :func:`~app.services.milestone3.documentation.extraction.find_due_date`,
                         i.e. an *explicit* date already located in a chunk.
    ``document_vague``   the source mentions a deadline only relatively
                         ("delivery next week"). Recorded for traceability; it
                         never produces a date.
    """
    admin_date = coerce_date(admin_value)
    admin_text = admin_date.isoformat() if admin_date else NOT_SPECIFIED

    doc_date = document_hit.value if document_hit else None
    doc_text = doc_date.isoformat() if doc_date else NOT_SPECIFIED

    if doc_date is not None:
        source = SOURCE_DOCUMENT
        effective = doc_date
        evidence = (document_hit.evidence if document_hit else "") or NOT_SPECIFIED
        document_name = (document_hit.document if document_hit else "") or ""
    elif admin_date is not None:
        source = SOURCE_ADMIN
        effective = admin_date
        evidence = NOT_SPECIFIED
        document_name = ""
    else:
        source = SOURCE_NONE
        effective = None
        evidence = NOT_SPECIFIED
        document_name = ""

    conflict = bool(
        doc_date is not None
        and admin_date is not None
        and doc_date != admin_date
    )

    return DateDecision(
        effective=effective.isoformat() if effective else NOT_SPECIFIED,
        effective_date=effective,
        admin_date=admin_text,
        document_date=doc_text,
        source=source,
        evidence=evidence,
        document=document_name,
        conflict=conflict,
        vague_in_source=document_vague and doc_date is None,
    )


__all__ = [
    "SOURCE_ADMIN",
    "SOURCE_DOCUMENT",
    "SOURCE_NONE",
    "DateDecision",
    "resolve_due_date",
]
