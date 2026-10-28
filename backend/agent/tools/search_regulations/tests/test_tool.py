import pytest

from agent.runtime import registry
from agent.tools.search_regulations import tool as search_tool


def _probe(monkeypatch, reason, contexts=(), tier="low", missing_files=()):
    """替换 retrieve_frozen 的检索替身：不加载模型、不联网（同既有测试的口径）。"""
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", lambda query, prev_query=None, trace=None, **kwargs: {
        "contexts": list(contexts), "tier": tier, "reason": reason,
        "missing_files": list(missing_files),
    })
    return registry.call("search_regulations", {"query": "随便问一句"})


BLOCK = {"source": "甲规定", "heading_path": ["第一章"], "text": "第一条 正文",
         "document_id": 7, "chunk_idx": 3, "rerank_score": 0.88}


def test_search_preserves_chunk_identity_and_trace(monkeypatch):
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", lambda query, prev_query=None, trace=None, **kwargs: {
        "contexts": [{"source": "甲规定", "heading_path": ["第一章"], "text": "第一条 正文",
                      "document_id": 7, "chunk_idx": 3, "rerank_score": 0.88}],
        "tier": "high", "reason": "rerank_high", "missing_files": [],
    })
    result = registry.call("search_regulations", {"query": "怎么规定"})
    assert result.status == "success"
    assert result.sources[0].chunk_id == "7:3"
    assert result.sources[0].text == "第一条 正文"
    assert result.meta["retrieval_trace"]["reason"] == "rerank_high"
    assert "rerank_high" not in registry.render(result)


def test_empty_search_is_no_evidence(monkeypatch):
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", lambda query, prev_query=None, trace=None, **kwargs: {
        "contexts": [], "tier": "low", "reason": "empty", "missing_files": [],
    })
    result = registry.call("search_regulations", {"query": "没有的内容"})
    assert result.status == "no_evidence"
    assert result.sources == []
    assert registry.render(result) == "未检索到相关资料。"


def test_search_passes_normalized_keywords_to_retrieval(monkeypatch):
    seen = {}

    def fake(query, prev_query=None, trace=None, *, keywords=None, **kwargs):
        seen.update({"query": query, "keywords": keywords})
        return {"contexts": [], "tier": "low", "reason": "empty", "missing_files": []}

    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    result = registry.call("search_regulations", {
        "query": "  补考有什么规定  ",
        "keywords": [" 补考 ", "课程考核", "补考"],
    })

    assert seen == {"query": "补考有什么规定", "keywords": ["补考", "课程考核"]}
    assert result.data["query"] == "补考有什么规定"
    assert result.data["keywords"] == ["补考", "课程考核"]


# ★ retrieve_frozen 的**空 contexts** 出口共 6 条（见 scripts/retrieval.py 的 _out 调用点）：
#   empty / absent:<文档> / no_dense_signal / rerank_low / score_low / grader_no。
#   前三条是「没有候选」（no_evidence），后三条是「找到过候选但相关性门拒绝」
#   （irrelevant_result）。压成同一个状态就等于把「库里没有」和「有但不相关」说成同一件事。
@pytest.mark.parametrize("reason,missing", [
    ("empty", ()),
    ("absent:桂电教〔2099〕1号", ("桂电教〔2099〕1号",)),
    ("no_dense_signal", ()),
])
def test_no_candidate_reasons_are_no_evidence(monkeypatch, reason, missing):
    result = _probe(monkeypatch, reason, missing_files=missing)
    assert result.status == "no_evidence"
    assert result.sources == []
    assert result.data["reason"] == reason


@pytest.mark.parametrize("reason", ["rerank_low", "score_low", "grader_no"])
def test_gate_rejection_reasons_are_irrelevant_result(monkeypatch, reason):
    result = _probe(monkeypatch, reason)
    assert result.status == "irrelevant_result"
    assert result.sources == []
    assert result.data["blocks"] == []
    assert result.data["reason"] == reason


def test_irrelevant_result_render_keeps_the_boundary(monkeypatch):
    """无关结果**不得**渲染成「未检索到」，也不得声称学校没有规定。"""
    rendered = registry.render(_probe(monkeypatch, "rerank_low"))
    assert rendered.strip()
    assert "未检索到相关资料" not in rendered
    assert "无关" in rendered
    assert "不能作为回答依据" in rendered
    assert rendered != registry.render(_probe(monkeypatch, "empty"))


@pytest.mark.parametrize("reason,missing", [
    ("empty", ()),
    ("absent:桂电教〔2099〕1号", ("桂电教〔2099〕1号",)),
])
def test_no_evidence_render_is_verbatim(monkeypatch, reason, missing):
    result = _probe(monkeypatch, reason, missing_files=missing)
    assert registry.render(result) == "未检索到相关资料。"


