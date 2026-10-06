"""Structured Action Item list builder — deterministic, zero LLM involvement.

Sources, in priority order. All three are **pre-existing rows**, so building the
list never creates data:

1. ``tasks`` where ``ai_generated = 1``  — Milestone 2 Action Item Agent output
2. ``tasks`` where ``ai_generated = 0``  — manually created work items
3. ``risks`` rows the source documents label ``A...`` — action items the Risk
   Detection Agent filed under the wrong entity type; they belong here, not in
   the risk register
4. ``blockers`` — each unresolved blocker implies exactly one resolution action

Every source row becomes exactly one entry, so the list cannot duplicate.

Field sourcing (the "never lose information" rule)
-------------------------------------------------
Each field is resolved in a fixed order, and the first value that exists wins:

    source document  ->  stored row  ->  "Not specified in project data."

A date is the exception: the *document* date always wins over the admin date
even when they disagree, because an explicit project deadline in the source
document is authoritative. Both values are retained on ``date_trace`` so the
comparison stays auditable — see
:mod:`app.services.milestone3.documentation.dates`.
"""

from __future__ import annotations

import re

from app.models import Blocker, Project, Risk, Task, User  # noqa: F401
from app.services.milestone3.common import NOT_SPECIFIED
from app.services.milestone3.documentation import validator as validation
from app.services.milestone3.documentation.dates import resolve_due_date
from app.services.milestone3.documentation.evidence import source_labels_for
from app.services.milestone3.documentation.extraction import (
    KIND_ACTION,
    KIND_RISK,
    DocumentIndex,
    build_document_index,
    ground_record,
    is_vague_date_expression,
    normalize_space,
    parse_entity_ref,
)
from app.services.milestone3.documentation.risk_register import BlockerEntity

_STATUS_ORDER = {"Blocked": 0, "In Progress": 1, "Not Started": 2, "Pending": 2, "Open": 3,
                 "Completed": 4, "Resolved": 4, "Closed": 4}
_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

ORIGIN_AI = "AI detected"
ORIGIN_MANUAL = "Manual entry"
ORIGIN_BLOCKER = "Blocker resolution"

#: Machine-readable companion to the ``origin`` labels above. ``origin`` is
#: written for people and may be reworded freely; callers that need to branch on
#: it must use this instead of matching on the label text.
ORIGIN_KIND_AI = "ai"
ORIGIN_KIND_MANUAL = "manual"
ORIGIN_KIND_BLOCKER = "blocker"

_ORIGIN_KIND = {
    ORIGIN_AI: ORIGIN_KIND_AI,
    ORIGIN_MANUAL: ORIGIN_KIND_MANUAL,
    ORIGIN_BLOCKER: ORIGIN_KIND_BLOCKER,
}


def origin_kind(origin: str) -> str:
    """The stable kind behind an ``origin`` label.

    >>> origin_kind("Blocker resolution")
    'blocker'
    """
    return _ORIGIN_KIND.get(origin, "")


def _fmt_date(value) -> str:
    from app.services.milestone3.documentation.extraction import format_date

    return format_date(value) or NOT_SPECIFIED


def _fmt_dt(value) -> str:
    if not value:
        return NOT_SPECIFIED
    try:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except AttributeError:
        return str(value)


# --------------------------------------------------------------------------- #
# Relationship linking
# --------------------------------------------------------------------------- #


def _match_words(title: str) -> set[str]:
    from app.services.milestone3.documentation.risk_register import _match_words as shared

    return shared(title)


def _shares_subject(a: str, b: str) -> bool:
    wa, wb = _match_words(a), _match_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) >= 2


def _links_to(title: str, other_title: str, other_kind: str) -> bool:
    """Stored-text relationship between two entities. Never a guess."""
    ref = parse_entity_ref(title)
    if ref and ref.raw.lower() in (other_title or "").lower():
        return True
    other_ref = parse_entity_ref(other_title)
    if other_ref and other_ref.raw.lower() in (title or "").lower():
        return True
    return _shares_subject(title, other_title)


def find_related_risk(title: str, risks: list[tuple[int, str]]) -> str:
    """The risk this action mitigates, or the explicit placeholder.

    ``risks`` is a list of ``(risk_id, title)`` in the register's own numbering.
    """
    best: str | None = None
    best_score = 0
    words = _match_words(title)
    for risk_id, risk_title in risks:
        if parse_entity_ref(title) and parse_entity_ref(title).raw.lower() in risk_title.lower():
            return risk_id
        score = len(words & _match_words(risk_title))
        if score > best_score:
            best, best_score = risk_id, score
    return best if best_score >= 2 else NOT_SPECIFIED


