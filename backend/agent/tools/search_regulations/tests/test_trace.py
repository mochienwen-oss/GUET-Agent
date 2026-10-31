import json

import pytest

from agent.tools.search_regulations.scripts.inspection.trace import RetrievalTrace


def test_trace_preserves_chunk_identity_and_json_round_trips():
    trace = RetrievalTrace.start("  转专业怎么办  ", trace_id="trace-1")
    trace.set_effective_query("转专业怎么办", "trim")
    trace.record_candidates("dense", [
        {"document_id": 7, "chunk_idx": 1, "source": "甲", "text": "a", "score": .8},
        {"document_id": 7, "chunk_idx": 2, "source": "甲", "text": "b", "score": .7},
    ])
    trace.record_timing("dense", 1.23456)
    trace.record_gate("supported", "rerank_high", .88)

    data = json.loads(json.dumps(trace.to_dict(), ensure_ascii=False))
    assert data["trace_id"] == "trace-1"
    assert [x["chunk_id"] for x in data["stages"]["dense"]] == ["7:1", "7:2"]
    assert data["timings_ms"]["dense"] == 1.235
    assert data["gate"] == {
        "status": "supported", "reason": "rerank_high", "best_score": .88}


def test_trace_records_degradation_without_claiming_no_evidence():
    trace = RetrievalTrace.start("问题", trace_id="trace-2")
    trace.record_degradation("rerank", "fallback", "CUDA unavailable")
    assert trace.to_dict()["degradations"] == [{
        "stage": "rerank", "code": "fallback", "detail": "CUDA unavailable"}]
    assert trace.to_dict()["gate"] == {}


def test_unknown_gate_status_is_rejected():
    trace = RetrievalTrace.start("问题")
    with pytest.raises(ValueError):
        trace.record_gate("no_evidence", "wrong vocabulary")


def test_gate_decision_telemetry_round_trips_without_touching_stages():
    trace = RetrievalTrace.start("问题", trace_id="trace-3")
    trace.record_candidates("dense", [{"document_id": 7, "chunk_idx": 1,
                                       "source": "甲", "text": "a", "score": .8}])
    trace.record_gate("uncertain", "gray_band", .60,
                      policy_version="evidence-gate/1",
                      signals={"best_rerank": .60},
                      contributing_signals=["dense_strength"],
                      retry_allowed=True, evidence_exposable=False)
    data = trace.to_dict()
    assert data["gate"] == {
        "status": "uncertain", "reason": "gray_band", "best_score": .60,
        "policy_version": "evidence-gate/1",
        "signals": {"best_rerank": .60},
        "contributing_signals": ["dense_strength"],
        "retry_allowed": True,
        "evidence_exposable": False,
    }
    assert data["stages"]["dense"][0]["chunk_id"] == "7:1"
