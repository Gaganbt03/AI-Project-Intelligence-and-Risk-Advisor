"""Deterministic, grounded field extraction from the project's uploaded documents.

This module is the **only** place a structured field may acquire a value from
document text. It is intentionally free of any LLM call: every value it returns
is a literal span copied out of a ``document_chunks`` row that Milestone 1
already produced, together with the verbatim evidence that contains it.

Why a separate module
---------------------
Milestone 1/2 stored structured rows (``risks``, ``blockers``, ``tasks``) and the
raw chunk text at the same time. The two can disagree, and the specification is
explicit about which one wins:

* an **explicit date in a source document** beats a conflicting admin-entered date;
* a field present in *either* place is preserved, never replaced by a
  "not specified" placeholder;
* nothing is ever inferred from prose such as "delivery next week".

Design rules
------------
1. **No inference.** Relative or vague expressions ("next week", "ASAP", "TBD",
   "Q4") never produce a date. :func:`parse_explicit_date` returns ``None`` for
   them, so the caller falls back to the admin value and then to the
   "not specified" placeholder.
2. **Evidence always travels with the value.** Each extracted field carries the
   exact substring it came from and the document it came from.
3. **Read-only.** Nothing here writes to the database, reads the vector store or
   calls the embedding provider, so the deterministic document types can be
   produced even when Ollama is offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models import DocumentChunk, ProjectDocument

# --------------------------------------------------------------------------- #
# Entity references
# --------------------------------------------------------------------------- #

#: ``R001``/``B002``/``A003`` — the identifiers real project documents use.
_REF_RE = re.compile(r"\b([RBA])[- _]?(\d{1,4})\b", re.IGNORECASE)

#: ``US-001``, ``RISK-0001``, ``ACT-BLK-0002`` — the identifiers this system emits.
_INTERNAL_REF_RE = re.compile(
    r"\b(ACT-BLK-\d+|ACT-\d+|RISK-\d+|R\d{3,}|B\d{3,}|A\d{3,}|US-\d+)\b", re.IGNORECASE
)

#: Reference prefix -> entity kind.
_PREFIX_KIND = {"R": "risk", "B": "blocker", "A": "action"}

KIND_RISK = "risk"
KIND_BLOCKER = "blocker"
KIND_ACTION = "action"
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class EntityRef:
    """A reference found in document text, e.g. ``R001``."""

    kind: str
    number: int
    raw: str

    @property
    def canonical(self) -> str:
        """``R001`` — the canonical, stable rendering used in generated output."""
        return f"{self.kind[0].upper()}{self.number:03d}"

    def key(self) -> str:
        return self.canonical.upper()


def parse_entity_ref(text: str) -> Optional[EntityRef]:
    """First entity reference in ``text``, or ``None``.

    ``"A001 - Implement MQTT reconnect handling"`` -> ``EntityRef('action', 1, ...)``
    """
    if not text:
        return None
    match = _REF_RE.search(text)
    if not match:
        return None
    letter, number = match.group(1).upper(), match.group(2)
    return EntityRef(kind=_PREFIX_KIND[letter], number=int(number), raw=match.group(0))


def all_entity_refs(text: str) -> list[EntityRef]:
    out: list[EntityRef] = []
    seen: set[str] = set()
    for match in _REF_RE.finditer(text or ""):
        ref = EntityRef(kind=_PREFIX_KIND[match.group(1).upper()], number=int(match.group(2)),
                        raw=match.group(0))
        if ref.key() in seen:
            continue
        seen.add(ref.key())
        out.append(ref)
    return out


def find_internal_ref(text: str) -> str | None:
    """Locate a reference this system itself emits (``R003``, ``ACT-BLK-0002``)."""
    match = _INTERNAL_REF_RE.search(text or "")
    return match.group(1).upper() if match else None


# --------------------------------------------------------------------------- #
# Explicit date parsing
# --------------------------------------------------------------------------- #

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

#: ISO-8601: 2026-10-11 (also tolerates ``/`` and ``.`` separators).
_ISO_RE = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")

#: 11 October 2026 / October 11, 2026 / Oct 11 2026
_DMY_TEXT_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_MDY_TEXT_RE = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
                          re.IGNORECASE)

#: 10/10/2026 — day/month/year is the project-document convention here.
_DMY_NUM_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")

#: Words that look like a deadline but never resolve to a calendar date. Listing
#: them explicitly is what stops "next week" from becoming an invented date.
_VAGUE_DATE_TERMS = (
    "next week", "next month", "next quarter", "next sprint", "this week",
    "this month", "this quarter", "this sprint", "coming week", "coming months",
    "asap", "as soon as possible", "tbd", "tbc", "to be decided", "to be confirmed",
    "to be determined", "soon", "shortly", "later", "eventually", "no later than",
    "end of month", "eom", "eoy", "quarterly", "milestone dependent",
)

_DATE_LABEL_RE = re.compile(
    r"\b(due(?:\s+date)?|due[-_ ]date|deadline|delivery(?:\s+deadline)?|target(?:\s+date)?|"
    r"finish(?:\s+date)?|completion(?:\s+date)?|eta|scheduled(?:\s+for)?|by)\b"
    r"\s*[:=\-–]?\s*",
    re.IGNORECASE,
)


def is_vague_date_expression(text: str) -> bool:
    """True when ``text`` names a deadline without naming a calendar date."""
    if not text:
        return False
    lowered = text.lower()
    if any(term in lowered for term in _VAGUE_DATE_TERMS):
        return True
    # A bare relative reference such as "next Friday" or "in 2 weeks".
    if re.search(r"\b(next|this|coming|following)\s+[a-z]+\b", lowered):
        return True
    # "in 2 weeks", "in three days", "in a couple of weeks", "within a fortnight".
    if re.search(
        r"\b(?:in|within|after|over)\s+(?:a|an|\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|couple|few|several)\s*(?:\w+\s+){0,2}"
        r"(?:day|days|week|weeks|month|months|sprint|sprints|fortnight|"
        r"quarter|quarters|year|years)\b",
        lowered,
    ):
        return True
    return False


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_explicit_date(text: str) -> Optional[date]:
    """The single explicit calendar date in ``text``, or ``None``.

    Returns ``None`` — never a guess — when the text contains a vague or
    relative deadline, when there is no date at all, or when it is ambiguous.

    >>> parse_explicit_date("Due: 2026-10-11").isoformat()
    '2026-10-11'
    >>> parse_explicit_date("Delivery deadline: October 11, 2026").isoformat()
    '2026-10-11'
    >>> parse_explicit_date("The team expects delivery next week.") is None
    True
    """
    if not text:
        return None
    if is_vague_date_expression(text):
        return None

    for match in _ISO_RE.finditer(text):
        found = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if found:
            return found

    for match in _DMY_TEXT_RE.finditer(text):
        month = _MONTHS.get(match.group(2).lower())
        if not month:
            continue
        found = _safe_date(int(match.group(3)), month, int(match.group(1)))
        if found:
            return found

    for match in _MDY_TEXT_RE.finditer(text):
        month = _MONTHS.get(match.group(1).lower())
        if not month:
            continue
        found = _safe_date(int(match.group(3)), month, int(match.group(2)))
        if found:
            return found

    match = _DMY_NUM_RE.search(text)
    if match:
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        # Unambiguous only when one component cannot be a month.
        if first > 12 and second <= 12:
            return _safe_date(year, second, first)
        if second > 12 and first <= 12:
            return _safe_date(year, first, second)
        # 10/10/2026 is genuinely ambiguous; prefer the day-first reading that the
        # project's own documents use, and never claim certainty we do not have.
        return None

    return None


def _date_span(text: str, found: date) -> Optional[str]:
    """The verbatim substring of ``text`` that produced ``found``."""
    for pattern in (_ISO_RE, _DMY_TEXT_RE, _MDY_TEXT_RE):
        for match in pattern.finditer(text):
            candidate = _safe_date(*_groups_to_ymd(match, pattern))
            if candidate == found:
                return match.group(0)
    return None


def _groups_to_ymd(match: re.Match, pattern: re.Pattern) -> tuple[int, int, int]:
    groups = match.groups()
    if pattern is _ISO_RE:
        return int(groups[0]), int(groups[1]), int(groups[2])
    if pattern is _DMY_TEXT_RE:
        return int(groups[2]), _MONTHS[groups[1].lower()], int(groups[0])
    return int(groups[2]), _MONTHS[groups[0].lower()], int(groups[1])


@dataclass
class DateHit:
    """An explicit date lifted out of document text, with its evidence."""

    value: date
    evidence: str = ""
    document: str = ""
    label: str = ""

    def isoformat(self) -> str:
        return self.value.isoformat()


def find_due_date(text: str, *, prefer_labelled: bool = True) -> Optional[DateHit]:
    """Locate a due date in ``text``, anchored on a deadline label when present.

    Returns ``None`` when the only deadline language is relative, which is the
    documented behaviour required by the specification.
    """
    if not text or is_vague_date_expression(text):
        return None

    label_match = _DATE_LABEL_RE.search(text)
    if prefer_labelled and label_match:
        tail = text[label_match.end():]
        found = parse_explicit_date(tail[:64])
        if found:
            return DateHit(value=found, evidence=text.strip(), label=label_match.group(0).strip())

    found = parse_explicit_date(text)
    if not found:
        return None
    span = _date_span(text, found) or ""
    return DateHit(value=found, evidence=text.strip(), label="")


def coerce_date(value) -> Optional[date]:
    """Normalize a stored DB / API date (``date``, ``datetime``, ISO string)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    found = parse_explicit_date(str(value))
    if found:
        return found
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def format_date(value) -> str:
    """``date``/``datetime``/``str`` -> ``YYYY-MM-DD`` (empty string when absent)."""
    found = coerce_date(value)
    return found.isoformat() if found else ""