def find_related_blocker(title: str, blockers: list[tuple[str, str]]) -> str:
    """The blocker this action unblocks, or the explicit placeholder."""
    best: str | None = None
    best_score = 0
    words = _match_words(title)
    for blocker_id, blocker_title in blockers:
        ref = parse_entity_ref(title)
        if ref and ref.raw.lower() in blocker_title.lower():
            return blocker_id
        score = len(words & _match_words(blocker_title))
        if score > best_score:
            best, best_score = blocker_id, score
    return best if best_score >= 2 else NOT_SPECIFIED


# --------------------------------------------------------------------------- #
# Entry construction
# --------------------------------------------------------------------------- #


def _task_action_id(task: Task) -> str:
    ref = parse_entity_ref(task.title)
    if ref and ref.kind == KIND_ACTION:
        return ref.canonical
    return f"ACT-{task.id:04d}"


def _task_entry(
    task: Task,
    doc_names: dict[int, str],
    users: dict[int, str],
    index: DocumentIndex,
    risks: list[tuple[int, str]],
    blockers: list[tuple[str, str]],
    action_id: str,
) -> tuple[dict, validation.Validator]:
    v = validation.Validator()
    record = ground_record(index, task.title)
    field_source = record.chunk.document if record else ""

    source_document = doc_names.get(task.source_document_id) if task.source_document_id else ""
    if not source_document and record:
        source_document = record.chunk.document
    grounding = validation.Grounding(
        document_text=index.haystack(),
        structured_text=f"{task.title} {task.description or ''} {task.source_ref or ''}",
    )
    # An owner entered by an administrator is project data in its own right, so the
    # account name joins what the fields are grounded against. Without it the
    # support test rejects every manually assigned owner.
    assigned_name = users.get(task.assigned_to, "") if task.assigned_to else ""
    if assigned_name:
        grounding = validation.Grounding(
            document_text=grounding.document_text,
            structured_text=f"{grounding.structured_text} {assigned_name}",
        )

    action = normalize_space(task.title) or "Untitled action"
    v.record("action", validation.FieldVerdict.SUPPORTED, action, field_source,
             "Action text preserved from the stored task record.")

    description = normalize_space(task.description or "")
    if not description and record:
        description = normalize_space(record.value("resolution_action"))
    description = validation.correct_free_text(v, "description", description, grounding,
                                               source=field_source, allow_unsupported=False)

    owner = users.get(task.assigned_to, "") if task.assigned_to else ""
    if record and record.value("owner"):
        owner = record.value("owner")
    owner = validation.correct_free_text(v, "owner", owner, grounding, source=field_source,
                                         allow_unsupported=False)

    priority = normalize_space(task.priority or "")
    if record and record.value("priority"):
        priority = record.value("priority")
    priority = validation.correct_choice(v, "priority", priority, grounding,
                                         choices=("Critical", "High", "Medium", "Low"),
                                         source=field_source, allow_unsupported=False)

    status = normalize_space(task.status or "")
    if record and record.value("status"):
        status = record.value("status")
    status = validation.correct_choice(v, "status", status, grounding,
                                       choices=("Not Started", "In Progress", "Blocked",
                                                "Completed", "Open", "Resolved", "Closed"),
                                       source=field_source, allow_unsupported=False)

    due = resolve_due_date(
        task.due_date,
        record.due if record else None,
        document_vague=bool(record and is_vague_date_expression(record.chunk.text)),
    )
    validation.record_date_trace(v, "due_date", due.to_dict(), source=field_source)

    related_risk = find_related_risk(task.title, risks)
    validation.record_field(v, "related_risk", related_risk, source=field_source)
    related_blocker = find_related_blocker(task.title, blockers)
    validation.record_field(v, "related_blocker", related_blocker, source=field_source)

    stored_evidence = normalize_space(task.source_ref or "")
    if record and record.evidence():
        evidence = record.evidence()
        validation.correct_evidence(v, "evidence", evidence, index.haystack(),
                                    source=source_document)
    elif stored_evidence and stored_evidence.lower() in index.haystack().lower():
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
    if source_document:
        validation.record_field(v, "source", source_document,
                                note="Source document recorded with the original item.")
    else:
        v.record("source", validation.FieldVerdict.MISSING, NOT_SPECIFIED, "",
                 "The item has no linked source document.")

    entry = {
        "action_id": action_id,
        "action": action,
        "description": description,
        "owner": owner or NOT_SPECIFIED,
        "owner_assigned": bool(owner),
        "priority": priority or NOT_SPECIFIED,
        "due_date": due.effective,
        "due_date_assigned": due.is_specified,
        "status": status or NOT_SPECIFIED,
        "related_risk": related_risk,
        "related_blocker": related_blocker,
        "source_document": source_document or NOT_SPECIFIED,
        "source_evidence": evidence,
        "origin": ORIGIN_AI if task.ai_generated else ORIGIN_MANUAL,
        "origin_kind": ORIGIN_KIND_AI if task.ai_generated else ORIGIN_KIND_MANUAL,
        "origin_detail": task.source_type or "manual",
        "due_date_trace": due.to_dict(),
        "due_date_comparison": due.comparison(),
        "created_at": _fmt_dt(task.created_at),
        "validation": v.summary.to_dict(),
        "field_verdicts": {f.field: f.verdict.value for f in v.summary.findings},
    }
    return entry, v


