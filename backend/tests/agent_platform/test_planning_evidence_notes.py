"""Registry observation reaches the next planner; budgets remain authoritative."""
import asyncio
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import Source, ToolResult
from agent.core.scope import request_scope
from agent.runtime import graph, grounded_answer as G
from agent.tools.search_regulations import tool as search_tool


def test_real_search_renderer_adds_only_lexical_hint_without_changing_sources(monkeypatch):
    source = {"source": "测试规定", "document_id": 1, "chunk_idx": 2, "heading_path": [],
              "text": "只能按期返校，不得自行延长。", "rerank_score": .99}
    monkeypatch.setattr(search_tool.retrieval, "retrieve_frozen", lambda *a, **kw: {
        "contexts": [source], "reason": "rerank_high", "tier": "high"})
    result = graph.registry.call("search_regulations", {"query": "交流能否延期或换国家？"})
    assert result.status == "success" and result.sources[0].text == source["text"]
    assert result.data["coverage"]["scope_filtered"] is None
    observation = graph.registry.render(result)
    assert "检索词匹配" in observation and "不代表事实缺失或证据充分" in observation


def test_scope_warning_is_in_observation_before_hash_and_next_planner(monkeypatch):
    result = ToolResult(tool="search_regulations", ok=True, status="success", data={"blocks": []},
                        sources=[Source(title="教职工进修管理办法", text="教职工可申请进修。")])
    monkeypatch.setattr(graph.registry, "call", lambda *a: result)
    scope = request_scope("交流能延期吗？")
    out = asyncio.run(graph.execute_tools({"question": "交流能延期吗？", "request_scope": scope,
                     "tool_calls": [{"name": "search_regulations", "arguments": {"query": "交流延期"}}]}))
    observation = out["observations"][0]
    assert "适用范围排除" in observation and "教职工进修管理办法" in observation
    assert out["calls"][0]["observation_sha256"] == hashlib.sha256(observation.encode()).hexdigest()
    assert out["messages"][-1]["content"] == observation
    assert graph.route_after_tools(out) == "decide"
    # The same executed call stays blocked, despite any coverage deficit.
    repeated = asyncio.run(graph.execute_tools(dict(out, tool_calls=[
        {"name": "search_regulations", "arguments": {"query": "交流延期"}}])))
    assert "未重复执行" in repeated["observations"][0]


def test_filtered_evidence_notes_survive_final_packet(monkeypatch):
    from agent.core import EvidenceLedger
    result = ToolResult(tool="search_regulations", ok=True, status="success",
                        data={"query": "延期", "coverage": {"scope_filtered": None}},
                        sources=[Source(title="教职工进修管理办法", text="教职工进修。")])
    ledger = EvidenceLedger()
    ledger.add_many(result.tool, {}, result.sources)
    packet = G.evidence_packet({"question": "可以延期吗？", "tool_results": [result],
                               "evidence": {item.evidence_id: item for item in ledger.items()}})
    assert packet["scope_filtered"] == 1 and packet["evidence"] == []
    assert packet["retrieval_notes"][0]["term_match"]["scope_filtered"] is None
