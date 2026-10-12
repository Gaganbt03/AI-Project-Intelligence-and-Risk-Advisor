"""Strict, document-grounded extraction contract (Section 19, Step 2).

These tests exercise the offline parsing/validation layer directly: no provider
is contacted. They lock in that the model can never fabricate a level, that a
refusal is rejected instead of silently becoming an all-defaults result, and
that an explicit empty list stays valid.
"""

from app.services.ai.repair import parse_and_validate
from app.services.ai.schemas import ActionItemAgentResult, RiskAgentResult


def test_refusal_is_rejected_not_validated_as_empty():
    assert parse_and_validate('{"error": "I cannot help with that."}', RiskAgentResult) is None


def test_non_json_refusal_is_rejected():
    assert parse_and_validate("I'm sorry, I can't do that.", RiskAgentResult) is None


def test_explicit_empty_list_is_still_valid():
    result = parse_and_validate('{"risks": []}', RiskAgentResult)
    assert result is not None
    assert result.risks == []


def test_unknown_levels_are_preserved_as_missing_not_guessed():
    result = parse_and_validate(
        '{"risks": [{"title": "r", "severity": "Very High", '
        '"probability": "Critical", "impact": "high"}]}',
        RiskAgentResult,
    )
    risk = result.risks[0]
    assert risk.severity == ""          # "Very High" is not a valid level -> not guessed as Medium
    assert risk.probability == ""       # "Critical" is invalid for probability -> not guessed
    assert risk.impact == "HIGH"        # a valid level is normalised, never defaulted


def test_bare_risk_object_is_wrapped_into_the_list():
    result = parse_and_validate('{"title": "bare", "severity": "High"}', RiskAgentResult)
    assert result is not None
    assert result.risks[0].title == "bare"


def test_action_item_evidence_field_is_accepted():
    result = parse_and_validate(
        '{"action_items": [{"action": "Do it", "evidence": "verbatim quote"}]}',
        ActionItemAgentResult,
    )
    assert result is not None
    assert result.action_items[0].evidence == "verbatim quote"