def _blocker_entry(
    blocker: BlockerEntity,
    doc_names: dict[int, str],
    index: DocumentIndex,
    risks: list[tuple[int, str]],
    blockers: list[tuple[str, str]],
) -> tuple[dict, validation.Validator]:
    """One resolution action per blocker.

    The action text is the resolution the documents actually state
    (``Resolution action=Obtain confirmed delivery date``), not a generic
    "resolve this blocker" label.

    The blocker arrives as a :class:`BlockerEntity` rather than an ORM row, because
    a project may record the same real-world blocker in the ``blockers`` table, in
    the ``risks`` table under a ``B...`` label, or in both. The registry has
    already merged those into one blocker with one identifier.
    """
    v = validation.Validator()
    record = ground_record(index, blocker.title)
    field_source = record.chunk.document if record else ""

    source_document = doc_names.get(blocker.source_document_id) if blocker.source_document_id else ""
    if not source_document and record:
        source_document = record.chunk.document
    grounding = validation.Grounding(
        document_text=index.haystack(),
        structured_text=f"{blocker.title} {blocker.description} {blocker.evidence}",
    )

    # `ACT-BLK-0001` — derived from the blocker's own identifier, so the action
    # always names the blocker it belongs to.
    action_id = f"ACT-{blocker.blocker_id}"
    action = ""
    if record:
        action = normalize_space(record.value("resolution_action"))
    if not action:
        action = normalize_space(blocker.expected_resolution)
    if not action or is_vague_date_expression(action):
        action = f"Resolve blocker: {blocker.title or 'Untitled blocker'}"
    v.record("action", validation.FieldVerdict.SUPPORTED, action, field_source,
             "Resolution action taken from the source document or the blocker record.")

    description = blocker.description
    description = validation.correct_free_text(v, "description", description, grounding,
                                               source=field_source, allow_unsupported=False)

    owner = blocker.owner
    if record and record.value("owner"):
        owner = record.value("owner")
    owner = validation.correct_free_text(v, "owner", owner, grounding, source=field_source,
                                         allow_unsupported=False)

    priority = blocker.severity
    if record and record.value("priority"):
        priority = record.value("priority")
    priority = validation.correct_choice(v, "priority", priority, grounding,
                                         choices=("Critical", "High", "Medium", "Low"),
                                         source=field_source, allow_unsupported=False)

    status = blocker.status
    if record and record.value("status"):
        status = record.value("status")
    status = validation.correct_choice(v, "status", status, grounding,
                                       choices=("Not Started", "In Progress", "Blocked",
                                                "Completed", "Open", "Resolved", "Closed"),
                                       source=field_source, allow_unsupported=False)

    # `expected_resolution` holds the resolution *text*, not a date. It is only
    # treated as an admin-entered date when it actually parses as one.
    admin_due = _admin_due_from_blocker(blocker)
    due = resolve_due_date(
        admin_due,
        record.due if record else None,
        document_vague=bool(record and is_vague_date_expression(record.chunk.text)),
    )
    validation.record_date_trace(v, "due_date", due.to_dict(), source=field_source)

    related_risk = find_related_risk(blocker.title, risks)
    validation.record_field(v, "related_risk", related_risk, source=field_source)
    validation.record_field(v, "related_blocker", blocker.blocker_id, source=field_source)

    stored_evidence = blocker.evidence
    if record and record.evidence():
        evidence = record.evidence()
        validation.correct_evidence(v, "evidence", evidence, index.haystack(),
                                    source=source_document)
    elif stored_evidence and stored_evidence.lower() in index.haystack().lower():
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
    if source_document:
        validation.record_field(v, "source", source_document,
                                note="Source document recorded with the original item.")
    else:
        v.record("source", validation.FieldVerdict.MISSING, NOT_SPECIFIED, "",
                 "The item has no linked source document.")

    entry = {
        "action_id": action_id,
        "action": action,
        "description": description,
        "owner": owner or NOT_SPECIFIED,
        "owner_assigned": bool(owner),
        "priority": priority or NOT_SPECIFIED,
        "due_date": due.effective,
        "due_date_assigned": due.is_specified,
        "status": status or NOT_SPECIFIED,
        "related_risk": related_risk,
        "related_blocker": blocker.blocker_id,
        "related_blocker_ref": blocker.document_ref or NOT_SPECIFIED,
        "source_document": source_document or NOT_SPECIFIED,
        "source_evidence": evidence,
        "origin": ORIGIN_BLOCKER,
        "origin_kind": ORIGIN_KIND_BLOCKER,
        "origin_detail": blocker.source_type or "ai_detected",
        "due_date_trace": due.to_dict(),
        "due_date_comparison": due.comparison(),
        "created_at": _fmt_dt(blocker.created_at),
        "validation": v.summary.to_dict(),
        "field_verdicts": {f.field: f.verdict.value for f in v.summary.findings},
    }
    return entry, v


