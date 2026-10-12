"""Evidence validation (Section 19, Step 3).

Two guarantees are locked in here:

1. The agent pipeline only verifies a record whose evidence is a real span of the
   retrieved chunks and whose cited document resolves to a project upload.
   Anything else is dropped rather than stored as an unverified AI claim.
2. Document attribution is checked against the project's *file names*, not the
   document body (a file name is not a span of the text it names).

No provider or database is contacted: the helpers are pure and are driven with
plain stand-in objects.
"""

from types import SimpleNamespace

from app.services.milestone3.documentation import validator as validation
from app.services.agents.orchestrator import (
    _evidence_supported,
    _verified_source,
)

CHUNKS = [
    {
        "document_id": 7,
        "original_name": "spec.txt",
        "text": "The MQTT gateway may be unreliable under load.\nCloud budget approval is outstanding.",
    }
]
HAYSTACK = "\n".join(c["text"] for c in CHUNKS)
DOC_MAP = {"spec.txt": 7}


def _item(evidence="", source_document=""):
    return SimpleNamespace(evidence=evidence, source_document=source_document)


# --------------------------------------------------------------------------- #
# evidence must be traceable to the retrieved text
# --------------------------------------------------------------------------- #


def test_verbatim_quote_is_supported():
    assert _evidence_supported("Cloud budget approval is outstanding.", HAYSTACK)


def test_quote_with_no_shared_vocabulary_is_rejected():
    assert not _evidence_supported(
        "Quoted from the uploaded project document.", HAYSTACK
    )


def test_empty_evidence_is_rejected():
    assert not _evidence_supported("", HAYSTACK)
    assert not _evidence_supported("   ", HAYSTACK)


# --------------------------------------------------------------------------- #
# a record is verified only with traceable evidence and a known document
# --------------------------------------------------------------------------- #


def test_verified_record_keeps_its_cited_document():
    verified, source_id = _verified_source(
        _item("Cloud budget approval is outstanding.", "spec.txt"),
        CHUNKS,
        DOC_MAP,
        HAYSTACK,
    )
    assert verified is True
    assert source_id == 7


def test_record_with_untraceable_evidence_is_dropped():
    verified, source_id = _verified_source(
        _item("A completely invented claim about llama farming.", "spec.txt"),
        CHUNKS,
        DOC_MAP,
        HAYSTACK,
    )
    assert verified is False
    assert source_id is None


def test_record_citing_an_unknown_document_is_dropped():
    verified, source_id = _verified_source(
        _item("Cloud budget approval is outstanding.", "missing.pdf"),
        CHUNKS,
        DOC_MAP,
        HAYSTACK,
    )
    assert verified is False
    assert source_id is None


def test_record_without_a_citation_is_linked_from_the_evidence_location():
    verified, source_id = _verified_source(
        _item("Cloud budget approval is outstanding.", ""),
        CHUNKS,
        DOC_MAP,
        HAYSTACK,
    )
    assert verified is True
    assert source_id == 7


# --------------------------------------------------------------------------- #
# file-name attribution (filename-vs-chunk-text bug)
# --------------------------------------------------------------------------- #


def _story(source_document: str) -> dict:
    return {
        "as_a": "admin",
        "i_want": "unreliable gateway handled",
        "so_that": "readings are not lost",
        "priority": "High",
        "status": "Proposed",
        "acceptance_criteria": [],
        "related_requirement": "",
        "source_document": source_document,
        "source_evidence": "The MQTT gateway may be unreliable under load.",
    }


def test_grounding_knows_a_file_name_that_is_not_in_the_body():
    grounding = validation.Grounding(
        document_text="the body never spells out the file name",
        document_names=["spec.txt"],
    )
    assert grounding.knows_document("spec.txt")
    assert grounding.knows_document("uploads/3/spec.txt")
    assert not grounding.knows_document("other.pdf")


def test_story_attribution_is_validated_against_document_names():
    grounding = validation.Grounding(
        document_text="The MQTT gateway may be unreliable under load.",
        document_names=["spec.txt"],
    )
    story, v = validation.validate_story(_story("spec.txt"), grounding)
    assert story["source_document"] == "spec.txt"
    verdicts = {(f.field, f.verdict) for f in v.summary.findings}
    assert ("source_document", validation.FieldVerdict.SUPPORTED) in verdicts


def test_story_attribution_to_an_unknown_document_is_removed():
    grounding = validation.Grounding(
        document_text="The MQTT gateway may be unreliable under load.",
        document_names=["spec.txt"],
    )
    story, v = validation.validate_story(_story("invented.pdf"), grounding)
    assert story["source_document"] == validation.NOT_SPECIFIED
    verdicts = {(f.field, f.verdict) for f in v.summary.findings}
    assert ("source_document", validation.FieldVerdict.UNSUPPORTED) in verdicts
