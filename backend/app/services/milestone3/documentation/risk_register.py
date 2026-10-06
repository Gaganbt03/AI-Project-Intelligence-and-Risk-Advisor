"""Risk Register builder — deterministic, zero LLM involvement.

Two structural rules drive this module.

**1. The register contains risks and nothing else.**
Milestone 2's Risk Detection Agent writes everything it recognises into the
``risks`` table, which in practice also holds rows the project's own documents
label ``B001`` (blocker) or ``A001`` (action item). :func:`classify_risk_row`
sorts each row into ``risk`` / ``blocker`` / ``action`` using the reference the
source document gives it, cross-checked against the ``blockers`` and ``tasks``
tables. Non-risks are *excluded from the register* and reported separately in
``excluded`` — never converted into a risk, and never deleted from the database.

**2. Nothing that already exists is downgraded.**
Probability, impact, severity, score, status and mitigation come from the stored
Milestone 2 record. Where the source document states a different level the
disagreement is surfaced as a conflict rather than silently applied — see
:mod:`app.services.milestone3.documentation.validator`.

Fields absent from both the record and the documents are reported as
``Not specified in project data.`` No owner, date, cause or contingency is ever
inferred.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Project, Risk
from app.services.milestone3.common import NOT_SPECIFIED, level, risk_band, severity_score
from app.services.milestone3.documentation import validator as validation
from app.services.milestone3.documentation.dates import resolve_due_date
from app.services.milestone3.documentation.evidence import source_labels_for
from app.services.milestone3.documentation.extraction import (
    KIND_ACTION,
    KIND_BLOCKER,
    KIND_RISK,
    DocumentIndex,
    EntityRecord,
    build_document_index,
    find_internal_ref,
    ground_record,
    normalize_space,
    parse_entity_ref,
    significant_words,
)

SCORE_MAX = 25

# Keyword -> category. First match wins, so the order is most-specific first.
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Schedule", ("delay", "schedule", "timeline", "deadline", "slip", "rebaseline", "late", "overdue")),
    ("Security", ("security", "vlan", "access approval", "compliance", "audit", "permission", "firewall")),
    ("Resource", ("resource", "staffing", "manpower", "hiring", "team", "capacity", "owner", "unassigned")),
    ("Dependency", ("dependency", "dependencies", "vendor", "third-party", "integration",
                    "waiting on", "pending approval")),
    ("Technical", ("gateway", "wifi", "wi-fi", "sensor", "model", "algorithm", "accuracy",
                   "scalability", "latency", "throughput")),
    ("Data", ("data", "dataset", "historical", "timestamp", "sandbox key", "quality", "migration")),
    ("Quality", ("bug", "regression", "test", "coverage", "validation", "defect")),
    ("Scope", ("scope", "requirement", "ambigu", "unclear", "creep", "clarif")),
)


def categorize(risk: Risk) -> str:
    """Deterministic category from the risk's own recorded text."""
    haystack = f"{risk.title} {risk.description} {risk.evidence}".lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in haystack for kw in keywords):
            return category
    return "General"


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def _title_key(title: str) -> str:
    """Title with any leading reference and punctuation removed."""
    ref = parse_entity_ref(title) or None
    body = title
    if ref:
        body = title.replace(ref.raw, " ", 1)
    return " ".join(normalize_space(body).lower().split())