def _admin_due_from_blocker(blocker: BlockerEntity):
    """The admin-entered date on a blocker, when the field really holds one."""
    from app.services.milestone3.documentation.extraction import parse_explicit_date

    return parse_explicit_date(blocker.expected_resolution)


def _label_free_headline(text: str) -> str:
    """The entity's own words, without its reference or its labelled fields.

    Two shapes appear in this project's documents:

    * prose/labelled — ``A001 - Implement MQTT reconnect handling | Owner: Backend Team``
    * spreadsheet rows — ``item_id=A001 | item_name=Implement MQTT reconnect handling``

    >>> _label_free_headline("A001 - Implement MQTT reconnect handling | Owner: Backend Team")
    'Implement MQTT reconnect handling'
    >>> _label_free_headline("item_id=A001 | item_name=Implement MQTT reconnect handling")
    'Implement MQTT reconnect handling'
    """
    body = (text or "").strip()
    if not body:
        return ""
    head = body.splitlines()[0]

    named = _key_value(head, ("item_name", "name", "title", "task", "risk", "description"))
    if named:
        return normalize_space(named)

    ref = parse_entity_ref(head)
    if ref:
        head = head.replace(ref.raw, " ", 1)
    # Trim the separators that followed the reference before splitting on them,
    # otherwise a leading " - " would consume the whole title.
    head = head.strip().lstrip("-–—. \u2022")
    for delimiter in ("|", ";", " — ", " • ", " - "):
        head = head.split(delimiter)[0]
    return normalize_space(head).strip()


