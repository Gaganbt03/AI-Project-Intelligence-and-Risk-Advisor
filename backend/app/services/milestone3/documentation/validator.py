"""Validation and Correction Layer (Milestone 3 — Documentation Quality Upgrade).

This sits between generation and persistence:

    documents -> RAG -> Milestone 2 data -> generation agent -> generated artifact
                                                                   |
                                                                   v
                                                     VALIDATION -> CORRECTION
                                                                   |
                                                                   v
                                                    final verified artifact

Responsibilities
----------------
1. Decide, per field, whether the value is **SUPPORTED**, **UNSUPPORTED**,
   **MISSING** or in **CONFLICT**.
2. **Correct** the artifact: an unsupported value is removed, a missing one is
   replaced with the explicit placeholder, and a documented date conflict is
   resolved in favour of the source document.
3. Produce the concise :class:`ValidationSummary` shown in the UI and printed in
   the downloaded document.

What "supported" means here
---------------------------
A value is SUPPORTED when it can be traced to at least one of:

* the uploaded document text (``document_chunks`` rows — verbatim substring),
* a RAG-retrieved chunk used to build the artifact,
* an existing Milestone 1/2 structured record (``risks`` / ``blockers`` / ``tasks``
  / ``project_insights``).

It is UNSUPPORTED when it appears nowhere in those three places, and MISSING when
the underlying data genuinely has no value for it. The distinction matters:
MISSING becomes the placeholder, UNSUPPORTED is *deleted* and counted in
``unsupported_removed``.

Deliberate non-goals
--------------------
* The validator never rewrites a Milestone 2 risk's probability, impact,
  severity, score or status. Those are the recorded assessment; a disagreement
  with the document text is reported as a CONFLICT with both values shown, and
  the recorded value is left untouched.
* The validator never exposes its reasoning. Findings carry a one-line factual
  note only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.milestone3.common import NOT_SPECIFIED
from app.services.milestone3.documentation.extraction import (
    normalize_space,
    significant_words,
)
from app.services.milestone3.documentation.schemas import (
    NO_CRITERIA,
    FieldFinding,
    FieldVerdict,
    ValidationSummary,
)

#: Fields the specification requires the validator to check, per artifact type.
STORY_FIELDS = (
    "actor",
    "requirement",
    "benefit",
    "priority",
    "acceptance_criteria",
    "source_document",
    "source_evidence",
)

RISK_FIELDS = (
    "risk_id",
    "description",
    "probability",
    "impact",
    "score",
    "severity",
    "status",
    "owner",
    "mitigation",
    "contingency",
    "source",
    "evidence",
)

ACTION_FIELDS = (
    "action",
    "owner",
    "priority",
    "due_date",
    "status",
    "related_risk",
    "related_blocker",
    "source",
    "evidence",
)

VERIFIED = "Verified"
VERIFIED_WITH_NOTES = "Verified with corrections"
UNVERIFIED = "Insufficient data"


@dataclass
class Grounding:
    """The searchable text a value must be traceable to."""

    document_text: str = ""
    rag_text: str = ""
    structured_text: str = ""

    def all_text(self) -> str:
        return "\n".join(t for t in (self.document_text, self.rag_text, self.structured_text) if t)

    def is_empty(self) -> bool:
        return not self.all_text().strip()


@dataclass
class Validator:
    """Accumulates findings and produces the summary."""

    summary: ValidationSummary = field(default_factory=ValidationSummary)
    notes: list[str] = field(default_factory=list)

    # -- recording -------------------------------------------------------- #

    def record(
        self,
        field_name: str,
        verdict: FieldVerdict,
        value: str = "",
        source: str = "",
        note: str = "",
    ) -> None:
        self.summary.findings.append(
            FieldFinding(
                field=field_name,
                verdict=verdict,
                value=_clip(value),
                source=source,
                note=note,
            )
        )
        if verdict is FieldVerdict.SUPPORTED:
            self.summary.supported += 1
        elif verdict is FieldVerdict.MISSING:
            self.summary.not_specified += 1
        elif verdict is FieldVerdict.CONFLICT:
            self.summary.conflicts += 1
        else:  # UNSUPPORTED — the value was removed from the artifact
            self.summary.unsupported_removed += 1
            self.summary.corrected += 1

    def corrected(self, count: int = 1) -> None:
        """Record a correction that produced no UNSUPPORTED finding of its own.

        An UNSUPPORTED finding already counts itself, so this is only for a value
        that was rewritten to a supported one — a date replaced by the document
        value, for instance.
        """
        self.summary.corrected += count

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

    def finish(self) -> ValidationSummary:
        self.summary.fields_checked = len(self.summary.findings)
        if self.summary.unsupported_removed or self.summary.conflicts:
            self.summary.status = VERIFIED_WITH_NOTES
        elif self.summary.fields_checked == 0:
            self.summary.status = UNVERIFIED
        else:
            self.summary.status = VERIFIED
        return self.summary


def _clip(value: str, limit: int = 160) -> str:
    v = (value or "").strip()
    return v if len(v) <= limit else v[:limit] + "…"


# --------------------------------------------------------------------------- #
# Support tests
# --------------------------------------------------------------------------- #


def appears_in(value: str, haystack: str, *, min_words: int = 1) -> bool:
    """True when ``value``'s distinctive words are present in ``haystack``.

    Deliberately conservative: a value that shares nothing with any source text
    is UNSUPPORTED, not "probably fine".
    """
    words = significant_words(value)
    if not words:
        return False
    lowered = haystack.lower()
    hits = sum(1 for w in words if w in lowered)
    if hits == len(words):
        return True
    if min_words <= 1:
        return False
    # Allow partial support for long sentences, but demand a real majority.
    return hits >= min(min_words, max(1, len(words) // 2))


def is_grounded(value: str, grounding: Grounding) -> bool:
    if not (value or "").strip():
        return False
    if grounding.is_empty():
        return False
    return appears_in(value, grounding.all_text())


# --------------------------------------------------------------------------- #
# Field-level correction helpers
# --------------------------------------------------------------------------- #


def ensure(value: str | None, placeholder: str = NOT_SPECIFIED) -> str:
    """A non-empty string, else the placeholder. Never invents content."""
    v = normalize_space(value or "")
    return v if v else placeholder


def _blank(value: str | None) -> bool:
    return not normalize_space(value or "")


def correct_free_text(
    validator: Validator,
    field_name: str,
    value: str,
    grounding: Grounding,
    *,
    source: str = "",
    allow_unsupported: bool = True,
    fallback: str = "",
) -> str:
    """Validate a free-text field, replacing it when unsupported or missing.

    ``allow_unsupported=False`` keeps the value and reports the verdict instead of
    deleting it — used for the deterministic document types, where the value comes
    from an authoritative stored row rather than from the model.
    """
    if _blank(value):
        validator.record(field_name, FieldVerdict.MISSING, NOT_SPECIFIED, source,
                         "No value recorded in the project data.")
        return fallback or NOT_SPECIFIED
    if is_grounded(value, grounding):
        validator.record(field_name, FieldVerdict.SUPPORTED, value, source)
        return value
    if allow_unsupported:
        validator.record(field_name, FieldVerdict.UNSUPPORTED, value, source,
                         "Value could not be traced to project data and was removed.")
        return fallback or NOT_SPECIFIED
    validator.record(field_name, FieldVerdict.UNSUPPORTED, value, source,
                     "Value could not be traced to the source documents.")
    return value


def correct_choice(
    validator: Validator,
    field_name: str,
    value: str,
    grounding: Grounding,
    *,
    choices: tuple[str, ...],
    source: str = "",
    allow_unsupported: bool = True,
) -> str:
    """Validate a controlled-vocabulary field (priority, status, level)."""
    v = normalize_space(value or "")
    if not v:
        validator.record(field_name, FieldVerdict.MISSING, NOT_SPECIFIED, source,
                         "No value recorded in the project data.")
        return NOT_SPECIFIED
    canonical = _canonical_choice(v, choices)
    if canonical is None:
        if allow_unsupported:
            validator.record(field_name, FieldVerdict.UNSUPPORTED, v, source,
                             "Value is not one of the recorded levels and was removed.")
            return NOT_SPECIFIED
        validator.record(field_name, FieldVerdict.UNSUPPORTED, v, source,
                         "Value is not one of the recorded levels.")
        return v
    if grounding.is_empty() or appears_in(canonical, grounding.all_text()):
        validator.record(field_name, FieldVerdict.SUPPORTED, canonical, source)
        return canonical
    if allow_unsupported:
        validator.record(field_name, FieldVerdict.UNSUPPORTED, v, source,
                         "Level could not be traced to project data and was removed.")
        return NOT_SPECIFIED
    validator.record(field_name, FieldVerdict.UNSUPPORTED, v, source,
                     "Level could not be traced to the source documents.")
    return canonical


def _canonical_choice(value: str, choices: tuple[str, ...]) -> str | None:
    v = value.strip().lower()
    for choice in choices:
        if v == choice.lower():
            return choice
    aliases = {
        "not started": "Not Started",
        "pending": "Not Started",
        "in progress": "In Progress",
        "blocked": "Blocked",
        "completed": "Completed",
        "done": "Completed",
        "open": "Open",
        "resolved": "Resolved",
        "closed": "Closed",
        "mitigated": "Mitigated",
        "tbd": NOT_SPECIFIED,
        "n/a": NOT_SPECIFIED,
        "na": NOT_SPECIFIED,
        "none": NOT_SPECIFIED,
        "unknown": NOT_SPECIFIED,
    }
    mapped = aliases.get(v)
    if mapped is None:
        return None
    for choice in choices:
        if mapped.lower() == choice.lower():
            return choice
    return None


def correct_list(
    validator: Validator,
    field_name: str,
    values: list[str],
    grounding: Grounding,
    *,
    source: str = "",
    empty_message: str = NO_CRITERIA,
) -> list[str]:
    """Validate a list of statements, dropping the ones that are not grounded."""
    kept: list[str] = []
    dropped = 0
    for raw in values:
        v = normalize_space(raw or "")
        if not v:
            continue
        if is_grounded(v, grounding):
            kept.append(v)
        else:
            dropped += 1
    if kept:
        validator.record(field_name, FieldVerdict.SUPPORTED, "; ".join(kept[:3]), source)
        if dropped:
            validator.corrected()
            validator.note(
                f"{dropped} acceptance criterion/criteria that could not be traced to the "
                "project documents were removed."
            )
        return kept
    if values:
        validator.record(field_name, FieldVerdict.UNSUPPORTED, empty_message, source,
                         "No stated criterion could be traced to the project documents.")
    else:
        validator.record(field_name, FieldVerdict.MISSING, empty_message, source,
                         "The project documents state no criteria for this item.")
    return []


def correct_evidence(
    validator: Validator,
    field_name: str,
    quote: str,
    source_text: str,
    *,
    source: str = "",
) -> str:
    """Keep an evidence quote only when it is a real span of the source text."""
    q = normalize_space(quote or "")
    if not q:
        validator.record(field_name, FieldVerdict.MISSING, NOT_SPECIFIED, source,
                         "No evidence recorded.")
        return NOT_SPECIFIED
    haystack = normalize_space(source_text or "").lower()
    probe = q.lower()
    if probe in haystack:
        validator.record(field_name, FieldVerdict.SUPPORTED, q, source)
        return q
    # A quote may be a trimmed span; accept it when its distinctive words are all present.
    words = significant_words(q)
    if words and all(w in haystack for w in words):
        validator.record(field_name, FieldVerdict.SUPPORTED, q, source)
        return q
    validator.record(field_name, FieldVerdict.UNSUPPORTED, q, source,
                     "Quote was not found in the retrieved source text and was replaced.")
    return NOT_SPECIFIED


def record_date_trace(
    validator: Validator,
    field_name: str,
    trace: dict,
    *,
    source: str = "",
) -> None:
    """Record a due date, flagging a conflict resolved in favour of the document."""
    effective = trace.get("effective_date") or NOT_SPECIFIED
    if trace.get("conflict"):
        validator.record(
            field_name,
            FieldVerdict.CONFLICT,
            effective,
            source or "Document",
            f"Admin-entered date {trace.get('admin_date')} differs from the explicit date in "
            f"{trace.get('document') or 'the source document'} "
            f"({trace.get('document_date')}); the document date is used.",
        )
        validator.note(
            "An explicit date in a source document takes precedence over a conflicting "
            "administrator-entered date. Both values are retained for traceability."
        )
        return
    origin = trace.get("source") or "Not specified"
    if trace.get("vague_in_source"):
        validator.note(
            "The source document names a deadline only in relative terms (for example "
            "\u201cnext week\u201d). No calendar date was inferred from it."
        )
    if trace.get("effective_date") in (None, "", NOT_SPECIFIED):
        validator.record(field_name, FieldVerdict.MISSING, NOT_SPECIFIED, origin,
                         "No explicit date in the document and no administrator-entered date.")
        return
    validator.record(field_name, FieldVerdict.SUPPORTED, effective, origin,
                     f"Explicit date found in {trace.get('document') or 'the source document'}."
                     if origin == "Document"
                     else "Administrator-entered date; no explicit date in the source documents.")


def record_field(
    validator: Validator,
    field_name: str,
    value: str,
    *,
    source: str = "",
    note: str = "",
) -> None:
    """Record a resolved field with no correction applied.

    Used for identifiers, relationships and source attribution, where the value is
    derived from stored records rather than generated text and therefore needs no
    support test.
    """
    if not normalize_space(value) or value == NOT_SPECIFIED:
        validator.record(field_name, FieldVerdict.MISSING, NOT_SPECIFIED, source,
                         note or "No related record exists in the project data.")
        return
    validator.record(field_name, FieldVerdict.SUPPORTED, value, source, note)


def record_conflicting_record(
    validator: Validator,
    field_name: str,
    recorded_value: str,
    document_value: str,
    *,
    source: str = "",
) -> None:
    """A stored Milestone 2 value that the source document states differently.

    Reported, never silently overwritten: changing a recorded risk assessment is
    outside what documentation generation is allowed to do.
    """
    if not normalize_space(document_value):
        validator.record(field_name, FieldVerdict.SUPPORTED, recorded_value, source)
        return
    if _same_level(recorded_value, document_value):
        validator.record(field_name, FieldVerdict.SUPPORTED, recorded_value, source)
        return
    validator.record(
        field_name,
        FieldVerdict.CONFLICT,
        recorded_value,
        source,
        f"The source document states '{document_value}'; the recorded value is "
        f"'{recorded_value}'. The recorded value is preserved unchanged.",
    )


def _same_level(a: str, b: str) -> bool:
    return normalize_space(a or "").lower() == normalize_space(b or "").lower()


# --------------------------------------------------------------------------- #
# Artifact-level validation
# --------------------------------------------------------------------------- #



def validate_story(
    story: dict,
    grounding: Grounding,
    *,
    source_document: str = "",
    field_name_prefix: str = "",
) -> tuple[dict, Validator]:
    """Validate and correct one user story in place; returns ``(story, validator)``.

    The story is never dropped here — an ungrounded *field* is corrected to the
    explicit placeholder, which is what the specification asks for. (A story whose
    requirement cannot be grounded at all is dropped earlier, by the generator.)
    """
    v = Validator()

    def name(field_name: str) -> str:
        return f"{field_name_prefix}{field_name}" if field_name_prefix else field_name

    story["as_a"] = correct_free_text(v, name("actor"), story.get("as_a", ""), grounding,
                                      source=source_document)
    story["i_want"] = ensure(story.get("i_want", ""))
    v.record(name("requirement"), FieldVerdict.SUPPORTED, story["i_want"], source_document,
             "Requirement text retained as generated.")

    story["so_that"] = correct_free_text(v, name("benefit"), story.get("so_that", ""), grounding,
                                         source=source_document)
    story["priority"] = correct_choice(v, name("priority"), story.get("priority", ""), grounding,
                                       choices=("Critical", "High", "Medium", "Low"),
                                       source=source_document)
    story["status"] = correct_choice(v, "status", story.get("status", ""), grounding,
                                     choices=("Proposed", "Approved", "In Progress", "Completed",
                                              "Rejected"),
                                     source=source_document)
    story["acceptance_criteria"] = correct_list(
        v, name("acceptance_criteria"), list(story.get("acceptance_criteria") or []), grounding,
        source=source_document,
    )
    story["related_requirement"] = correct_free_text(
        v, "related_requirement", story.get("related_requirement", ""), grounding,
        source=source_document,
    )

    # Source attribution must name a document that actually belongs to this project.
    named = normalize_space(story.get("source_document", ""))
    haystack = grounding.document_text.lower()
    if named and named.lower() in haystack:
        v.record(name("source_document"), FieldVerdict.SUPPORTED, named, named)
    elif named:
        v.record(name("source_document"), FieldVerdict.UNSUPPORTED, named, named,
                 "Cited document was not found in the retrieved text.")
        story["source_document"] = NOT_SPECIFIED
    else:
        v.record(name("source_document"), FieldVerdict.MISSING, NOT_SPECIFIED, "",
                 "No source document recorded.")
        story["source_document"] = NOT_SPECIFIED

    story["source_evidence"] = correct_evidence(
        v, name("source_evidence"), story.get("source_evidence", ""), grounding.all_text(),
        source=story.get("source_document", ""),
    )
    return story, v


def merge_validators(validators: list[Validator]) -> Validator:
    """Combine per-artifact validators into one document-level summary."""
    combined = Validator()
    for v in validators:
        for f in v.summary.findings:
            combined.summary.findings.append(f)
            if f.verdict is FieldVerdict.SUPPORTED:
                combined.summary.supported += 1
            elif f.verdict is FieldVerdict.MISSING:
                combined.summary.not_specified += 1
            elif f.verdict is FieldVerdict.CONFLICT:
                combined.summary.conflicts += 1
            else:
                combined.summary.unsupported_removed += 1
        combined.summary.corrected += v.summary.corrected
        for n in v.notes:
            combined.note(n)
    return combined


__all__ = [
    "ACTION_FIELDS",
    "Grounding",
    "RISK_FIELDS",
    "STORY_FIELDS",
    "UNVERIFIED",
    "VERIFIED",
    "VERIFIED_WITH_NOTES",
    "Validator",
    "appears_in",
    "correct_choice",
    "correct_evidence",
    "correct_free_text",
    "correct_list",
    "ensure",
    "is_grounded",
    "merge_validators",
    "record_conflicting_record",
    "record_date_trace",
    "record_field",
    "validate_story",
]