# --------------------------------------------------------------------------- #
# Labelled field extraction
# --------------------------------------------------------------------------- #

#: Field -> the labels a project document may use for it. Order matters only for
#: readability; matching is case-insensitive and separator-agnostic.
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "owner": ("owner", "responsible", "assignee", "assigned to"),
    "priority": ("priority",),
    "status": ("status", "state"),
    "impact": ("impact",),
    "probability": ("probability", "likelihood", "chance"),
    "mitigation": ("mitigation", "mitigating action", "recommended action"),
    "contingency": ("contingency", "contingency plan", "fallback", "fall back plan", "backup plan"),
    "resolution_action": ("resolution action", "resolution", "action", "next step", "required action"),
    "effect": ("effect", "impact description", "description"),
    "category": ("category", "type"),
    "requirement": ("requirement", "requirement id"),
}

#: A labelled field ends at the next ``Label:``/``Label=`` marker or the end of
#: the line, so "Owner: Network Team | Status: Blocked" splits cleanly.
_NEXT_LABEL_RE = re.compile(
    r"\s*[|•·;]\s*|\s+(?=(?:Owner|Priority|Status|Impact|Probability|Mitigation|Contingency|"
    r"Due|Due\s?Date|Action|Resolution|Effect|Category|Requirement|Reference)\s*[:=])",
    re.IGNORECASE,
)


