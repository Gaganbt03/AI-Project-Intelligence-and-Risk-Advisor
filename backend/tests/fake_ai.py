"""Deterministic offline stand-ins for the AI and embedding providers.

The pipeline tests must exercise the *real* orchestrator, health scorer,
documentation generator and due-date resolver, but must never reach a live
provider: a 9-stage pipeline per upload makes the suite take minutes and
depends on network availability.

So this module provides two things:

``fake_generate``
    Replaces ``ProviderManager.generate``. It dispatches on the agent's own
    prompt prefix (the same string each agent's ``*_prompt()`` builder emits) and
    returns JSON that satisfies the agent's real Pydantic schema. The production
    parsing, validation, retry and persistence paths are untouched.

``FakeEmbeddingProvider``
    Replaces the embedding provider so ingestion and RAG run entirely in
    process. Vectors are derived from the text with a stable, seeded hash, so
    identical text always produces identical vectors and results are
    reproducible.

Nothing here is imported by production code.
"""

from __future__ import annotations

import hashlib
import json
import math

from app.services.embeddings import EmbeddingProvider

#: Kept small so the in-memory vector maths in the test run stays instant.
EMBEDDING_DIMS = 64

FAKE_PROVIDER = "fake-test-provider"
FAKE_MODEL = "fake-test-model"

_EVIDENCE = "Quoted from the uploaded project document."


class FakeEmbeddingProvider(EmbeddingProvider):
    """Offline embedder: a deterministic bag-of-characters projection."""

    model = "fake-embedding-model"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    @staticmethod
    def _embed(text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIMS
        lowered = (text or "").lower()
        for token in lowered.split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMS
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


# --------------------------------------------------------------------------- #
# Agent payloads — schema-valid and grounded in the SPEC text the tests upload
# --------------------------------------------------------------------------- #

_SCOPE_RESULT = {
    "project_goal": "deliver the Smart Campus IoT platform",
    "scope": ["sensor gateway firmware", "mobile application"],
    "out_of_scope": [],
    "deliverables": ["gateway firmware v1", "mobile app v1"],
    "milestones": [
        {
            "name": "Sensor datasheet finalised",
            "date": "2026-10-15",
            "evidence": {
                "document": "spec.txt",
                "page": None,
                "section": None,
                "quote": "Priya must finalise the sensor datasheet by 2026-10-15.",
            },
        }
    ],
    "timeline": ["due 2026-12-31"],
    "responsibilities": [],
    "technologies": ["MQTT"],
    "requirements": [],
}

#: The first risk deliberately has no owner anywhere in the source document.
#: That is what keeps ``test_unstated_owner_is_not_filled_in`` meaningful.
_RISK_RESULT = {
    "risks": [
        {
            "title": "MQTT gateway reliability",
            "description": "The MQTT gateway may be unreliable under load.",
            "severity": "High",
            "probability": "Medium",
            "impact": "High",
            "evidence": _EVIDENCE,
            "source_document": "spec.txt",
            "source_page": None,
            "source_section": None,
            "recommended_action": "Load test the gateway before release.",
        },
        {
            "title": "Sensor battery life",
            "description": "Sensor battery life may be insufficient.",
            "severity": "Medium",
            "probability": "Medium",
            "impact": "Medium",
            "evidence": _EVIDENCE,
            "source_document": "spec.txt",
            "source_page": None,
            "source_section": None,
            "recommended_action": "Measure battery drain in the field.",
        },
    ]
}

_FORECAST_RESULT = {
    "current_status": "Scope is defined and two risks are documented.",
    "schedule_status": "Minor Risk",
    "expected_delivery": "2026-12-31",
    "risk_level": "Medium",
    "factors": [
        {"factor": "Outstanding cloud budget approval", "impact": "May delay delivery", "severity": "Medium"}
    ],
    "evidence": [
        {"document": "spec.txt", "page": None, "section": None, "quote": "Cloud budget approval is outstanding."}
    ],
    "caveat": "Forecast is an AI analysis, not a precise prediction.",
}

_BLOCKER_RESULT = {
    "blockers": [
        {
            "title": "Cloud budget approval outstanding",
            "description": "Cloud budget approval is outstanding.",
            "severity": "High",
            "owner": "",
            "evidence": _EVIDENCE,
            "source_document": "spec.txt",
            "source_page": None,
            "source_section": None,
            "recommended_action": "Escalate the budget approval.",
        }
    ]
}

_ACTION_RESULT = {
    "action_items": [
        {
            "action": "Finalise the sensor datasheet",
            "assigned_person": "Priya",
            "deadline": "2026-10-15",
            "priority": "High",
            "source_document": "spec.txt",
            "source_page": None,
            "source_section": None,
        },
        {
            "action": "Publish the API contract",
            "assigned_person": "",
            "deadline": None,
            "priority": "Medium",
            "source_document": "spec.txt",
            "source_page": None,
            "source_section": None,
        },
    ]
}

_ASSISTANT_REPLY = (
    "This project delivers the Smart Campus IoT platform. "
    "The uploaded document sets the project due date as 2026-12-31."
)

#: Each agent's prompt builder starts with a distinctive sentence. Matching on
#: it keeps the fake honest: if a prompt is refactored, the fake stops matching
#: and the test fails loudly instead of silently returning the wrong payload.
_DISPATCH: tuple[tuple[str, dict], ...] = (
    ("Extract scope and deliverables from the context.", _SCOPE_RESULT),
    ("Identify project risks from the context.", _RISK_RESULT),
    ("Produce an initial delivery forecast", _FORECAST_RESULT),
    ("Identify blockers from the context", _BLOCKER_RESULT),
    ("Extract action items / to-dos from the context.", _ACTION_RESULT),
)


def fake_generate(
    self,
    prompt: str,
    system: str | None = None,
    temperature: float | None = None,
    json_mode: bool = False,
) -> tuple[str, str, str]:
    """Drop-in replacement for ``ProviderManager.generate``.

    Agents get schema-valid JSON; the conversational assistant gets prose,
    matching how each caller uses the return value.
    """
    for prefix, payload in _DISPATCH:
        if prompt.startswith(prefix):
            return json.dumps(payload), FAKE_PROVIDER, FAKE_MODEL

    # Anything else is the assistant: it wants prose, not JSON.
    return _ASSISTANT_REPLY, FAKE_PROVIDER, FAKE_MODEL
