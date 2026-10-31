from types import SimpleNamespace

from agent.tools.search_regulations.scripts.recall import retrieval
from agent.tools.search_regulations.scripts.inspection.trace import RetrievalTrace


def _chunk(doc, idx, score):
    return {"document_id": doc, "chunk_idx": idx, "source": f"文档{doc}",
            "snippet": f"文档{doc}块{idx}", "text": f"文档{doc}块{idx}", "heading_path": [],
            "score": score, "rrf": .01}


def test_candidate_filter_precedes_selection_but_not_linked_context(monkeypatch):
    monkeypatch.setattr(retrieval, "ensure_collection", lambda: None)
    monkeypatch.setattr(retrieval, "_absent_reference", lambda query: "")
    monkeypatch.setattr(retrieval, "_gate_anchors", lambda: None)
    monkeypatch.setattr(retrieval, "_retrieve_chunks", lambda *a, **k: [
        _chunk(1, 1, .9), _chunk(2, 1, .8), _chunk(3, 1, .7)])

    def rerank(query, chunks):
        for row, score in zip(chunks, [.9, .05, .049]):
            row["rerank_score"] = score

    monkeypatch.setattr(retrieval, "_rerank", rerank)
    monkeypatch.setattr(retrieval, "_expand_flow", lambda rows: rows + [_chunk(1, 2, None)])
    monkeypatch.setattr(retrieval, "_follow_refs", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_missing_files", lambda rows: [])
    trace = RetrievalTrace.start("问题")
    result = retrieval.retrieve("问题", top_k=5, trace=trace)
    assert [r["doc_id"] for r in trace.stages["candidate_filtered"]] == ["1", "2"]
    assert {(r["document_id"], r["chunk_idx"]) for r in result["contexts"]} == {
        (1, 1), (2, 1), (1, 2)}
    assert result["contexts"][-1].get("rerank_score") is None


def test_dense_only_does_not_apply_reranker_threshold(monkeypatch):
    monkeypatch.setattr(retrieval, "ensure_collection", lambda: None)
    monkeypatch.setattr(retrieval, "_absent_reference", lambda query: "")
    monkeypatch.setattr(retrieval, "_gate_anchors", lambda: None)
    monkeypatch.setattr(retrieval, "_retrieve_chunks", lambda *a, **k: [_chunk(1, 1, .9)])
    monkeypatch.setattr(retrieval, "_expand_flow", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_follow_refs", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_missing_files", lambda rows: [])
    trace = RetrievalTrace.start("问题")
    retrieval.retrieve("问题", use_reranker=False, trace=trace)
    assert len(trace.stages["candidate_filtered"]) == 1


def test_trace_on_and_off_return_identical_results(monkeypatch):
    monkeypatch.setattr(retrieval, "ensure_collection", lambda: None)
    monkeypatch.setattr(retrieval, "_absent_reference", lambda query: "")
    monkeypatch.setattr(retrieval, "_gate_anchors", lambda: None)
    monkeypatch.setattr(retrieval, "_retrieve_chunks",
                        lambda query, limit, trace=None, **kwargs: [
                            _chunk(1, 1, .9), _chunk(2, 1, .8)])
    monkeypatch.setattr(retrieval, "_rerank", lambda query, chunks: [
        row.__setitem__("rerank_score", .9 - i / 10) for i, row in enumerate(chunks)])
    monkeypatch.setattr(retrieval, "_expand_flow", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_follow_refs", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_missing_files", lambda contexts: [])

    without = retrieval.retrieve("问题", top_k=2)
    trace = RetrievalTrace.start("问题", trace_id="fixed")
    with_trace = retrieval.retrieve("问题", top_k=2, trace=trace)
    assert with_trace == without
    assert trace.to_dict()["gate"]["status"] == "supported"
    assert [row["title"] for row in trace.to_dict()["stages"]["final"]] == ["文档1", "文档2"]


def test_retrieve_chunks_records_dense_sparse_and_fused(monkeypatch):
    class Client:
        def query_points(self, **kwargs):
            if kwargs["using"] == "dense":
                points = [SimpleNamespace(payload={"document_id": 1, "chunk_idx": 1,
                                                    "source": "甲", "text": "a"}, score=.8)]
            else:
                points = [SimpleNamespace(payload={"document_id": 2, "chunk_idx": 1,
                                                    "source": "乙", "text": "b"}, score=3.0)]
            return SimpleNamespace(points=points)

    monkeypatch.setattr(retrieval, "_get_client", lambda: Client())
    monkeypatch.setattr(retrieval, "_encode_query", lambda query: [0.1])
    monkeypatch.setattr(retrieval.settings, "use_sparse", True)
    monkeypatch.setattr(retrieval.settings, "aux_route", False)
    monkeypatch.setattr(retrieval.settings, "sparse_fuse_gate", False)
    from agent.tools.search_regulations.scripts.index_ops import bm25
    monkeypatch.setattr(bm25, "load_vocab", lambda: {"x": 1})
    monkeypatch.setattr(bm25, "query_vector", lambda query, vocab: ([1], [1.0]))
    trace = RetrievalTrace.start("问题")
    result = retrieval._retrieve_chunks("问题", 10, trace)
    data = trace.to_dict()
    assert len(data["stages"]["dense"]) == 1
    assert len(data["stages"]["bm25"]) == 1
    assert {row["title"] for row in data["stages"]["fused"]} == {"甲", "乙"}
    assert len(result) == 2


def test_trace_records_sparse_query_and_keywords_without_changing_semantic_query():
    trace = RetrievalTrace.start("原始问题")
    trace.set_effective_query("上一轮 原始问题", "multiturn_carry")
    trace.set_query_plan("补考 课程考核", ["补考", "课程考核"])
    data = trace.to_dict()
    assert data["effective_query"] == "上一轮 原始问题"
    assert data["sparse_query"] == "补考 课程考核"
    assert data["keywords"] == ["补考", "课程考核"]


def test_explicit_keywords_reach_bm25_not_dense_or_reranker(monkeypatch):
    seen = {}
    monkeypatch.setattr(retrieval, "ensure_collection", lambda: None)
    monkeypatch.setattr(retrieval, "_absent_reference", lambda query: "")
    monkeypatch.setattr(retrieval, "_gate_anchors", lambda: None)
    monkeypatch.setattr(retrieval.settings, "multiturn_carry", True)

    def fake_retrieve(query, limit, trace=None, *, sparse_query=None, **kwargs):
        seen["dense"] = query
        seen["sparse"] = sparse_query
        return [_chunk(1, 1, .9)]

    def fake_rerank(query, chunks):
        seen["rerank"] = query
        chunks[0]["rerank_score"] = .9

    monkeypatch.setattr(retrieval, "_retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(retrieval, "_rerank", fake_rerank)
    monkeypatch.setattr(retrieval, "_expand_flow", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_follow_refs", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_missing_files", lambda contexts: [])

    retrieval.retrieve("我大二", top_k=1, prev_query="转专业有什么要求",
                       keywords=["转专业", "申请条件"])

    assert seen == {
        "dense": "转专业有什么要求 我大二",
        "sparse": "转专业 申请条件",
        "rerank": "转专业有什么要求 我大二",
    }
