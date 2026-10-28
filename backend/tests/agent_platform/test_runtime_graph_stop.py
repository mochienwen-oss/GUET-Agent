# tests/agent_platform/test_runtime_graph_stop.py —— 停止规则与收尾（设计 §5.3）
#
# ★ 收尾**不是**第二次兜底问答：它走同一条流式路径，`tool_choice="none"`，
#   不加任何消息。「不能再调工具了」这个信号由 tool_choice 传达。
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import ToolResult, Source, EvidenceLedger  # noqa: E402
from agent.runtime import graph  # noqa: E402
from core.config import settings  # noqa: E402


def _empty():
    return ToolResult(tool="search_regulations", ok=True, status="no_evidence")


def _filled():
    return ToolResult(tool="search_regulations", ok=True, status="success",
                      sources=[object()])


def _fake_stream(text="已拿到的资料支持这一部分；还缺 XX。", calls=None, seen=None):
    async def _s(messages, tool_choice="auto"):
        if seen is not None:
            seen.append((messages, tool_choice))
        for chunk in ([text] if text else []):
            yield ("delta", chunk)
        if calls:
            yield ("calls", calls)
    return _s


def _evidence():
    ledger = EvidenceLedger()
    item = ledger.add("search_regulations", {"query": "q"}, Source(title="测试规定", text="有据的正文"))
    return {item.evidence_id: item}


def _draft_for(packet, text):
    unit_ids = [unit["unit_id"] for unit in packet.get("evidence_units") or []]
    slots = list((packet.get("question_brief") or {}).get("required_slots") or [])
    return {"status": "answer", "claims": [{
        "claim_id": "c1", "claim_type": "conclusion", "question_slot": "conclusion",
        "text": text, "unit_ids": unit_ids,
        "supplement_class": "required"}],
        "slot_coverage": [{"slot": slot,
                           "status": "covered" if slot == "conclusion" else "not_applicable",
                           "claim_ids": ["c1"] if slot == "conclusion" else [],
                           "reason": "" if slot == "conclusion" else "test fixture"}
                          for slot in slots],
        "blocks": [{"claim_ids": ["c1"]}], "missing": []}


def _fake_draft(text, seen=None):
    async def final(messages, packet, **kwargs):
        if seen is not None:
            seen.append((messages, "evidence_draft"))
        return _draft_for(packet, text)
    return final


# ── 路由 ────────────────────────────────────────────────────────────────


def test_route_after_tools_keeps_going_while_there_is_budget():
    state = {"steps_used": 1, "tool_calls_used": 1, "tool_results": [_filled()]}
    assert graph.route_after_tools(state) == "decide"


def test_route_after_tools_wraps_up_on_step_budget():
    state = {"steps_used": settings.agent_max_steps, "tool_calls_used": 1,
             "tool_results": [_filled()]}
    assert graph.route_after_tools(state) == "wrap_up"


def test_route_after_tools_wraps_up_on_tool_call_budget():
    state = {"steps_used": 1, "tool_calls_used": settings.agent_max_tool_calls,
             "tool_results": [_filled()]}
    assert graph.route_after_tools(state) == "wrap_up"


def test_route_after_tools_wraps_up_after_two_rounds_without_new_evidence():
    state = {"steps_used": 2, "tool_calls_used": 2, "no_new_evidence_rounds": 2}
    assert graph.route_after_tools(state) == "wrap_up"


def test_route_after_tools_does_not_stop_after_a_round_that_found_something():
    state = {"steps_used": 2, "tool_calls_used": 2, "no_new_evidence_rounds": 0}
    assert graph.route_after_tools(state) == "decide"


# ── 收尾 ────────────────────────────────────────────────────────────────


