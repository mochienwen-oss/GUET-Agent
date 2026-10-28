# -*- coding: utf-8 -*-
"""Read-only retrieval trace used for diagnosis, never for ranking decisions."""
from dataclasses import dataclass, field
from uuid import uuid4


GATE_STATUSES = frozenset({"supported", "ambiguous", "uncertain", "irrelevant", "empty"})


def _candidate(row: dict, rank: int) -> dict:
    doc_id = row.get("document_id")
    chunk_idx = row.get("chunk_idx")
    return {
        "rank": rank,
        "doc_id": str(doc_id) if doc_id is not None else None,
        "chunk_id": (f"{doc_id}:{chunk_idx}"
                     if doc_id is not None and chunk_idx is not None else None),
        "title": row.get("source") or "",
        "heading_path": list(row.get("heading_path") or []),
        "score": row.get("score"),
        "rrf": row.get("rrf"),
        "rerank_score": row.get("rerank_score"),
        **{k: row[k] for k in ("rerank_logit", "rerank_score_version", "chunking_version") if k in row},
        **({"physical_chunk_id": row["chunk_id"]} if "chunk_id" in row else {}),
        **({"evidence_unit": row["evidence_unit"]} if "evidence_unit" in row else {}),
        **({"table_context": row["table_context"]} if "table_context" in row else {}),
        "text_preview": (row.get("text") or row.get("snippet") or "")[:240],
    }


@dataclass
class RetrievalTrace:
    """One retrieval call's observable stages.

    The object is deliberately append-only from the retrieval pipeline's point of
    view. Nothing in this class can reorder, filter, or mutate candidate rows.
    """

    trace_id: str
    original_query: str
    effective_query: str
    sparse_query: str = ""
    keywords: list[str] = field(default_factory=list)
    focus_document_id: str | None = None
    normalization_steps: list[str] = field(default_factory=list)
    stages: dict[str, list[dict]] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    correction: dict | None = None
    recovery: dict | None = None
    degradations: list[dict] = field(default_factory=list)
    table_selection: dict | None = None
    evidence_units: dict | None = None

    @classmethod
    def start(cls, original_query: str, trace_id: str | None = None):
        query = original_query or ""
        return cls(trace_id=trace_id or uuid4().hex,
                   original_query=query, effective_query=query)

    def set_effective_query(self, query: str, step: str | None = None) -> None:
        self.effective_query = query
        if step and step not in self.normalization_steps:
            self.normalization_steps.append(step)

    def set_query_plan(self, sparse_query: str, keywords: list[str]) -> None:
        self.sparse_query = sparse_query
        self.keywords = list(keywords)

    def set_focus_document_id(self, value) -> None:
        self.focus_document_id = str(value) if value is not None else None

    def record_candidates(self, stage: str, rows: list[dict]) -> None:
        self.stages[stage] = [_candidate(row, rank)
                              for rank, row in enumerate(rows or [], 1)]

    def record_timing(self, stage: str, elapsed_ms: float) -> None:
        self.timings_ms[stage] = round(max(0.0, float(elapsed_ms)), 3)

    def record_gate(self, status: str, reason: str,
                    best_score: float | None = None, *,
                    policy_version: str | None = None,
                    signals: dict | None = None,
                    contributing_signals: list[str] | None = None,
                    retry_allowed: bool | None = None,
                    evidence_exposable: bool | None = None) -> None:
        if status not in GATE_STATUSES:
            raise ValueError("unknown retrieval gate status: %r" % status)
        self.gate = {"status": status, "reason": reason,
                     "best_score": best_score}
        # Additive telemetry. It never enters evidence binding or candidate rows.
        if policy_version is not None:
            self.gate["policy_version"] = policy_version
        if signals is not None:
            self.gate["signals"] = signals
        if contributing_signals is not None:
            self.gate["contributing_signals"] = list(contributing_signals)
        if retry_allowed is not None:
            self.gate["retry_allowed"] = bool(retry_allowed)
        if evidence_exposable is not None:
            self.gate["evidence_exposable"] = bool(evidence_exposable)

    def record_correction(self, first_decision, corrective) -> None:
        """Record the single bounded correction; never mutates candidates."""
        first = (first_decision.to_dict() if hasattr(first_decision, "to_dict")
                 else dict(first_decision or {}))
        second = (corrective.to_dict() if hasattr(corrective, "to_dict")
                  else dict(corrective or {}))
        self.correction = {"first_decision": first, "corrective": second}

    def record_recovery(self, data: dict) -> None:
        """Record the one bounded document-scoped recovery."""
        self.recovery = dict(data)

    def record_degradation(self, stage: str, code: str, detail: str = "") -> None:
        self.degradations.append({"stage": stage, "code": code,
                                  "detail": (detail or "")[:240]})

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "original_query": self.original_query,
            "effective_query": self.effective_query,
            "normalized_query": self.effective_query,
            "sparse_query": self.sparse_query or self.effective_query,
            "keywords": list(self.keywords),
            "focus_document_id": self.focus_document_id,
            "normalization_steps": list(self.normalization_steps),
            "stages": {name: list(rows) for name, rows in self.stages.items()},
            "timings_ms": dict(self.timings_ms),
            "gate": dict(self.gate),
            "correction": dict(self.correction) if self.correction else None,
            "recovery": dict(self.recovery) if self.recovery else None,
            "degradations": list(self.degradations),
            **({"table_selection": self.table_selection} if self.table_selection is not None else {}),
            **({"evidence_units": self.evidence_units} if self.evidence_units is not None else {}),
        }
