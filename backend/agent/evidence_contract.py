# -*- coding: utf-8 -*-
"""Strict claim and evidence-span contracts for the v2 evidence path.

This module is deliberately independent from retrieval, models, and runtime
state.  It describes reviewed facts; it does not decide whether a document is
relevant or whether an answer may be shown to a user.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping


ClaimType = Literal[
    "numeric", "range", "condition", "procedure", "scope", "negation",
    "definition", "calculation",
]
EvidenceType = Literal["text", "table", "adjacent", "cross_clause"]
EvidenceRelation = Literal["supports", "contradicts", "insufficient"]
VerificationStatus = Literal["supported", "contradicted", "insufficient"]

CLAIM_TYPES = frozenset({
    "numeric", "range", "condition", "procedure", "scope", "negation",
    "definition", "calculation",
})
EVIDENCE_TYPES = frozenset({"text", "table", "adjacent", "cross_clause"})
EVIDENCE_RELATIONS = frozenset({"supports", "contradicts", "insufficient"})
VERIFICATION_STATUSES = frozenset({"supported", "contradicted", "insufficient"})


class EvidenceContractError(ValueError):
    """The serialized evidence contract is malformed or internally false."""


def _strict_fields(data: Mapping[str, Any], required: set[str], *, kind: str) -> None:
    if not isinstance(data, Mapping):
        raise EvidenceContractError(f"{kind} must be an object")
    actual = set(data)
    missing = required - actual
    extra = actual - required
    if missing:
        raise EvidenceContractError(f"{kind} missing fields: {sorted(missing)}")
    if extra:
        raise EvidenceContractError(f"{kind} unknown fields: {sorted(extra)}")


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ClaimSpec:
    claim_id: str
    claim_type: ClaimType
    subject: str
    predicate: str
    object: str | None
    operator: str | None
    value: str | int | float | None
    unit: str | None
    scope: tuple[str, ...]
    required: bool

    def __post_init__(self) -> None:
        _nonempty(self.claim_id, "claim_id")
        if self.claim_type not in CLAIM_TYPES:
            raise EvidenceContractError(f"unknown claim_type: {self.claim_type!r}")
        _nonempty(self.subject, "subject")
        _nonempty(self.predicate, "predicate")
        if not isinstance(self.scope, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in self.scope):
            raise EvidenceContractError("scope must contain non-empty strings")
        if not isinstance(self.required, bool):
            raise EvidenceContractError("required must be boolean")

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["scope"] = list(self.scope)
        return row


@dataclass(frozen=True)
class EvidenceSpan:
    document_id: str
    chunk_id: str
    source: str
    heading_path: tuple[str, ...]
    quote: str
    start: int
    end: int
    evidence_type: EvidenceType
    relation: EvidenceRelation

    def __post_init__(self) -> None:
        _nonempty(self.document_id, "document_id")
        _nonempty(self.chunk_id, "chunk_id")
        _nonempty(self.source, "source")
        _nonempty(self.quote, "quote")
        if not isinstance(self.heading_path, tuple) or any(
                not isinstance(item, str) or not item.strip()
                for item in self.heading_path):
            raise EvidenceContractError(
                "heading_path must contain non-empty strings")
        if not isinstance(self.start, int) or isinstance(self.start, bool):
            raise EvidenceContractError("start must be an integer")
        if not isinstance(self.end, int) or isinstance(self.end, bool):
            raise EvidenceContractError("end must be an integer")
        if self.start < 0 or self.end <= self.start:
            raise EvidenceContractError("invalid evidence offsets")
        if self.end - self.start != len(self.quote):
            raise EvidenceContractError("evidence offsets do not match quote length")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise EvidenceContractError(
                f"unknown evidence_type: {self.evidence_type!r}")
        if self.relation not in EVIDENCE_RELATIONS:
            raise EvidenceContractError(
                f"unknown evidence relation: {self.relation!r}")

    def validate_source_text(self, source_text: str) -> None:
        if not isinstance(source_text, str):
            raise EvidenceContractError("source_text must be a string")
        if self.end > len(source_text) or \
                source_text[self.start:self.end] != self.quote:
            raise EvidenceContractError(
                "evidence quote does not match source text at declared offsets")

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["heading_path"] = list(self.heading_path)
        return row


@dataclass(frozen=True)
class ClaimVerification:
    claim_id: str
    status: VerificationStatus
    reason: str
    evidence_spans: tuple[EvidenceSpan, ...]
    verifier_version: str

    def __post_init__(self) -> None:
        _nonempty(self.claim_id, "claim_id")
        if self.status not in VERIFICATION_STATUSES:
            raise EvidenceContractError(
                f"unknown verification status: {self.status!r}")
        _nonempty(self.reason, "reason")
        _nonempty(self.verifier_version, "verifier_version")
        if not isinstance(self.evidence_spans, tuple):
            raise EvidenceContractError("evidence_spans must be a tuple")
        if self.status in {"supported", "contradicted"} and not self.evidence_spans:
            raise EvidenceContractError(
                f"{self.status} verification requires evidence spans")
        expected_relation = {
            "supported": "supports",
            "contradicted": "contradicts",
        }.get(self.status)
        if expected_relation and any(
                span.relation != expected_relation for span in self.evidence_spans):
            raise EvidenceContractError(
                f"{self.status} verification requires {expected_relation} spans")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status,
            "reason": self.reason,
            "evidence_spans": [span.to_dict() for span in self.evidence_spans],
            "verifier_version": self.verifier_version,
        }


_CLAIM_FIELDS = {
    "claim_id", "claim_type", "subject", "predicate", "object", "operator",
    "value", "unit", "scope", "required",
}
_SPAN_FIELDS = {
    "document_id", "chunk_id", "source", "heading_path", "quote", "start",
    "end", "evidence_type", "relation",
}
_VERIFICATION_FIELDS = {
    "claim_id", "status", "reason", "evidence_spans", "verifier_version",
}


def claim_from_dict(data: Mapping[str, Any]) -> ClaimSpec:
    _strict_fields(data, _CLAIM_FIELDS, kind="claim")
    scope = data["scope"]
    if not isinstance(scope, list):
        raise EvidenceContractError("claim scope must be a list")
    return ClaimSpec(
        claim_id=data["claim_id"], claim_type=data["claim_type"],
        subject=data["subject"], predicate=data["predicate"],
        object=data["object"], operator=data["operator"], value=data["value"],
        unit=data["unit"], scope=tuple(scope), required=data["required"],
    )


def evidence_span_from_dict(data: Mapping[str, Any], *,
                            source_text: str | None = None) -> EvidenceSpan:
    _strict_fields(data, _SPAN_FIELDS, kind="evidence_span")
    heading_path = data["heading_path"]
    if not isinstance(heading_path, list):
        raise EvidenceContractError("heading_path must be a list")
    span = EvidenceSpan(
        document_id=str(data["document_id"]), chunk_id=str(data["chunk_id"]),
        source=data["source"], heading_path=tuple(heading_path),
        quote=data["quote"], start=data["start"], end=data["end"],
        evidence_type=data["evidence_type"], relation=data["relation"],
    )
    if source_text is not None:
        span.validate_source_text(source_text)
    return span


def verification_from_dict(data: Mapping[str, Any]) -> ClaimVerification:
    _strict_fields(data, _VERIFICATION_FIELDS, kind="claim_verification")
    spans = data["evidence_spans"]
    if not isinstance(spans, list):
        raise EvidenceContractError("evidence_spans must be a list")
    return ClaimVerification(
        claim_id=data["claim_id"], status=data["status"], reason=data["reason"],
        evidence_spans=tuple(evidence_span_from_dict(span) for span in spans),
        verifier_version=data["verifier_version"],
    )