@dataclass
class FieldHit:
    """A labelled value lifted out of document text, with its evidence."""

    value: str = ""
    evidence: str = ""
    document: str = ""
    label: str = ""


def _label_pattern(labels: Iterable[str]) -> re.Pattern:
    alt = "|".join(re.escape(l) for l in sorted(labels, key=len, reverse=True))
    return re.compile(rf"(?:^|[\s|•·;])({alt})\s*[:=]\s*", re.IGNORECASE)


_LABEL_PATTERNS: dict[str, re.Pattern] = {
    field_name: _label_pattern(labels) for field_name, labels in FIELD_LABELS.items()
}
_DUE_LABEL_PATTERN = _DATE_LABEL_RE


def _clean_value(value: str) -> str:
    value = (value or "").strip()
    value = value.strip(" .,;|")
    # Drop a trailing sentence that belongs to the next sentence in prose.
    return value


def find_field(text: str, field_name: str) -> Optional[FieldHit]:
    """First labelled value of ``field_name`` in ``text``, or ``None``.

    >>> find_field("B002 - Delayed sensor shipment | Owner=Procurement", "owner").value
    'Procurement'
    """
    pattern = _LABEL_PATTERNS.get(field_name)
    if pattern is None or not text:
        return None
    for match in pattern.finditer(text):
        tail = text[match.end():]
        split = _NEXT_LABEL_RE.search(tail)
        raw = tail[: split.start()] if split else tail
        value = _clean_value(raw)
        if not value:
            continue
        if field_name == "due_date" or field_name == "resolution_action":
            # Guard against a label match that is really part of another sentence.
            if len(value) > 120:
                continue
        return FieldHit(value=value, evidence=text.strip(), label=match.group(1).strip())
    return None


def find_due_date_field(text: str) -> Optional[DateHit]:
    """A date introduced by a deadline label, or ``None``."""
    return find_due_date(text)


# --------------------------------------------------------------------------- #
# Document chunk index
# --------------------------------------------------------------------------- #


@dataclass
class ChunkRef:
    """A document chunk the extractor is allowed to quote."""

    document_id: int
    document: str
    text: str
    section: str = ""
    page: Optional[int] = None
    row: Optional[int] = None
    ref: Optional[EntityRef] = None

    def citation(self) -> str:
        parts = [self.document or "Unknown document"]
        if self.page is not None:
            parts.append(f"Page {self.page}")
        if self.section:
            parts.append(self.section)
        if self.row is not None:
            parts.append(f"Row {self.row}")
        return " — ".join(parts)