def _matches(a: str, b: str) -> bool:
    """Same title once the reference prefix and stop words are dropped."""
    ka, kb = _title_key(a), _title_key(b)
    if not ka or not kb:
        return False
    if ka == kb or ka in kb or kb in ka:
        return True
    wa = set(significant_words(ka))
    wb = set(significant_words(kb))
    if not wa or not wb:
        return False
    return len(wa & wb) >= max(1, min(len(wa), len(wb)) // 2)


def blocker_id_for(title: str, row_id: int | None = None) -> str:
    """``B002`` when the source document names the blocker, else ``BLK-0002``."""
    ref = parse_entity_ref(title)
    if ref and ref.kind == KIND_BLOCKER:
        return ref.canonical
    return f"BLK-{row_id:04d}" if row_id is not None else "BLK"


def action_id_for(title: str, row_id: int | None = None) -> str:
    """``A001`` when the source document names the action, else ``ACT-0001``."""
    ref = parse_entity_ref(title)
    if ref and ref.kind == KIND_ACTION:
        return ref.canonical
    return f"ACT-{row_id:04d}" if row_id is not None else "ACT"


def classify_risk_row(
    risk: Risk,
    index: DocumentIndex,
    *,
    blocker_titles: list[str] | None = None,
    task_titles: list[str] | None = None,
) -> tuple[str, str]:
    """Return ``(kind, reason)`` for one ``risks`` row.

    ``kind`` is ``risk``, ``blocker`` or ``action``. The reason is a short,
    factual explanation used for traceability in the UI and the DOCX.

    Precedence:

    1. An explicit reference in the title (``R001`` / ``B002`` / ``A003``) that
       the source documents also use. This is the strongest signal available and
       always decides the entity type.
    2. An explicit reference the documents do not use — still honoured, because
       the stored title is the project's own labelling.
    3. Title overlap with an existing ``blockers`` or ``tasks`` record.
    4. Otherwise the row stays a risk: that is what the Milestone 2 Risk
       Detection Agent recorded it as.
    """
    ref = parse_entity_ref(risk.title)
    grounded = index.record_for(ref) if ref else None

    if ref is not None:
        if grounded is not None:
            kind = ref.kind
            return kind, f"Referenced as {ref.canonical} in the source documents."
        if ref.kind in (KIND_BLOCKER, KIND_ACTION):
            target = "blocker" if ref.kind == KIND_BLOCKER else "action item"
            return ref.kind, f"Recorded as {ref.canonical}, a {target}, with no matching " \
                             f"chunk in the uploaded documents."

    for title in blocker_titles or []:
        if _matches(risk.title, title):
            return KIND_BLOCKER, "Title matches an existing blocker record."
    for title in task_titles or []:
        if _matches(risk.title, title):
            return KIND_ACTION, "Title matches an existing task record."

    if ref is not None:
        return KIND_RISK, f"Referenced as {ref.canonical} in the source documents."
    return KIND_RISK, ("Recorded by risk detection with no blocker or "
                       "action reference.")


# --------------------------------------------------------------------------- #
# Per-project entity registries
# --------------------------------------------------------------------------- #


@dataclass
class BlockerEntity:
    """One blocker as this project records it, with a stable per-project id.

    Milestone 2 spreads blockers across two tables: the ``blockers`` table, and the
    ``risks`` rows that the source documents label ``B...``. A project usually has
    the second group only, sometimes both — the same real-world blocker written up
    twice. :func:`blocker_registry` merges the two groups into one list, so every
    blocker gets exactly one identifier, one resolution action, and one place in
    the ``RISK -> BLOCKER -> ACTION -> OWNER -> DUE DATE`` chain.

    ``blocker_id`` is a **per-project sequence** (``BLK-0001``) rather than a
    database row id. Row ids are global, so using them would make an identifier
    depend on how many other projects exist and would leak another project's
    volume into a download.
    """

    blocker_id: str = ""
    document_ref: str = ""
    title: str = ""
    description: str = ""
    evidence: str = ""
    severity: str = ""
    status: str = ""
    owner: str = ""
    expected_resolution: str = ""
    source_document_id: int | None = None
    source_type: str = ""
    created_at: object = None
    has_blocker_row: bool = False

    @property
    def numbering(self) -> tuple:
        """Sort key: document order first, then unreferenced rows by title.

        >>> BlockerEntity(document_ref="B002", title="x").numbering[:2]
        (False, 2)
        >>> BlockerEntity(document_ref="", title="x").numbering[0]
        True
        """
        ref = self.document_ref
        number = int(ref[1:]) if len(ref) > 1 and ref[1:].isdigit() else 0
        return (ref == "", number, not self.has_blocker_row, self.title.lower())


@dataclass
class ActionEntity:
    """One action item as this project records it, with a stable per-project id.

    Identifiers keep the source document's own label whenever the documents give
    one (``A001``); otherwise they come from a per-project ``ACT-nnnn`` sequence,
    which keeps them stable across regenerations and independent of any other
    project's data.
    """

    action_id: str = ""
    document_ref: str = ""
    title: str = ""
    row_id: int | None = None
    is_task: bool = False

    @property
    def numbering(self) -> tuple:
        ref = self.document_ref
        number = int(ref[1:]) if len(ref) > 1 and ref[1:].isdigit() else 0
        return (ref == "", number, not self.is_task, self.title.lower())


def _entity_from_row(row, has_blocker_row: bool, record: EntityRecord | None) -> BlockerEntity:
    """Normalise a ``blockers`` row or a ``risks`` row into one blocker entity.

    ``Risk`` has no ``owner`` or ``expected_resolution`` column, so those are read
    with ``getattr`` rather than assumed.
    """
    title = normalize_space(getattr(row, "title", ""))
    ref = record.ref if (record is not None and record.ref is not None) else parse_entity_ref(title)
    return BlockerEntity(
        document_ref=ref.canonical if ref else "",
        title=title,
        description=normalize_space(getattr(row, "description", "") or ""),
        evidence=normalize_space(getattr(row, "evidence", "") or ""),
        severity=normalize_space(getattr(row, "severity", "") or ""),
        status=normalize_space(getattr(row, "status", "") or ""),
        owner=normalize_space(getattr(row, "owner", "") or ""),
        expected_resolution=normalize_space(getattr(row, "expected_resolution", "") or ""),
        source_document_id=getattr(row, "source_document_id", None),
        source_type=getattr(row, "source_type", "") or "",
        created_at=getattr(row, "created_at", None),
        has_blocker_row=has_blocker_row,
    )


def document_blocker_rows(risk_rows: list[Risk], index: DocumentIndex) -> list[Risk]:
    """``risks`` rows the source documents label ``B...``.

    Milestone 2's Risk Detection Agent files these under the wrong entity type.
    They belong to the blocker chain and must never reach the risk register.
    """
    out: list[Risk] = []
    for row in risk_rows:
        ref = parse_entity_ref(row.title)
        if ref and ref.kind == KIND_BLOCKER and index.record_for(ref) is not None:
            out.append(row)
    return out


def document_action_risks(db, project_id: int, index: DocumentIndex) -> list[Risk]:
    """``risks`` rows the source documents label ``A...``.

    The counterpart of :func:`document_blocker_rows`: action items Milestone 2's
    Risk Detection Agent filed under the wrong entity type. They belong to the
    action item list and must never reach the risk register.
    """
    from app.services.milestone3.documentation.evidence import existing_risks

    out: list[Risk] = []
    for row in existing_risks(db, project_id):
        ref = parse_entity_ref(row.title)
        if ref and ref.kind == KIND_ACTION and index.record_for(ref) is not None:
            out.append(row)
    return out


def blocker_registry(db, project: Project, index: DocumentIndex) -> list[BlockerEntity]:
    """Every blocker this project records, each with one identifier."""
    from app.models import Blocker
    from app.services.milestone3.documentation.evidence import existing_risks

    stored = (
        db.query(Blocker).filter(Blocker.project_id == project.id).order_by(Blocker.id).all()
    )
    candidates: list[tuple[object, bool]] = [(row, True) for row in stored]
    candidates += [(row, False) for row in document_blocker_rows(existing_risks(db, project.id),
                                                                 index)]

    merged: dict[str, BlockerEntity] = {}
    for row, has_blocker_row in candidates:
        record = ground_record(index, row.title)
        ref = record.ref if (record is not None and record.ref is not None) else parse_entity_ref(
            row.title
        )
        key = ref.key() if ref else _title_key(row.title)
        if not key:
            continue
        current = merged.get(key)
        # A real ``blockers`` row outranks a ``risks``-table stand-in, so an owner
        # or severity the administrator recorded is never dropped in its favour.
        if current is None or (has_blocker_row and not current.has_blocker_row):
            merged[key] = _entity_from_row(row, has_blocker_row, record)

    ordered = sorted(merged.values(), key=lambda e: e.numbering)
    for sequence, entity in enumerate(ordered, start=1):
        entity.blocker_id = f"BLK-{sequence:04d}"
    return ordered


def action_registry(db, project: Project, index: DocumentIndex) -> list[ActionEntity]:
    """Every action item this project records, each with one identifier.

    Covers both carriers: the ``tasks`` table and the ``risks`` rows the documents
    label ``A...``. Sharing one registry with
    :func:`app.services.milestone3.documentation.risk_register.build_risk_register`
    guarantees that the risk register's "related actions" and the action item
    list can never disagree about what an action is called.
    """
    from app.models import Task

    tasks = db.query(Task).filter(Task.project_id == project.id).order_by(Task.id).all()
    document_actions = document_action_risks(db, project.id, index)

    merged: dict[str, ActionEntity] = {}
    for row, is_task in [(t, True) for t in tasks] + [(a, False) for a in document_actions]:
        record = ground_record(index, row.title)
        ref = record.ref if (record is not None and record.ref is not None) else parse_entity_ref(
            row.title
        )
        title = normalize_space(row.title)
        key = ref.key() if ref else _title_key(title)
        if not key:
            continue
        current = merged.get(key)
        if current is None or (is_task and not current.is_task):
            merged[key] = ActionEntity(
                document_ref=ref.canonical if ref else "",
                title=title,
                row_id=row.id,
                is_task=is_task,
            )

    ordered = sorted(merged.values(), key=lambda e: e.numbering)
    for sequence, entity in enumerate(ordered, start=1):
        entity.action_id = entity.document_ref or f"ACT-{sequence:04d}"
    return ordered


# --------------------------------------------------------------------------- #
# Related-entity linking
# --------------------------------------------------------------------------- #


def _match_words(title: str) -> set[str]:
    """Distinctive words of a title, with a naive plural fold.

    Used only for *linking* entities together — ``R003 Sensor shipment delay`` and
    ``B002 Delayed sensor shipment`` are the same subject — never for deciding
    whether a value is grounded, where the stricter rules in
    :mod:`~app.services.milestone3.documentation.validator` apply.
    """
    out: set[str] = set()
    for word in significant_words(_title_key(title)):
        out.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return out


def _shares_subject(a: str, b: str) -> bool:
    """True when the two titles are about the same subject.

    Two shared significant words is the bar: the Milestone 2 action titles in
    this project are single words ("frontend", "backend"), so a one-word rule
    would link every risk to every task.

    >>> _shares_subject("R001 MQTT gateway reliability", "R003 Sensor shipment delay")
    False
    >>> _shares_subject("R001 MQTT gateway reliability", "MQTT gateway uptime")
    True
    >>> _shares_subject("R001 MQTT gateway reliability", "frontend")
    False
    """
    wa = _match_words(a)
    wb = _match_words(b)
    if not wa or not wb:
        return False
    shared = wa & wb
    if not shared:
        return False
    if len(wa) == 1 or len(wb) == 1:
        # A single-word title only counts on an exact match.
        return shared == {next(iter(wa))} or shared == {next(iter(wb))}
    return len(shared) >= 2


def _related(other_title: str, risk: Risk) -> bool:
    """Strong, stored-text overlap only — never a guess.

    A reference carried by ``other_title`` that also appears in the risk's stored
    record counts as an explicit link; otherwise the two titles must share two
    identifying terms.

    Only ``other_title``'s own reference is tested. Folding the risk's reference
    in would compare ``R001`` against a haystack that starts with ``R001`` and
    report every task and blocker as related to every risk.
    """
    ref = parse_entity_ref(other_title)
    if ref:
        haystack = f"{(risk.title or '')} {(risk.description or '')}".lower()
        if ref.raw.lower() in haystack:
            return True
    return _shares_subject(other_title, risk.title)


# --------------------------------------------------------------------------- #
# Entry construction
# --------------------------------------------------------------------------- #


def build_risk_entry(
    risk: Risk,
    doc_names: dict[int, str],
    index: DocumentIndex,
    *,
    related_blockers: list[tuple[str, str]] | None = None,
    related_actions: list[tuple[str, str]] | None = None,
) -> tuple[dict, validation.Validator]:
    """One register row, in the exact field order the specification requires."""
    v = validation.Validator()
    record = ground_record(index, risk.title)
    field_source = record.chunk.document if record else ""

    source_document = doc_names.get(risk.source_document_id) if risk.source_document_id else ""
    if not source_document and record:
        source_document = record.chunk.document
    grounding = validation.Grounding(
        document_text=index.haystack(),
        structured_text=f"{risk.title} {risk.description or ''} {risk.evidence or ''}",
    )

    # -- Risk ID ---------------------------------------------------------- #
    ref = parse_entity_ref(risk.title) or None
    internal = find_internal_ref(risk.title)
    risk_id = ref.canonical if (ref and ref.kind == KIND_RISK) else (internal or f"RISK-{risk.id:04d}")
    v.record("risk_id", validation.FieldVerdict.SUPPORTED, risk_id, field_source,
             "Identifier taken from the source document reference."
             if ref else "Identifier derived from the stored record.")

    # -- Description ------------------------------------------------------ #
    description = normalize_space(risk.description or "")
    if not description and record:
        description = normalize_space(record.value("effect"))
    description = validation.correct_free_text(
        v, "description", description, grounding, source=field_source)

    # -- Category --------------------------------------------------------- #
    category = categorize(risk)
    v.record("category", validation.FieldVerdict.SUPPORTED, category, "",
             "Classified from the recorded risk text.")

    # -- Probability / Impact / Severity ----------------------------------- #
    probability = (risk.probability or "").strip().title() or "Medium"
    impact = (risk.impact or "").strip().title() or "Medium"
    severity = (risk.severity or "").strip().title() or "Medium"

    if record:
        validation.record_conflicting_record(v, "probability", probability,
                                             record.value("probability"),
                                             source=field_source)
        validation.record_conflicting_record(v, "impact", impact, record.value("impact"),
                                             source=field_source)
    else:
        v.record("probability", validation.FieldVerdict.SUPPORTED, probability, "",
                 "Probability preserved from the recorded risk assessment.")
        v.record("impact", validation.FieldVerdict.SUPPORTED, impact, "",
                 "Impact preserved from the recorded risk assessment.")

    score = severity_score(risk.severity, risk.probability, risk.impact)
    v.record("score", validation.FieldVerdict.SUPPORTED, f"{score}/{SCORE_MAX}", "",
             "Deterministically computed from the recorded severity, probability and impact.")
    v.record("severity", validation.FieldVerdict.SUPPORTED, severity, "",
             "Severity preserved from the recorded risk assessment.")

    status = normalize_space(risk.status or "") or "Open"
    if record:
        validation.record_conflicting_record(v, "status", status, record.value("status"),
                                             source=field_source)
    else:
        v.record("status", validation.FieldVerdict.SUPPORTED, status, "",
                 "Status preserved from the recorded risk assessment.")

    # -- Owner ------------------------------------------------------------- #
    owner = normalize_space(record.value("owner")) if record else ""
    owner = validation.correct_free_text(v, "owner", owner, grounding, source=field_source)

    # -- Mitigation -------------------------------------------------------- #
    mitigation = normalize_space(risk.recommended_action or "")
    if not mitigation and record:
        mitigation = normalize_space(record.value("mitigation"))
    mitigation = validation.correct_free_text(v, "mitigation", mitigation, grounding,
                                              source=field_source,
                                              allow_unsupported=False)

    # -- Contingency ------------------------------------------------------- #
    contingency = normalize_space(record.value("contingency")) if record else ""
    contingency = validation.correct_free_text(v, "contingency", contingency, grounding,
                                               source=field_source)

    # -- Related entities -------------------------------------------------- #
    blockers = [{"id": rid, "title": title} for rid, title in (related_blockers or [])]
    actions = [{"id": aid, "title": title} for aid, title in (related_actions or [])]
    v.record("related_blockers",
             validation.FieldVerdict.SUPPORTED if blockers else validation.FieldVerdict.MISSING,
             ", ".join(b["id"] for b in blockers) or NOT_SPECIFIED, "",
             "Linked from the existing blocker records." if blockers
             else "No blocker is recorded as related to this risk.")
    v.record("related_actions",
             validation.FieldVerdict.SUPPORTED if actions else validation.FieldVerdict.MISSING,
             ", ".join(a["id"] for a in actions) or NOT_SPECIFIED, "",
             "Linked from the existing action records." if actions
             else "No action item is recorded as related to this risk.")

    # -- Source ------------------------------------------------------------ #
    # Milestone 2 stored short markers such as "[1]" in `evidence`, which are not
    # quotable on their own. The verbatim chunk text is preferred whenever the
    # documents describe this entity, and the stored marker is only used when it
    # really is a span of the source text.
    stored_evidence = normalize_space(risk.evidence or "")
    quoteable = index.haystack().lower()
    if record and record.evidence():
        evidence = record.evidence()
        validation.correct_evidence(v, "evidence", evidence, index.haystack(),
                                    source=source_document)
    elif stored_evidence and stored_evidence.lower() in quoteable:
        evidence = stored_evidence
        v.record("evidence", validation.FieldVerdict.SUPPORTED, evidence, source_document)
    else:
        evidence = NOT_SPECIFIED
        if stored_evidence:
            v.record("evidence", validation.FieldVerdict.UNSUPPORTED, stored_evidence,
                     source_document,
                     "Stored evidence marker is not a quotable span of the source document.")
        else:
            v.record("evidence", validation.FieldVerdict.MISSING, NOT_SPECIFIED, "",
                     "No evidence recorded.")
    v.record("source", validation.FieldVerdict.SUPPORTED if source_document
             else validation.FieldVerdict.MISSING, source_document or NOT_SPECIFIED, "",
             "Document the risk was extracted from."
             if source_document else "No source document recorded.")

    # -- Due date (risks carry one only when the documents state it) -------- #
    date_trace = resolve_due_date(None, record.due if record else None).to_dict()
    validation.record_date_trace(v, "due_date", date_trace, source=field_source)

    # -- Conflicts ---------------------------------------------------------- #
    # Where the documents disagree with the recorded Milestone 2 assessment the
    # recorded value is kept, so the disagreement has to stay visible: the UI,
    # the Markdown and the DOCX all read this list.
    conflicts = [
        f"{finding.field.replace('_', ' ').capitalize()}: {finding.note}"
        for finding in v.summary.findings
        if finding.verdict is validation.FieldVerdict.CONFLICT and finding.note
    ]

    entry = {
        "risk_id": risk_id,
        "source_risk_id": risk.id,
        "title": normalize_space(risk.title) or "Untitled risk",
        "description": description,
        "category": category,
        "probability": probability,
        "impact": impact,
        "risk_score": score,
        "risk_score_max": SCORE_MAX,
        "severity": severity,
        "severity_band": risk_band(score),
        "probability_level": level(risk.probability),
        "status": status,
        "owner": owner,
        "mitigation": mitigation,
        "contingency": contingency,
        "related_blockers": blockers,
        "related_actions": actions,
        "source_document": source_document or NOT_SPECIFIED,
        "source_evidence": evidence,
        "evidence": evidence,
        "date_trace": date_trace,
        "conflicts": conflicts,
        "validation": v.summary.to_dict(),
        "field_verdicts": {f.field: f.verdict.value for f in v.summary.findings},
    }
    return entry, v


# --------------------------------------------------------------------------- #
# Register assembly
# --------------------------------------------------------------------------- #


def build_risk_register(db, project: Project) -> dict:
    """Assemble the full risk register for a project from existing rows."""
    from app.models import Blocker, Task
    from app.services.milestone3.documentation.evidence import existing_risks

    doc_names = source_labels_for(db, project.id)
    rows = existing_risks(db, project.id)
    index = build_document_index(db, project.id)

    blocker_titles = [b.title for b in db.query(Blocker).filter(Blocker.project_id == project.id).all()]
    task_titles = [t.title for t in db.query(Task).filter(Task.project_id == project.id).all()]

    classified: list[tuple[Risk, str, str]] = []
    excluded: list[dict] = []
    for risk in rows:
        kind, reason = classify_risk_row(
            risk, index, blocker_titles=blocker_titles, task_titles=task_titles
        )
        classified.append((risk, kind, reason))
        if kind != KIND_RISK:
            ref = parse_entity_ref(risk.title)
            excluded.append({
                "risk_id": ref.canonical if ref else f"RISK-{risk.id:04d}",
                "title": normalize_space(risk.title),
                "classified_as": kind,
                "reason": reason,
                "kept_in": "Blocker records" if kind == KIND_BLOCKER else "Action item list",
            })

    # One numbering shared with the action item list, so a blocker or an action is
    # called the same thing in every generated document.
    blocker_registry_ = blocker_registry(db, project, index)
    actions = action_registry(db, project, index)

    entries: list[dict] = []
    validators: list[validation.Validator] = []
    for risk, kind, _reason in classified:
        if kind != KIND_RISK:
            continue
        related_blockers = [
            (entity.blocker_id, entity.title)
            for entity in blocker_registry_ if _related(entity.title, risk)
        ]
        related_actions = [
            (entity.action_id, entity.title)
            for entity in actions if _related(entity.title, risk)
        ]
        entry, v = build_risk_entry(
            risk, doc_names, index,
            related_blockers=related_blockers, related_actions=related_actions,
        )
        entries.append(entry)
        validators.append(v)

    entries.sort(key=lambda e: (-e["risk_score"], e["risk_id"]))

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["severity_band"]] = counts.get(e["severity_band"], 0) + 1

    combined = validation.merge_validators(validators)
    summary = combined.finish()

    return {
        "doc_type": "risk_register",
        "title": "Project Risk Register",
        "project": project.name,
        "project_id": project.id,
        "total": len(entries),
        "counts_by_band": counts,
        "max_score": max((e["risk_score"] for e in entries), default=0),
        "total_exposure": sum(e["risk_score"] for e in entries),
        "entries": entries,
        "excluded": excluded,
        "insufficient_evidence": not entries,
        "reason": "" if entries else NOT_SPECIFIED,
        "validation": summary.to_dict(),
        "validation_notes": combined.notes,
        "notes": [
            "Risk Score = severity x probability x impact, each mapped to "
            "CRITICAL/HIGH/MEDIUM/LOW (10/6/3/1) and HIGH/MEDIUM/LOW (3/2/1), "
            f"clamped to 1-{SCORE_MAX}. Computed deterministically from stored values.",
            "The register contains risks only. Records the source documents label as "
            "blockers (B...) or action items (A...) are listed under 'excluded' and are "
            "kept in their own category — they are never converted into risks.",
            "Probability, impact, severity, score and status are preserved from the "
            "recorded risk assessments. Where a source document states a different level the "
            "disagreement is reported as a conflict and the recorded value is left unchanged.",
            f"Fields absent from both the record and the documents read '{NOT_SPECIFIED}'.",
        ],
    }


__all__ = [
    "SCORE_MAX",
    "ActionEntity",
    "BlockerEntity",
    "action_id_for",
    "action_registry",
    "blocker_id_for",
    "blocker_registry",
    "build_risk_entry",
    "build_risk_register",
    "categorize",
    "classify_risk_row",
    "document_blocker_rows",
]