def _gated(monkeypatch, state, blocks=(), reason="r", retry=False,
           exposable=True):
    def fake(query, prev_query=None, trace=None, **kwargs):
        gate = {"status": state, "reason": reason,
                "policy_version": "test/1", "retry_allowed": retry,
                "evidence_exposable": exposable}
        if trace is not None:
            trace.record_gate(state, reason, .6, policy_version="test/1",
                              signals={"best_rerank": .6},
                              contributing_signals=[], retry_allowed=retry,
                              evidence_exposable=exposable)
        return {"contexts": list(blocks), "tier": "medium", "reason": reason,
                "missing_files": [], "gate": gate}

    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    return registry.call("search_regulations", {"query": "随便问一句"})


@pytest.mark.parametrize("state,expected", [
    ("supported", "success"),
    ("uncertain", "partial_evidence"),
    ("irrelevant", "irrelevant_result"),
    ("empty", "no_evidence"),
])
def test_gate_state_is_the_authoritative_status(monkeypatch, state, expected):
    blocks = [BLOCK] if state in ("supported", "uncertain") else []
    result = _gated(monkeypatch, state, blocks)
    assert result.status == expected
    assert result.data["gate"]["status"] == state
    assert result.meta["retrieval_trace"]["gate"]["policy_version"] == "test/1"


def test_gray_evidence_is_partial_not_success(monkeypatch):
    result = _gated(monkeypatch, "uncertain", [BLOCK], reason="gray_band", retry=True)
    assert result.status == "partial_evidence"
    assert result.data["blocks"][0]["text"] == "第一条 正文"
    assert result.sources[0].chunk_id == "7:3"
    assert result.meta["retrieval_trace"]["gate"]["retry_allowed"] is True


@pytest.mark.parametrize("state,blocks", [
    ("supported", []),
    ("uncertain", []),
    ("irrelevant", [BLOCK]),
    ("empty", [BLOCK]),
])
def test_gate_state_block_contract_fails_closed(monkeypatch, state, blocks):
    result = _gated(monkeypatch, state, blocks)
    assert result.status == "tool_failed"


def test_unknown_gate_state_becomes_tool_failed(monkeypatch):
    result = _gated(monkeypatch, "new_state", [BLOCK])
    assert result.status == "tool_failed"
    assert result.ok is False
    assert "未检索到相关资料" not in registry.render(result)


def test_legacy_medium_without_gate_keeps_old_mapping(monkeypatch):
    """Callers that bypass the evidence gate keep the legacy reason mapping."""
    result = _probe(monkeypatch, "medium", [BLOCK], tier="medium")
    assert result.status == "success"
    assert result.meta["retrieval_trace"]["reason"] == "medium"


def test_gate_telemetry_does_not_change_evidence_identity(monkeypatch):
    from agent.core.evidence import identity

    result = _gated(monkeypatch, "uncertain", [BLOCK])
    assert [identity(source) for source in result.sources] == ["c:7:3"]
    assert result.sources[0].text == "第一条 正文"
    assert result.data["gate"]["status"] == "uncertain"


def _gate(status, reason, retry, exposable=True):
    return {"status": status, "reason": reason,
            "policy_version": "test/1", "retry_allowed": retry,
            "evidence_exposable": exposable}


def test_uncertain_triggers_exactly_one_corrective_retrieval(monkeypatch):
    from agent.tools.search_regulations.scripts.corrective_query import CorrectiveQuery

    calls = []

    def fake(query, prev_query=None, trace=None, *, keywords=None, extra_query=None, **kwargs):
        calls.append(extra_query)
        if extra_query is None:
            return {"contexts": [BLOCK], "tier": "medium", "reason": "gray_band",
                    "missing_files": [],
                    "gate": _gate("uncertain", "gray_band", True)}
        return {"contexts": [BLOCK], "tier": "high", "reason": "supported_high",
                "missing_files": [],
                "gate": _gate("supported", "supported_high", False)}

    corrected = CorrectiveQuery(original_query="原问题",
                                normalized_query="原问题 补考规定",
                                deterministic_expansions=("补考规定",),
                                reason="uncertain_gray_band", retry_count=1)
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda query, plan, decision, **kwargs: corrected)
    result = registry.call("search_regulations", {"query": "原问题"})

    assert calls == [None, "原问题 补考规定"]
    assert result.status == "success"
    assert result.data["query"] == "原问题"
    assert result.data["first_gate"]["status"] == "uncertain"
    assert result.data["corrective"]["normalized_query"] == "原问题 补考规定"
    assert result.meta["retrieval_trace"]["correction"]["corrective"]["retry_count"] == 1