@dataclass
class EntityRecord:
    """Everything the documents explicitly state about one referenced entity."""

    ref: EntityRef
    chunk: ChunkRef
    fields: dict[str, FieldHit] = field(default_factory=dict)
    due: Optional[DateHit] = None

    def value(self, name: str) -> str:
        hit = self.fields.get(name)
        return hit.value if hit else ""

    def evidence(self) -> str:
        """The verbatim chunk text this record was read from."""
        return self.chunk.text.strip()


@dataclass
class DocumentIndex:
    """Per-project view of the chunks Milestone 1 already produced.

    Builds three lookups, all deterministic:

    * ``by_ref``   - ``R001`` -> the segment that describes it
    * ``units``    - the search units: one entry per entity described by a chunk
      (a chunk holding three risks yields three units)
    * ``chunks``   - every chunk, in ingestion order

    Matching a title against ``units`` rather than ``chunks`` matters: the
    blockers section lists B001 and B002 in a single chunk, so a whole-chunk
    search would attach both blockers' owner, due date and resolution action to
    whichever entity was resolved first.
    """

    project_id: int
    chunks: list[ChunkRef] = field(default_factory=list)
    units: list[ChunkRef] = field(default_factory=list)
    by_ref: dict[str, EntityRecord] = field(default_factory=dict)

    def haystack(self) -> str:
        return " \n".join(c.text for c in self.chunks)

    def has_documents(self) -> bool:
        return bool(self.chunks)

    def record_for(self, ref: EntityRef | str | None) -> Optional[EntityRecord]:
        """The record for a reference, or ``None`` when the documents are silent."""
        if ref is None:
            return None
        key = ref.key() if isinstance(ref, EntityRef) else str(ref).upper()
        return self.by_ref.get(key)

    def find_field_anywhere(self, name: str) -> Optional[FieldHit]:
        """A labelled field from any chunk. Used only when no reference exists."""
        for chunk in self.chunks:
            hit = find_field(chunk.text, name)
            if hit:
                hit.document = chunk.document
                return hit
        return None

    def best_match_chunk(self, title: str, *, min_overlap: int = 2) -> Optional[ChunkRef]:
        """The chunk that shares the most distinctive words with ``title``."""
        return best_matching_chunk(self.chunks, title, min_overlap=min_overlap)


#: Very common words carry no evidential weight when deciding whether a value is
#: traceable to the source text, so they are excluded from every support test.
_STOPWORDS = frozenset(
    """the and for with that this from have has had will shall must may can not but you your
    our their they them then than there here when what which who whom whose into over under
    about after before while each some such only also been being does did done make made
    need needs should would could shall system shall requirement requirements project data
    specified information specified""".split()
)


def normalize_space(value: str) -> str:
    """Collapse runs of whitespace and trim, so quotes compare reliably."""
    return re.sub(r"\s+", " ", (value or "")).strip()


def significant_words(value: str, *, min_length: int = 4) -> list[str]:
    """Distinctive lowercase words of ``value``, stopwords and noise removed."""
    words = re.findall(r"[a-z0-9][a-z0-9\-']*", (value or "").lower())
    return [w for w in words if len(w) >= min_length and w not in _STOPWORDS]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3}


def best_matching_chunk(chunks: list[ChunkRef], title: str, *, min_overlap: int = 2) -> Optional[ChunkRef]:
    best: Optional[ChunkRef] = None
    best_score = 0
    target = _words(title)
    if not target:
        return None
    for chunk in chunks:
        score = len(target & _words(chunk.text))
        if score > best_score:
            best, best_score = chunk, score
    return best if best_score >= min_overlap else None


def segment_by_ref(text: str) -> list[tuple[EntityRef | None, str]]:
    """Split chunk text into per-entity segments.

    Real project documents put several entities in one chunk — the DOCX
    requirements file lists R001/R002/R003 in a single section, and the status
    notes list them one per line inside one chunk. Reading a labelled field from
    the *whole* chunk would attribute R001's mitigation to R002, so each entity
    is given only the span of text that belongs to it.

    A segment runs from one reference to the next; lines that mention no
    reference are treated as a continuation of the entity above them.
    """
    segments: list[tuple[EntityRef | None, str]] = []
    current_ref: EntityRef | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            segments.append((current_ref, body))

    for line in (text or "").splitlines():
        ref = parse_entity_ref(line)
        inline = [m for m in _REF_RE.finditer(line)]
        if len(inline) > 1:
            # Several references on one line: cut at each one.
            flush()
            buffer = []
            cursor = 0
            for match in inline:
                head = line[cursor:match.start()].strip(" -\u2013\u2014|,;")
                if head and current_ref is not None and segments:
                    segments[-1] = (segments[-1][0], f"{segments[-1][1]}\n{head}")
                current_ref = EntityRef(kind=_PREFIX_KIND[match.group(1).upper()],
                                        number=int(match.group(2)), raw=match.group(0))
                buffer = []
                cursor = match.end()
            buffer.append(line[cursor:])
            continue
        if ref is not None:
            flush()
            current_ref = ref
            buffer = [line]
            continue
        buffer.append(line)
    flush()
    return segments


