import json
import sys
from pathlib import Path

import pytest

from agent.tools.search_regulations import tool as search_tool
from agent.tools.search_regulations.scripts import gate_signals, retrieval
from agent.tools.search_regulations.scripts.corrective_query import CorrectiveQuery
from agent.tools.search_regulations.scripts.evidence_gate import (
    POLICY_V1, GateDecision, GatePolicy, decide,
)
from agent.tools.search_regulations.scripts.gate_signals import GateSignals
from agent.tools.search_regulations.scripts.trace import RetrievalTrace


CANDIDATE = GatePolicy(version="evidence-gate/1.2-candidate",
                       high_support=.70, low_maybe=.54,
                       require_support_signal=True,
                       require_semantic_recovery=True)


def _trace_with_docs(*rows):
    trace = RetrievalTrace.start("原始问句")
    trace.record_candidates("reranked", list(rows))
    trace.record_candidates("final", list(rows)[:2])
    return trace.to_dict()


def _row(doc_id, title, chunk_idx=0, text="正文"):
    return {"document_id": doc_id, "chunk_idx": chunk_idx, "source": title,
            "heading_path": [], "text": text, "snippet": text,
            "score": .7, "rerank_score": .7}


def _signals(**overrides):
    base = GateSignals(
        best_rerank=.64, second_rerank=.60, rerank_margin=.04, best_dense=.70,
        dense_bm25_top1_agreement=True, dense_bm25_top3_overlap=.5,
        top_doc_count=4, top_doc_share=1.0, top_doc_id="101",
        title_anchor=None, title_anchor_hit=None,
        docno_anchor=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None,
        token_coverage_top1=.6, token_coverage_top3=.7,
        candidate_count=10, bm25_candidate_count=10, bm25_available=True,
        focus_document_id=None, scope_conflict=False,
        scope_conflict_kind=None, absent_reference="", degradations=(),
        selected_document_ids=("101",),
    )
    return GateSignals(**{**base.__dict__, **overrides})


def test_unique_document_id_enables_recovery():
    trace = _trace_with_docs(_row(101, "A"), _row(101, "A"), _row(202, "B"))
    doc_id = search_tool.scoped_document_id(trace, "A")
    assert doc_id == 101
    assert search_tool.g2_1_eligible(
        CANDIDATE, {"absent_reference": "", "scope_conflict_kind": None},
        "A", doc_id, True) is True


def test_zero_document_id_blocks_recovery():
    trace = _trace_with_docs(_row(101, "A"))
    doc_id = search_tool.scoped_document_id(trace, "C")
    assert doc_id is None
    assert search_tool.g2_1_eligible(
        CANDIDATE, {"absent_reference": "", "scope_conflict_kind": None},
        "C", doc_id, True) is False


def test_multiple_document_ids_block_recovery():
    trace = _trace_with_docs(_row(101, "A"), _row(202, "A"))
    doc_id = search_tool.scoped_document_id(trace, "A")
    assert doc_id is None
    assert search_tool.g2_1_eligible(
        CANDIDATE, {"absent_reference": "", "scope_conflict_kind": None},
        "A", doc_id, True) is False


def test_scope_conflict_blocks_recovery():
    assert search_tool.g2_1_eligible(
        CANDIDATE, {"absent_reference": "",
                    "scope_conflict_kind": "entity_conflict"},
        "A", 101, True) is False


def test_unsupported_action_blocks_recovery():
    assert search_tool.g2_1_eligible(
        CANDIDATE, {"absent_reference": "",
                    "scope_conflict_kind": "unsupported_action"},
        "A", 101, True) is False


def test_policy_v1_and_non_semantic_policy_cannot_enter():
    clean = {"absent_reference": "", "scope_conflict_kind": None}
    assert search_tool.g2_1_eligible(POLICY_V1, clean, "A", 101, True) is False
    non_semantic = GatePolicy(version="evidence-gate/1.2-candidate",
                              require_semantic_recovery=False)
    assert search_tool.g2_1_eligible(non_semantic, clean, "A", 101, True) is False
    assert search_tool.g2_1_eligible(CANDIDATE, clean, "A", 101, False) is False


def test_scoped_filter_isolates_document():
    chunks = [{"document_id": 101, "chunk_id": "101:1"},
              {"document_id": 202, "chunk_id": "202:1"}]
    assert retrieval._only_document(chunks, 101) == [chunks[0]]
    assert retrieval._document_filter(101) is not None


def test_scoped_retrieval_never_combines_documents():
    chunks = [{"document_id": 101}, {"document_id": 101}, {"document_id": 202}]
    assert {c["document_id"] for c in retrieval._only_document(chunks, 101)} == {101}


def test_numeric_shape_is_recovered_from_scoped_chunk():
    claim = gate_signals.claim_anchor(
        "休学累计最长能休多久？", (), ["累计休学年限不得超过2年（含休学）。"])
    assert claim["claim_shape"] == "numeric"
    assert claim["claim_shape_supported"] is True


def test_scoped_chunk_without_shape_stays_unexposable():
    claim = gate_signals.claim_anchor(
        "休学累计最长能休多久？", (), ["休学应当经学校批准。"])
    assert claim["claim_shape"] == "numeric"
    assert claim["claim_shape_supported"] is False
    decision = decide(_signals(
        claim_anchor_terms=("休学", "累计"),
        claim_anchor_hits=("休学", "累计"),
        claim_anchor_coverage=1.0,
        claim_shape="numeric",
        claim_shape_supported=False,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None,
    ), CANDIDATE)
    assert decision.evidence_exposable is False
    assert decision.status in ("uncertain", "irrelevant")