def test_wrap_up_streams_with_tool_choice_none_and_adds_no_messages(monkeypatch):
    seen = []
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence",
                        _fake_draft("只有一部分有据；缺：补助标准。", seen=seen))
    emitted = []
    state = {"history": [], "question": "转专业有没有名额上限？",
             "messages": graph.init_messages([], "转专业有没有名额上限？"),
             "tool_results": [_filled()], "steps_used": settings.agent_max_steps,
             "evidence": _evidence()}

    out = asyncio.run(graph.wrap_up(state, emit=emitted.append))

    assert out["final_answer"] == "只有一部分有据；缺：补助标准。"
    assert out["tool_calls"] == []
    messages, tool_choice = seen[0]
    assert tool_choice == "evidence_draft"
    assert out["answer_audit"]["selected_evidence_ids"]
    # ★ 没有往里塞「请收尾」之类的合成轮 —— 轨迹必须仍是可观察的那三种
    assert [m["role"] for m in messages] == ["system", "user"]
    assert [ev for ev, _ in emitted] == ["delta"]


def test_wrap_up_uses_the_round_messages_when_they_exist(monkeypatch):
    """收尾时模型必须看得到已经拿到的 observation，否则只能凭空收尾。"""
    seen = []
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", _fake_draft("收尾", seen=seen))
    messages = graph.init_messages([], "问")
    messages += graph.round_messages([{"name": "query_course", "arguments": {}}],
                                     ["【课程定位卡】…"], 0)

    asyncio.run(graph.wrap_up({"messages": messages, "evidence": _evidence()}, emit=None))

    assert [m["role"] for m in seen[0][0]] == ["system", "user", "assistant", "tool"]


def test_wrap_up_emits_nothing_when_there_is_no_emit_hook(monkeypatch):
    """直调（单测）时不装 emit 钩子 —— 没有 try/except 兜底，是显式的 None。"""
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", _fake_draft("收尾"))
    out = asyncio.run(graph.wrap_up({"messages": graph.init_messages([], "问"), "evidence": _evidence()}))
    assert out["final_answer"] == "收尾"


def test_wrap_up_never_leaves_an_empty_answer(monkeypatch):
    """★ 收尾那轮什么都没说出来时也不许空答（设计 §5.3）。"""
    monkeypatch.setattr(graph.model, "answer", _fake_stream(""))
    emitted = []
    out = asyncio.run(graph.wrap_up(
        {"messages": graph.init_messages([], "问")}, emit=emitted.append))

    assert out["final_answer"].strip()
    assert emitted and emitted[0][0] == "delta"


# ── 决策轮的流式分支 ────────────────────────────────────────────────────


def test_first_round_is_non_streaming_and_effective(monkeypatch):
    """第 1 轮仍走 `chat_with_tools` —— 与迁移前逐字同形。"""
    seen = []

    async def fake_decide(messages):
        seen.append(messages)
        return {"type": "tool_calls",
                "calls": [{"name": "query_empty_classroom", "arguments": {}}]}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    out = asyncio.run(graph.agent_decide({"history": [], "question": "问"}))

    assert len(seen) == 1
    assert out["tool_calls"][0]["name"] == "query_empty_classroom"


def test_later_rounds_stream_and_can_still_ask_for_tools(monkeypatch):
    """看过 observation 之后走流式；模型若仍要工具，调用**不能丢**。

    迁移前那一步是拿 `tool_choice="none"` 再问一次，等于把调用丢掉。
    """
    seen = []
    monkeypatch.setattr(graph.model, "answer",
                        _fake_stream("", calls=[{"name": "query_course",
                                                 "arguments": {"major": "A",
                                                               "course": "B"}}], seen=seen))
    out = asyncio.run(graph.agent_decide(
        {"history": [], "question": "问", "rounds_done": 1, "steps_used": 1}))

    assert seen[0][1] == "auto"
    assert out["tool_calls"] == [{"name": "query_course",
                                  "arguments": {"major": "A", "course": "B"}}]
    assert out["final_answer"] is None


def test_later_rounds_do_not_emit_unverified_text_when_the_model_wrote_something(monkeypatch):
    """边写边喊的文本也必须经过证据出口，不能因已生成文本而绕过。"""
    monkeypatch.setattr(graph.model, "answer",
                        _fake_stream("答案就在这句话里",
                                     calls=[{"name": "query_course", "arguments": {}}]))
    out = asyncio.run(graph.agent_decide({"history": [], "question": "问",
                                          "rounds_done": 1}))
    assert out["final_answer"] == graph.grounded_answer.NO_EVIDENCE
    assert out["tool_calls"] == []