def _key_value(text: str, keys: tuple[str, ...]) -> str:
    """Value of the first ``key=value``/``key: value`` pair present, or ``""``.

    >>> _key_value("item_id=A001 | item_name=Implement MQTT handling", ("item_name",))
    'Implement MQTT handling'
    """
    for key in keys:
        pattern = r"(?:^|[\s|;])" + key + r"\s*[:=]\s*([^|;\n]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _document_action_entry(
    row: Risk,
    doc_names: dict[int, str],
    index: DocumentIndex,
    risks: list[tuple[int, str]],
    blockers: list[tuple[str, str]],
    action_id: str,
) -> tuple[dict, validation.Validator]:
    """An action item the source documents label ``A...`` but Milestone 2 filed
    in the ``risks`` table.

    Nothing is invented: every field comes from the document record, and the row
    itself only supplies the title and its severity as a fallback priority.
    """
    v = validation.Validator()
    record = ground_record(index, row.title)
    field_source = record.chunk.document if record else ""

    source_document = doc_names.get(row.source_document_id) if row.source_document_id else ""
    if not source_document and record:
        source_document = record.chunk.document
    grounding = validation.Grounding(document_text=index.haystack())

    action = normalize_space(row.title)
    if record:
        action = _label_free_headline(record.chunk.text)
    action = action or "Untitled action"
    v.record("action", validation.FieldVerdict.SUPPORTED, action, field_source,
             "Action text taken from the source document.")

    description = validation.correct_free_text(
        v, "description", normalize_space(row.description or ""), grounding,
        source=field_source, allow_unsupported=False)

    owner = validation.correct_free_text(v, "owner", record.value("owner") if record else "",
                                         grounding, source=field_source)
    priority = validation.correct_choice(
        v, "priority", record.value("priority") if record else "", grounding,
        choices=("Critical", "High", "Medium", "Low"), source=field_source)
    if priority == NOT_SPECIFIED:
        priority = normalize_space(row.severity or "") or NOT_SPECIFIED
        v.record("priority", validation.FieldVerdict.SUPPORTED, priority, "",
                 "Priority preserved from the stored record; the document states none.")
    status = validation.correct_choice(
        v, "status", record.value("status") if record else "", grounding,
        choices=("Not Started", "In Progress", "Blocked", "Completed", "Open", "Resolved",
                 "Closed"), source=field_source)

    due = resolve_due_date(
        None, record.due if record else None,
        document_vague=bool(record and is_vague_date_expression(record.chunk.text)),
    )
    validation.record_date_trace(v, "due_date", due.to_dict(), source=field_source)

    validation.record_field(v, "related_risk", find_related_risk(row.title, risks),
                            source=field_source)
    validation.record_field(v, "related_blocker", find_related_blocker(row.title, blockers),
                            source=field_source)

    evidence = validation.correct_evidence(
        v, "evidence", record.evidence() if record else "", index.haystack(),
        source=source_document)
    if source_document:
        validation.record_field(v, "source", source_document,
                                note="Source document recorded with the original item.")
    else:
        v.record("source", validation.FieldVerdict.MISSING, NOT_SPECIFIED, "",
                 "The item has no linked source document.")

    entry = {
        "action_id": action_id,
        "action": action,
        "description": description,
        "owner": owner,
        "owner_assigned": owner != NOT_SPECIFIED,
        "priority": priority,
        "due_date": due.effective,
        "due_date_assigned": due.is_specified,
        "status": status,
        "related_risk": find_related_risk(row.title, risks),
        "related_blocker": find_related_blocker(row.title, blockers),
        "source_document": source_document or NOT_SPECIFIED,
        "source_evidence": evidence,
        "origin": ORIGIN_AI,
        "origin_kind": ORIGIN_KIND_AI,
        "origin_detail": row.source_type or "ai_detected",
        "due_date_trace": due.to_dict(),
        "due_date_comparison": due.comparison(),
        "created_at": _fmt_dt(row.created_at),
        "validation": v.summary.to_dict(),
        "field_verdicts": {f.field: f.verdict.value for f in v.summary.findings},
    }
    return entry, v


# --------------------------------------------------------------------------- #
# List assembly
# --------------------------------------------------------------------------- #