def test_second_uncertain_does_not_retry_again(monkeypatch):
    from agent.tools.search_regulations.scripts.corrective_query import CorrectiveQuery

    calls = []

    def fake(query, prev_query=None, trace=None, *, keywords=None, extra_query=None, **kwargs):
        calls.append(extra_query)
        return {"contexts": [BLOCK], "tier": "medium", "reason": "gray_band",
                "missing_files": [],
                "gate": _gate("uncertain", "gray_band", True, False)}

    corrected = CorrectiveQuery("原问题", "原问题 补考规定", ("补考规定",), "r", 1)
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda query, plan, decision, **kwargs: corrected)
    result = registry.call("search_regulations", {"query": "原问题"})
    assert len(calls) == 2
    assert result.status == "irrelevant_result"
    assert result.data["blocks"] == []
    assert result.sources == []


def test_second_grounded_uncertain_is_partial_evidence(monkeypatch):
    from agent.tools.search_regulations.scripts.corrective_query import CorrectiveQuery

    calls = []

    def fake(query, prev_query=None, trace=None, *, keywords=None, extra_query=None, **kwargs):
        calls.append(extra_query)
        return {"contexts": [BLOCK], "tier": "medium", "reason": "gray_band",
                "missing_files": [],
                "gate": _gate("uncertain", "gray_band", True, True)}

    corrected = CorrectiveQuery("原问题", "原问题 补考规定", ("补考规定",), "r", 1)
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda query, plan, decision, **kwargs: corrected)
    result = registry.call("search_regulations", {"query": "原问题"})
    assert len(calls) == 2
    assert result.status == "partial_evidence"
    assert result.data["blocks"][0]["text"] == "第一条 正文"


def test_unexposable_uncertain_without_correction_fails_closed(monkeypatch):
    calls = []

    def fake(query, prev_query=None, trace=None, **kwargs):
        calls.append(query)
        return {"contexts": [BLOCK], "tier": "medium", "reason": "gray_band_weak_support",
                "missing_files": [],
                "gate": _gate("uncertain", "gray_band_weak_support", True, False)}

    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda *args, **kwargs: None)
    result = registry.call("search_regulations", {"query": "这个怎么弄？"})
    assert calls == ["这个怎么弄？"]
    assert result.status == "irrelevant_result"
    assert result.data["blocks"] == []
    assert result.sources == []
    assert result.data["first_gate"]["status"] == "uncertain"


def test_empty_or_irrelevant_never_corrects(monkeypatch):
    calls = []

    def fake(query, prev_query=None, trace=None, *, keywords=None, extra_query=None, **kwargs):
        calls.append(extra_query)
        return {"contexts": [], "tier": "low", "reason": "absent_reference",
                "missing_files": [], "gate": _gate("empty", "absent_reference", False)}

    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", fake)
    monkeypatch.setattr(search_tool, "build_corrective_query",
                        lambda *args: (_ for _ in ()).throw(AssertionError("must not correct")))
    result = registry.call("search_regulations", {"query": "不存在的文件"})
    assert calls == [None]
    assert result.status == "no_evidence"


# ★ reason → ToolStatus 是**穷举契约**：空结果只允许落在上面两张表里。表外的 reason
#   （未知字符串/None/空串）属契约漂移，必须当场抛错，**不得静默降级成 no_evidence** ——
#   那会把「有内容但不相关」说成「库里没有」，正是 Task 4.3 要修的丢失。
@pytest.mark.parametrize("reason", ["new_gate_reason", None, ""])
def test_unknown_empty_reason_raises_value_error(monkeypatch, reason):
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", lambda query, prev_query=None, trace=None, **kwargs: {
        "contexts": [], "tier": "low", "reason": reason, "missing_files": [],
    })
    with pytest.raises(ValueError) as caught:
        search_tool.search_regulations_result("随便问一句")
    message = str(caught.value)
    assert repr(reason) in message, message
    for known in ("empty", "no_dense_signal", "rerank_low", "score_low", "grader_no"):
        assert known in message, message
    assert "不得静默归为 no_evidence" in message, message


def test_unknown_reason_becomes_tool_failed_through_registry(monkeypatch):
    """direct API 抛错，Registry 边界把它转成 tool_failed —— 绝**不**是 no_evidence。"""
    result = _probe(monkeypatch, "new_gate_reason")
    assert result.status == "tool_failed"
    assert result.ok is False
    assert result.error.code == "tool_failed"
    rendered = registry.render(result)
    assert rendered.strip()
    assert "未检索到相关资料" not in rendered
    assert "不代表学校没有相关规定" in rendered
