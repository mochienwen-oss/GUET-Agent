# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from agent.evidence_contract import (  # noqa: E402
    ClaimVerification,
    EvidenceContractError,
    claim_from_dict,
    evidence_span_from_dict,
    verification_from_dict,
)


def claim_row(**updates):
    row = {
        "claim_id": "claim-1",
        "claim_type": "numeric",
        "subject": "毕业要求学分",
        "predicate": "达到",
        "object": None,
        "operator": ">=",
        "value": 80,
        "unit": "分",
        "scope": ["本科生"],
        "required": True,
    }
    row.update(updates)
    return row


def span_row(**updates):
    row = {
        "document_id": "doc-1",
        "chunk_id": "doc-1:3",
        "source": "培养方案.md",
        "heading_path": ["毕业要求"],
        "quote": "毕业要求学分为80分",
        "start": 2,
        "end": 12,
        "evidence_type": "table",
        "relation": "supports",
    }
    row.update(updates)
    return row


def test_claim_round_trip_is_strict_and_lossless():
    claim = claim_from_dict(claim_row())
    assert claim.to_dict() == claim_row()
    assert claim.scope == ("本科生",)


@pytest.mark.parametrize("row, message", [
    ({**claim_row(), "unexpected": True}, "unknown fields"),
    ({key: value for key, value in claim_row().items() if key != "subject"},
     "missing fields"),
    (claim_row(claim_type="other"), "unknown claim_type"),
    (claim_row(scope="本科生"), "scope must be a list"),
])
def test_claim_rejects_unknown_missing_or_invalid_fields(row, message):
    with pytest.raises(EvidenceContractError, match=message):
        claim_from_dict(row)


def test_span_must_match_declared_source_offsets():
    text = "前言毕业要求学分为80分后文"
    span = evidence_span_from_dict(span_row(), source_text=text)
    assert span.quote == text[span.start:span.end]
    assert span.to_dict() == span_row()


@pytest.mark.parametrize("updates, message", [
    ({"start": 3}, "offsets do not match quote length"),
    ({"end": 2}, "invalid evidence offsets"),
    ({"evidence_type": "image"}, "unknown evidence_type"),
    ({"relation": "maybe"}, "unknown evidence relation"),
])
def test_span_rejects_invalid_shape(updates, message):
    with pytest.raises(EvidenceContractError, match=message):
        evidence_span_from_dict(span_row(**updates))


def test_span_rejects_quote_that_is_not_at_declared_offsets():
    with pytest.raises(EvidenceContractError, match="does not match source text"):
        evidence_span_from_dict(span_row(), source_text="前言完全不同的正文后文")


def test_supported_and_contradicted_verifications_require_matching_spans():
    supported = verification_from_dict({
        "claim_id": "claim-1",
        "status": "supported",
        "reason": "exact_table_fact",
        "evidence_spans": [span_row()],
        "verifier_version": "evidence-verifier/2",
    })
    assert isinstance(supported, ClaimVerification)
    assert supported.to_dict()["evidence_spans"] == [span_row()]

    with pytest.raises(EvidenceContractError, match="requires evidence spans"):
        verification_from_dict({
            "claim_id": "claim-1",
            "status": "supported",
            "reason": "missing",
            "evidence_spans": [],
            "verifier_version": "evidence-verifier/2",
        })

    with pytest.raises(EvidenceContractError, match="requires contradicts spans"):
        verification_from_dict({
            "claim_id": "claim-1",
            "status": "contradicted",
            "reason": "opposite_rule",
            "evidence_spans": [span_row()],
            "verifier_version": "evidence-verifier/2",
        })


def test_insufficient_verification_may_have_no_span_but_is_still_versioned():
    result = verification_from_dict({
        "claim_id": "claim-1",
        "status": "insufficient",
        "reason": "requested_count_absent",
        "evidence_spans": [],
        "verifier_version": "evidence-verifier/2",
    })
    assert result.status == "insufficient"
    assert result.evidence_spans == ()