def build_action_items(db, project: Project) -> dict:
    """Assemble the structured action item list for a project."""
    from app.services.milestone3.documentation.evidence import existing_blockers, existing_tasks
    from app.services.milestone3.documentation.risk_register import (
        action_registry,
        blocker_registry,
        build_risk_register,
        document_action_risks,
    )

    doc_names = source_labels_for(db, project.id)
    index = build_document_index(db, project.id)
    tasks = existing_tasks(db, project.id)
    blocker_rows = existing_blockers(db, project.id)

    # Risk register is reused purely for its numbering, so the action list and the
    # register always agree on which risk is R00x.
    register = build_risk_register(db, project)
    risk_numbering = [(e["risk_id"], e["title"]) for e in register["entries"]]

    # One registry per entity kind, shared with the risk register. This is what
    # gives every blocker and every action exactly one identifier across both
    # generated documents.
    blockers = blocker_registry(db, project, index)
    blocker_numbering = [(entity.blocker_id, entity.title) for entity in blockers]
    actions = action_registry(db, project, index)

    tasks_by_id = {t.id: t for t in tasks}
    document_action_rows = {
        row.id: row
        for row in document_action_risks(db, project.id, index)
    }

    user_ids = {t.assigned_to for t in tasks if t.assigned_to}
    users: dict[int, str] = {}
    if user_ids:
        for uid, name in db.query(User.id, User.name).filter(User.id.in_(user_ids)).all():
            users[uid] = name

    entries: list[dict] = []
    validators: list[validation.Validator] = []
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []

    def add(entry: dict, validator: validation.Validator) -> None:
        action_id = entry["action_id"]
        if action_id in seen_ids:
            # The registries make identifiers unique, so reaching this would be a
            # bug rather than an expected duplicate. It is reported, never dropped
            # silently.
            duplicate_ids.append(action_id)
            return
        seen_ids.add(action_id)
        entries.append(entry)
        validators.append(validator)

    for entity in actions:
        if entity.is_task:
            row = tasks_by_id.get(entity.row_id)
            if row is None:
                continue
            entry, v = _task_entry(row, doc_names, users, index, risk_numbering,
                                   blocker_numbering, entity.action_id)
        else:
            row = document_action_rows.get(entity.row_id)
            if row is None:
                continue
            entry, v = _document_action_entry(row, doc_names, index, risk_numbering,
                                              blocker_numbering, entity.action_id)
        add(entry, v)

    for entity in blockers:
        entry, v = _blocker_entry(entity, doc_names, index, risk_numbering, blocker_numbering)
        add(entry, v)

    entries.sort(
        key=lambda e: (
            _PRIORITY_ORDER.get((e["priority"] or "").upper(), 9),
            _STATUS_ORDER.get(e["status"], 9),
            e["action_id"],
        )
    )

    open_entries = [e for e in entries if e["status"] in ("Pending", "In Progress", "Blocked",
                                                          "Open", "Not Started")]
    unassigned = [e for e in entries if not e["owner_assigned"]]
    undated = [e for e in open_entries if not e["due_date_assigned"]]
    conflicts = [e for e in entries if e["due_date_trace"].get("conflict")]

    combined = validation.merge_validators(validators)
    summary = combined.finish()

    counts_by_status: dict[str, int] = {}
    for e in entries:
        counts_by_status[e["status"]] = counts_by_status.get(e["status"], 0) + 1

    if duplicate_ids:
        combined.note(
            "Duplicate action identifiers were produced and the repeats were excluded: "
            + ", ".join(sorted(set(duplicate_ids)))
        )

    document_blockers = [entity for entity in blockers if not entity.has_blocker_row]

    return {
        "doc_type": "action_items",
        "title": "Project Action Items",
        "project": project.name,
        "project_id": project.id,
        "total": len(entries),
        "open": len(open_entries),
        "from_tasks": len(tasks),
        "from_blockers": len(blocker_rows),
        "from_document_blockers": len(document_blockers),
        "from_document_actions": len(document_action_rows),
        "ai_generated": sum(1 for e in entries if e["origin"] == ORIGIN_AI),
        "unassigned_count": len(unassigned),
        "missing_due_date_count": len(undated),
        "date_conflict_count": len(conflicts),
        "counts_by_status": counts_by_status,
        "entries": entries,
        "insufficient_evidence": not entries,
        "reason": "" if entries else NOT_SPECIFIED,
        "validation": summary.to_dict(),
        "validation_notes": combined.notes,
        "notes": [
            "Every task row produces exactly one entry, so the list cannot duplicate work "
            "items. Action items the source documents label 'A...' are included from the "
            "records that already exist, and each unresolved blocker contributes exactly one "
            "resolution action.",
            "Blockers and action items the Risk Detection filed under the "
            "wrong entity type are reached through the records that already exist. They are "
            "never duplicated and never deleted.",
            "Identifiers are per-project sequences (BLK-0001, ACT-0001) or the source "
            "document's own label (A001), so they are stable across regenerations and "
            "identical in the risk register.",
            "Owner, priority and status are read from the source document first and from the "
            "stored record second. Neither is ever inferred.",
            "Due date follows the document-first precedence rule: an explicit date in a source "
            "document overrides a conflicting administrator-entered date, and both values are "
            "kept for traceability.",
            f"Fields absent from both the record and the documents read '{NOT_SPECIFIED}'.",
        ],
    }


__all__ = [
    "ORIGIN_AI",
    "ORIGIN_BLOCKER",
    "ORIGIN_MANUAL",
    "ORIGIN_KIND_AI",
    "ORIGIN_KIND_BLOCKER",
    "ORIGIN_KIND_MANUAL",
    "build_action_items",
    "find_related_blocker",
    "find_related_risk",
    "origin_kind",
]