def test_title_alone_does_not_expose_scoped_evidence():
    decision = retrieval._scoped_decision(_signals(
        title_anchor="A", title_anchor_hit=True,
        claim_anchor_terms=("休学", "累计"),
        claim_anchor_hits=(),
        claim_anchor_coverage=0.0,
        claim_shape="numeric", claim_shape_supported=False,
        entity_anchor=None, entity_anchor_hit=None,
    ), CANDIDATE)
    assert decision.status == "irrelevant"
    assert decision.reason == "scoped_claim_not_supported"
    assert decision.evidence_exposable is False


def test_trace_retains_original_corrective_and_scoped_document():
    trace = RetrievalTrace.start("原始问句")
    trace.set_effective_query("上一轮 原始问句", "multiturn_carry")
    trace.record_recovery({
        "recovery_kind": "document_scoped",
        "scoped_document_id": "101",
        "corrective_query": "原始问句 标题",
        "selected_scoped_chunk_ids": ["101:2"],
    })
    data = trace.to_dict()
    assert data["original_query"] == "原始问句"
    assert data["effective_query"] == "上一轮 原始问句"
    assert data["recovery"]["corrective_query"] == "原始问句 标题"
    assert data["recovery"]["scoped_document_id"] == "101"


def test_final_binding_uses_recovered_chunk():
    source = search_tool._source({
        "document_id": 101, "chunk_idx": 2, "source": "A",
        "heading_path": ["章"], "text": "恢复后的真实块", "rerank_score": .8,
    })
    assert source.chunk_id == "101:2"
    assert source.title == "A"
    assert source.text == "恢复后的真实块"


def test_existing_title_guard_still_rejects_non_uncertain():
    plan = search_tool.build_query_plan("q", [], None)
    first = GateDecision("supported", "supported_high", "p", (), False, True)
    assert search_tool.build_corrective_query("q", plan, first) is None


class _FakeRetrieval:
    def __init__(self, first_rows, scoped_rows, first_reason="gray_band_weak_support",
                 scope_conflict=None):
        self.first_rows = first_rows
        self.scoped_rows = scoped_rows
        self.first_reason = first_reason
        self.scope_conflict = scope_conflict
        self.calls = []

    def __call__(self, query, prev_query=None, trace=None, **kwargs):
        self.calls.append(dict(kwargs))
        scoped = kwargs.get("document_id") is not None
        rows = self.scoped_rows if scoped else self.first_rows
        if trace is not None:
            trace.record_candidates("reranked", rows)
            trace.record_candidates("final", rows[:2])
        contexts = [{
            "document_id": r["document_id"], "chunk_idx": r["chunk_idx"],
            "source": r["source"], "heading_path": [],
            "text": r["text"], "score": .7, "rerank_score": .7,
        } for r in rows]
        gate = {
            "status": "supported" if scoped else "uncertain",
            "reason": "supported_high" if scoped else self.first_reason,
            "policy_version": "evidence-gate/1.2-candidate",
            "retry_allowed": not scoped,
            "evidence_exposable": scoped,
            "contributing_signals": [],
        }
        signals = {
            "top_document_title": rows[0]["source"] if rows else None,
            "absent_reference": "",
            "scope_conflict_kind": self.scope_conflict,
        }
        return {"contexts": contexts, "gate": gate, "signals": signals,
                "tier": "high" if scoped else "medium",
                "reason": gate["reason"], "missing_files": []}


def _run_fake(monkeypatch, first_rows, scoped_rows, **kwargs):
    fake = _FakeRetrieval(first_rows, scoped_rows, **kwargs)
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda *a, **k: CorrectiveQuery(
                            original_query="q", normalized_query="q A",
                            deterministic_expansions=("A",), reason="guard",
                            retry_count=1, title_correction="A"))
    result = search_tool.search_regulations_result(
        "q", policy=CANDIDATE, document_scoped_recovery=True)
    return fake, result


def test_scoped_recovery_runs_at_most_once_and_uses_recovered_chunks(monkeypatch):
    fake, result = _run_fake(
        monkeypatch,
        [_row(101, "A"), _row(202, "B")],
        [_row(101, "A", chunk_idx=2, text="恢复块")])
    scoped_calls = [c for c in fake.calls if c.get("document_id") is not None]
    assert len(scoped_calls) == 1
    assert result.status == "success"
    assert result.sources[0].chunk_id == "101:2"
    assert result.sources[0].text == "恢复块"


def test_policy_v1_does_not_enter_scoped_path(monkeypatch):
    fake = _FakeRetrieval([_row(101, "A")], [_row(101, "A")])
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda *a, **k: CorrectiveQuery(
                            original_query="q", normalized_query="q A",
                            deterministic_expansions=("A",), reason="guard",
                            retry_count=1, title_correction="A"))
    search_tool.search_regulations_result(
        "q", policy=POLICY_V1, document_scoped_recovery=True)
    assert not [c for c in fake.calls if c.get("document_id") is not None]


def test_scope_conflict_does_not_enter_scoped_path(monkeypatch):
    fake, _ = _run_fake(
        monkeypatch, [_row(101, "A")], [_row(101, "A")],
        scope_conflict="unsupported_action")
    assert not [c for c in fake.calls if c.get("document_id") is not None]


def test_policy_v1_zero_drift_472_rows():
    work = Path(__file__).resolve().parents[5] / "_work" / "phase7" / "evidence-gate-v1"
    sys.path.insert(0, str(work))
    from build_candidate_v12 import old_decide
    rows = [json.loads(line) for line in
            (work / "claim-anchor-survey-v1.1.jsonl").read_text(
                encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 472
    mismatches = 0
    for row in rows:
        signals = GateSignals(**row["signals"])
        if decide(signals, POLICY_V1).to_dict() != old_decide(signals, POLICY_V1).to_dict():
            mismatches += 1
    assert mismatches == 0