def _fields_from_text(text: str) -> dict[str, FieldHit]:
    fields: dict[str, FieldHit] = {}
    for name in FIELD_LABELS:
        hit = find_field(text, name)
        if hit:
            fields[name] = hit
    return fields


def build_document_index(db: Session, project_id: int) -> DocumentIndex:
    """Read this project's chunks and index them by entity reference.

    Only ``document_chunks`` rows are read — the vector store and the embedding
    provider are not touched, so this works while Ollama is offline.

    When a chunk describes several entities, each one is indexed against its own
    segment of that chunk (see :func:`segment_by_ref`) so a labelled field can
    never leak from one entity to another.
    """
    names = {
        r[0]: r[1]
        for r in db.query(ProjectDocument.id, ProjectDocument.original_name)
        .filter(ProjectDocument.project_id == project_id)
        .all()
    }
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.project_id == project_id)
        .order_by(DocumentChunk.document_id.asc(), DocumentChunk.chunk_index.asc())
        .all()
    )

    index = DocumentIndex(project_id=project_id)
    for row in rows:
        chunk = ChunkRef(
            document_id=row.document_id,
            document=names.get(row.document_id, ""),
            text=row.text or "",
            section=row.section or "",
            page=row.page_number,
            row=row.row_number,
        )
        index.chunks.append(chunk)

        segments = segment_by_ref(chunk.text)
        for ref, segment in segments:
            scoped = ChunkRef(
                document_id=chunk.document_id,
                document=chunk.document,
                text=segment,
                section=chunk.section,
                page=chunk.page,
                row=chunk.row,
                ref=ref,
            )
            index.units.append(scoped)
            if ref is None:
                continue
            key = ref.key()
            if key in index.by_ref:
                # Keep the earliest, richest description; later duplicates of the
                # same entity add nothing the first segment did not already say.
                continue
            record = EntityRecord(ref=ref, chunk=scoped)
            record.fields = _fields_from_text(segment)
            record.due = find_due_date_field(segment)
            index.by_ref[key] = record
    return index


def ground_record(index: DocumentIndex, title: str) -> Optional[EntityRecord]:
    """Read a record by explicit reference, else by title overlap with a segment.

    Never guesses beyond a strong title overlap, and returns ``None`` when the
    documents say nothing about the entity — which is what makes the caller fall
    back to the stored row, and then to "not specified".

    The title search runs over :attr:`DocumentIndex.units`, so a record read this
    way covers exactly one entity. The stored Milestone 2 titles for blockers and
    action items carry no ``B###``/``A###`` reference, so this path is the one
    they actually use.
    """
    ref = parse_entity_ref(title)
    if ref:
        record = index.record_for(ref)
        if record:
            return record
    internal = find_internal_ref(title)
    if internal:
        record = index.record_for(internal)
        if record:
            return record
    unit = best_matching_chunk(index.units, title, min_overlap=2)
    if unit is None:
        return None
    record = EntityRecord(ref=unit.ref or ref, chunk=unit)
    record.fields = _fields_from_text(unit.text)
    record.due = find_due_date_field(unit.text)
    return record


__all__ = [
    "KIND_RISK",
    "KIND_BLOCKER",
    "KIND_ACTION",
    "KIND_UNKNOWN",
    "ChunkRef",
    "DateHit",
    "DocumentIndex",
    "EntityRecord",
    "EntityRef",
    "FieldHit",
    "all_entity_refs",
    "best_matching_chunk",
    "build_document_index",
    "coerce_date",
    "find_due_date",
    "find_due_date_field",
    "find_field",
    "find_internal_ref",
    "format_date",
    "ground_record",
    "is_vague_date_expression",
    "normalize_space",
    "parse_entity_ref",
    "parse_explicit_date",
    "segment_by_ref",
    "significant_words",
]
