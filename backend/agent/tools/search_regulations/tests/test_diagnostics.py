from agent.tools.search_regulations.scripts.diagnostics import (
    _config_fingerprint,
    _expected_evidence_rank,
    diagnose_case,
    load_keyword_map,
    summarize,
)


CASE = {
    "id": "a", "query": "q", "answerable": True,
    "expected_tool": "search_regulations",
    "expected_docs": ["桂林电子科技大学学籍管理规定"],
    "expected_evidence": ["第一条 原文"],
    "acceptable_gate": ["supported", "ambiguous"],
}


def _row(stage, rank=1, title="学籍管理规定", text="第一条 原文"):
    return {"rank": rank, "title": title, "chunk_id": "1:1",
            "text_preview": text}


def _trace(*, fused=True, pool=True, top5=True, final=True, gate="supported"):
    return {"stages": {
        "fused": [_row("fused")] if fused else [],
        "rerank_pool": [_row("pool")] if pool else [],
        "reranked": [_row("rank", 1 if top5 else 6)] if pool else [],
        "final": [_row("final")] if final else [],
    }, "gate": {"status": gate, "reason": "test"}}


def _diagnosis(trace, *, docs=None, corpus="第一条 原文", final=None):
    docs = docs if docs is not None else {"学籍管理规定"}
    texts = {"学籍管理规定": [corpus]}
    return diagnose_case(CASE, trace, corpus_docs=docs, corpus_texts=texts,
                         final_texts=final if final is not None else [corpus])["diagnosis"]


def test_each_answerable_failure_has_one_exclusive_layer():
    assert _diagnosis(_trace(), docs={"其他文档"}) == "corpus_coverage"
    assert _diagnosis(_trace(fused=False)) == "candidate_pool"
    assert _diagnosis(_trace(pool=False)) == "dedupe_or_truncation"
    # ★ 必须显式传 final=[]：`_diagnosis` 的默认 final_texts 是 [corpus]，
    #   而 corpus 里正含证据 —— 新逻辑「final 有证据即 success」会把它判成 success。
    #   这里要模拟的是「精排没进前 5 **且** final 也没有证据」，那才是 rerank 层。
    assert _diagnosis(_trace(top5=False, final=False), final=[]) == "rerank"
    assert _diagnosis(_trace(final=False, gate="irrelevant")) == "gate"
    assert _diagnosis(_trace(), final=["另一个块"]) == "final_selection"
    assert _diagnosis(_trace()) == "success"


def test_focused_final_selection_can_recover_evidence_below_rerank_top5():
    assert _diagnosis(_trace(top5=False)) == "success"


def test_evidence_rank_does_not_accept_an_unrelated_chunk_from_expected_document():
    rows = [
        _row("rank", rank=1, text="同一份学籍管理规定里的其他条款"),
        {"rank": 2, "title": "学籍管理规定", "chunk_id": "1:2",
         "text_preview": "第一条 原文"},
    ]

    assert _expected_evidence_rank(CASE, rows) == 2


def test_diagnosis_reports_evidence_removed_before_rerank_pool():
    trace = _trace()
    trace["stages"]["fused"] = [
        {"rank": 1, "title": "学籍管理规定", "chunk_id": "1:2",
         "text_preview": "第一条 原文"}]
    trace["stages"]["rerank_pool"] = [
        _row("pool", text="同一份文档里的其他条款")]

    result = diagnose_case(
        CASE, trace,
        corpus_docs={"学籍管理规定"},
        corpus_texts={"学籍管理规定": ["第一条 原文", "其他条款"]},
        corpus_chunks={"1:1": "同一份文档里的其他条款",
                       "1:2": "第一条 原文"},
        final_texts=["同一份文档里的其他条款"],
    )

    assert result["diagnosis"] == "dedupe_or_truncation"


def test_negative_case_only_measures_gate_acceptance():
    negative = dict(CASE, answerable=False, expected_docs=[], expected_evidence=[],
                    acceptable_gate=["empty", "irrelevant"])
    assert diagnose_case(negative, _trace(gate="empty"))["diagnosis"] == "success"
    assert diagnose_case(negative, _trace(gate="supported"))["diagnosis"] == "gate"


def test_summary_keeps_recall_and_gate_metrics_separate():
    rows = [
        {"case": CASE, "candidate_rank": 1, "rerank_rank": 2,
         "candidate_evidence_rank": 1, "rerank_evidence_rank": 2,
         "gate": "supported", "diagnosis": "success"},
        {"case": CASE, "candidate_rank": None, "rerank_rank": None,
         "candidate_evidence_rank": None, "rerank_evidence_rank": None,
         "gate": "irrelevant", "diagnosis": "candidate_pool"},
        {"case": dict(CASE, answerable=False, acceptable_gate=["empty", "irrelevant"]),
         "candidate_rank": None, "rerank_rank": None,
         "candidate_evidence_rank": None, "rerank_evidence_rank": None,
         "gate": "supported", "diagnosis": "gate"},
    ]
    result = summarize(rows)
    assert result["candidate_recall@120"] == .5
    assert result["rerank_recall@5"] == .5
    assert result["evidence_candidate_recall@120"] == .5
    assert result["evidence_rerank_recall@5"] == .5
    assert result["gate_false_reject_rate"] == .5
    assert result["out_of_scope_false_accept_rate"] == 1.0


def test_config_fingerprint_records_multi_signal_controls():
    values = _config_fingerprint()["values"]
    assert "gate_multi_signal" in values
    assert "aux_focus_topn" in values
    assert "aux_focus_need" in values
    assert "multi_signal_focus_cap" in values
    assert "rerank_strict" in values


def test_keyword_map_requires_id_to_string_list(tmp_path):
    valid = tmp_path / "valid.json"
    valid.write_text('{"case-1":["转专业","申请条件"]}', encoding="utf-8")
    assert load_keyword_map(valid) == {"case-1": ["转专业", "申请条件"]}

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"case-1":"转专业"}', encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="字符串数组"):
        load_keyword_map(invalid)
