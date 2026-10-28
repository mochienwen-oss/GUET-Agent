from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.core import ToolResult
from agent.tools.search_regulations.renderer import render
from agent.tools.search_regulations.scripts import retrieval
from agent.tools.search_regulations.scripts.trace import RetrievalTrace


QUERY = "计算机网络支撑哪些毕业要求？"


def row(idx, parent="table-a", *, doc=1, term="计算机网络", score=.98):
    text = f"毕业要求：要求{idx}\n支撑课程：{term}"
    return {"document_id": doc, "chunk_idx": idx, "chunk_id": f"row-{doc}-{idx}",
            "source": f"方案{doc}", "source_sha256": f"sha-{doc}", "parent_id": parent,
            "row_number": idx+1, "source_span": [10, 100], "heading_path": ["对应矩阵"],
            "chunking_version": "structure-evidence/1", "block_type": "table",
            "text": text, "snippet": text, "rerank_score": score,
            "rerank_score_version": "logit-single-sigmoid/1",
            "table_fields": [{"text": term, "header": "支撑课程"}], "score": .8}


def select(ranked, *, query=QUERY, top_k=5, max_chars=8000, cap=2):
    from agent.tools.search_regulations.scripts.table_selection import select_table_rows
    baseline = retrieval._cap_per_doc(ranked, top_k)
    return select_table_rows(query, ranked, baseline, top_k=top_k, per_doc=cap,
                             max_chars=max_chars, min_score=.8473)


def test_two_physical_tables_return_three_rows_without_merging_sources(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "max_chunks_per_doc", 2)
    ranked = [row(59, "table-b"), row(51), row(52, term="操作系统"), row(48)]
    original = deepcopy(ranked)
    actual, info = select(ranked)
    assert {r["chunk_idx"] for r in actual} == {48, 51, 59}
    assert ranked == original
    assert all(any(r is old for old in ranked) for r in actual)
    assert len(info["groups"]) == 2 and not info["omitted_chunk_ids"]


def test_not_an_enumeration_keeps_existing_selection(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "max_chunks_per_doc", 2)
    ranked = [row(1), row(2), row(3)]
    baseline = retrieval._cap_per_doc(ranked, 5)
    actual, info = select(ranked, query="计算机网络支撑这个要求吗？")
    assert actual == baseline and info is None


@pytest.mark.parametrize("key", ["parent_id", "source_sha256", "row_number", "table_fields", "rerank_score_version"])
def test_missing_source_or_score_metadata_never_enables_grouping(key):
    ranked = [row(1), row(2), row(3)]
    for item in ranked:
        item.pop(key)
    actual, info = select(ranked)
    assert info is None


def test_different_documents_never_share_a_group(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "max_chunks_per_doc", 2)
    actual, info = select([row(1), row(2), row(3, doc=2), row(4, doc=2)])
    assert len(info["groups"]) == 2
    assert len({tuple(g["source_identity"]) for g in info["groups"]}) == 2
    assert len(actual) == 4


def test_low_score_and_substring_subject_do_not_expand():
    ranked = [row(1, term="高等代数"), row(2, term="代数"), row(3, term="代数", score=.1)]
    actual, info = select(ranked, query="高等代数支撑哪些毕业要求？")
    assert info is None


@pytest.mark.parametrize("budget", ["row", "characters"])
def test_budget_keeps_complete_best_row_and_reports_omission(budget):
    ranked = [row(3), row(1), row(2)]
    actual, info = select(ranked, top_k=1 if budget == "row" else 5,
                           max_chars=100 if budget == "characters" else 8000)
    assert actual and actual[0] is ranked[0]
    assert info["omitted_chunk_ids"]
    assert all(r["text"] == next(o["text"] for o in ranked if o["chunk_idx"] == r["chunk_idx"]) for r in actual)


def test_real_retrieval_selection_hook_and_partial_guard(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "max_chunks_per_doc", 2)
    monkeypatch.setattr(retrieval, "ensure_collection", lambda: None)
    monkeypatch.setattr(retrieval, "_absent_reference", lambda q: "")
    monkeypatch.setattr(retrieval, "_gate_anchors", lambda: None)
    monkeypatch.setattr(retrieval, "_expand_flow", lambda xs: xs)
    monkeypatch.setattr(retrieval, "_follow_refs", lambda xs: xs)
    monkeypatch.setattr(retrieval, "_missing_files", lambda xs: [])
    ranked = [row(3, "table-b"), row(2), row(1)]
    monkeypatch.setattr(retrieval, "_retrieve_chunks", lambda *a, **kw: ranked)
    monkeypatch.setattr(retrieval, "_rerank", lambda *a: None)
    trace = RetrievalTrace.start(QUERY)
    result = retrieval.retrieve(QUERY, top_k=5, trace=trace)
    assert len(result["contexts"]) == 3
    assert result["gate"]["status"] == "supported"
    assert trace.to_dict()["table_selection"]["scope"] == "qualified_rerank_candidates"
    partial = retrieval.retrieve(QUERY, top_k=1)
    assert partial["gate"]["status"] == "uncertain"
    assert partial["reason"] == "table_enumeration_incomplete"
    observation = render(ToolResult(tool="search_regulations", ok=True, status="partial_evidence",
                                   data={"blocks": partial["contexts"], "reason": partial["reason"]}))
    assert "不能据此声称已经列全" in observation


def test_qdrant_projection_preserves_row_identity():
    original = row(1)
    mapped = retrieval._chunk_from_point(SimpleNamespace(payload=original), .8)
    assert mapped["row_number"] == original["row_number"]
