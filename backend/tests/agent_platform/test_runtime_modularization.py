"""拆分后的真实主图集成；实体、模型和 PPT 流使用明确的本地替身。"""
import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from entities import entity_resolver as ER
from agent.runtime import graph
from agent.tools.create_ppt import tool as ppt_tool


@pytest.fixture(autouse=True)
def local_dependencies(monkeypatch):
    resolver = ER.Resolver(
        {"entities": {kind: [] for kind in ER.ENTITY_TYPES}},
        catalog_sha256="0" * 64, aliases_sha256="0" * 64,
    )
    monkeypatch.setattr(ER, "build_production_resolver", lambda: resolver)

    async def forbidden(*args, **kwargs):
        pytest.fail("本地模块化检查不得调用模型 API")

    monkeypatch.setattr(graph.model, "decide", forbidden)
    monkeypatch.setattr(graph.model, "compose", forbidden)
    monkeypatch.setattr(graph.model, "answer", forbidden)


def drive(question):
    async def collect():
        return [event async for event in graph.run_stream([], question, owner_id=7)]
    return asyncio.run(collect())


def done(events):
    results = [payload for event, payload in events if event == "done"]
    assert len(results) == 1
    return results[0]


def test_greeting_has_one_final_event_without_tools(monkeypatch):
    async def decide(messages):
        return {"type": "text", "content": "你好"}
    monkeypatch.setattr(graph.model, "decide", decide)
    events = drive("你好")
    assert events[0] == ("delta", graph.grounded_answer.safe_non_fact("你好"))
    assert done(events)["calls"] == []


def test_tool_execution_feeds_paired_messages_to_next_round(monkeypatch):
    from .test_grounded_answers import draft_for

    async def decide(messages):
        return {"type": "tool_calls", "calls": [
            {"name": "query_empty_classroom", "arguments": {}}]}

    seen = []
    async def answer(messages, tool_choice="auto", **kwargs):
        seen.extend(messages)
        yield ("delta", "已取得查询入口。")

    async def compose(messages, packet, **kwargs):
        return draft_for(packet, "请通过教务系统查询空教室。",
                         [item["evidence_id"] for item in packet["evidence"]])

    monkeypatch.setattr(graph.model, "decide", decide)
    monkeypatch.setattr(graph.model, "answer", answer)
    monkeypatch.setattr(graph.model, "compose", compose)
    result = done(drive("查空教室"))
    assert [call["name"] for call in result["calls"]] == ["query_empty_classroom"]
    calls = [message for message in seen if message.get("tool_calls")]
    observations = [message for message in seen if message["role"] == "tool"]
    assert len(calls) == len(observations) == 1
    assert calls[0]["tool_calls"][0]["id"] == observations[0]["tool_call_id"]
    assert result["call_validations"][0]["decision"] == "allow"


def test_graduate_scope_stops_undergraduate_tool_before_execution(monkeypatch):
    async def decide(messages):
        return {"type": "tool_calls", "calls": [{"name": "query_major_requirements",
            "arguments": {"major": "人工智能"}}]}

    def forbidden(*args, **kwargs):
        pytest.fail("不适用的本科工具不得执行")

    monkeypatch.setattr(graph.model, "decide", decide)
    monkeypatch.setattr(graph.registry, "call", forbidden)
    result = done(drive("我是研究生，查询我的毕业学分要求"))
    assert result["calls"] == []
    assert result["call_validations"][0]["reason"] == "scope_mismatch:level"


def test_ppt_handoff_keeps_one_done_and_returns_subgraph_records(monkeypatch):
    async def decide(messages):
        return {"type": "tool_calls", "calls": [{"name": "create_ppt",
            "arguments": {"goal": "介绍校园", "audience": "新生"}}]}

    async def ppt_stream(history, question, **kwargs):
        assert kwargs["brief"]["audience"] == "新生"
        yield ("ppt", {"ppt_draft": {"task_id": "local-ppt"}})
        yield ("done", {"calls": [{"name": "local-research"}],
                        "contexts": [{"source": "本地替身材料"}]})

    monkeypatch.setattr(graph.model, "decide", decide)
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *args: "local-ppt")
    monkeypatch.setattr(ppt_tool, "run_stream", ppt_stream)
    events = drive("请制作校园介绍PPT")
    assert events[0] == ("ppt", {"ppt_draft": {"task_id": "local-ppt"}})
    result = done(events)
    assert result["calls"] == [{"name": "local-research"}]
    assert result["contexts"] == [{"source": "本地替身材料"}]
