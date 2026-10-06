"""Shared vocabulary for Milestone 3.

The single most important rule in this module: when the project data does not
contain a fact, the generator must say so. These placeholders are the only
substitutions permitted in a generated document, and they are what the UI
renders when a field cannot be sourced.
"""

from __future__ import annotations

# --- Evidence insufficiency (user stories / deliverables) -------------------
INSUFFICIENT_EVIDENCE = "Insufficient evidence in uploaded project documents."

# --- Missing structured field (risk register / action items) ----------------
NOT_SPECIFIED = "Not specified in project data."

# --- Document types produced by the Documentation Generation Agent ----------
DOC_USER_STORIES = "user_stories"
DOC_RISK_REGISTER = "risk_register"
DOC_ACTION_ITEMS = "action_items"

DOC_TYPES = (DOC_USER_STORIES, DOC_RISK_REGISTER, DOC_ACTION_ITEMS)

DOC_TITLES = {
    DOC_USER_STORIES: "User Stories",
    DOC_RISK_REGISTER: "Risk Register",
    DOC_ACTION_ITEMS: "Action Item List",
}

DOC_SLUGS = {
    DOC_USER_STORIES: "user-stories",
    DOC_RISK_REGISTER: "risk-register",
    DOC_ACTION_ITEMS: "action-items",
}


def doc_file_name(project_name: str, doc_type: str, extension: str = "md") -> str:
    """A stable, safe download name derived from the real project name."""
    slug = DOC_SLUGS.get(doc_type, doc_type)
    base = "".join(ch if (ch.isalnum() or ch in "-_ ") else " " for ch in (project_name or "project"))
    base = "_".join(base.split())[:80].strip("_") or "project"
    return f"{base}_{slug}.{extension.lstrip('.') or 'md'}"


# --- Severity / probability / impact ladders --------------------------------
# These are deterministic lookup tables used by the health scorer and the risk
# register. They are shared so both modules agree on the mapping.

SEVERITY_WEIGHT = {"CRITICAL": 10, "HIGH": 6, "MEDIUM": 3, "LOW": 1}
PROBABILITY_WEIGHT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
IMPACT_WEIGHT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

SCHEDULE_STATUS_SCORE = {
    "ON TRACK": 100.0,
    "MINOR RISK": 72.0,
    "SIGNIFICANT RISK": 45.0,
    "AT RISK": 22.0,
}


def level(value: str | None) -> str:
    """Normalize a free-form level to one of the known tokens."""
    u = (value or "").strip().upper()
    if u in SEVERITY_WEIGHT:
        return u
    if u in PROBABILITY_WEIGHT:
        return u
    return "MEDIUM"


def severity_score(severity: str | None, probability: str | None, impact: str | None) -> int:
    """Deterministic 1-25 risk score = severity_weight x probability x impact,
    clamped. The LLM never produces this number."""
    s = SEVERITY_WEIGHT.get(level(severity), 3)
    p = PROBABILITY_WEIGHT.get(level(probability), 2)
    i = IMPACT_WEIGHT.get(level(impact), 2)
    return max(1, min(25, s * p * i))


def risk_band(score: int) -> str:
    if score >= 20:
        return "Critical"
    if score >= 12:
        return "High"
    if score >= 6:
        return "Medium"
    return "Low"
