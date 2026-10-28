# tests/agent_platform/test_runtime_graph_entry.py —— 主图编译与唯一入口
#
# ★ 走**真的** `run_stream`（真的自定义流、真的最终状态），只把模型与 PPT 子图
#   换成假实现 —— 这样验的是端到端的事件形状，不是某个节点自己说它发了什么。
import asyncio
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime import graph  # noqa: E402
from agent.tools.create_ppt import tool as ppt_tool  # noqa: E402
from core.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def final_draft_stub(monkeypatch):
    """Exercise the real final validator, without making live model requests."""
    async def final(messages, packet, **kwargs):
        unit_ids = [unit["unit_id"] for unit in packet.get("evidence_units") or []]
        slots = list((packet.get("question_brief") or {}).get("required_slots") or [])
        return {"status": "answer", "claims": [{
            "claim_id": "c1", "claim_type": "conclusion", "question_slot": "conclusion",
            "text": "已核实的答案。", "unit_ids": unit_ids,
            "supplement_class": "required"}],
            "slot_coverage": [{"slot": slot,
                               "status": "covered" if slot == "conclusion" else "not_applicable",
                               "claim_ids": ["c1"] if slot == "conclusion" else [],
                               "reason": "" if slot == "conclusion" else "test fixture"}
                              for slot in slots],
            "blocks": [{"claim_ids": ["c1"]}], "missing": []}
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", final)


def _drive(question="转专业有没有名额上限？", **kwargs):
    async def _go():
        return [item async for item in graph.run_stream([], question, **kwargs)]
    return asyncio.run(_go())


def _done(items):
    return [payload for event, payload in items if event == "done"][-1]


def _events(items):
    return [event for event, _ in items]


# ── 事件形状（六个既有 SSE 键的语义不变）────────────────────────────────


def test_no_tool_reply_emits_delta_then_one_done(monkeypatch):
    async def fake_decide(messages):
        return {"type": "text", "content": "你好呀，有什么可以帮你？"}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    items = _drive("你好")

    assert items[0] == ("delta", graph.grounded_answer.safe_non_fact("你好"))
    assert _events(items) == ["delta", "done"]
    done = _done(items)
    assert set(done) == {"contexts", "calls", "n_refs", "decision",
                         "call_validations", "answer_audit", "answer"}
    assert done["answer"]["version"] == "final-answer/1"
    assert done["answer"]["text"] == items[0][1]
    assert done["answer"]["contexts"] == done["contexts"]
    assert done["decision"] == "no_tool"
    assert done["contexts"] == [] and done["calls"] == []
    assert done["call_validations"] == []


def test_meta_question_does_not_force_retrieval(monkeypatch):
    async def fake_decide(messages):
        return {"type": "text", "content": "我是 SAI。"}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    items = _drive("你好，你是什么模型")

    assert _events(items) == ["delta", "done"]
    done = _done(items)
    assert done["decision"] == "no_tool"
    assert done["contexts"] == [] and done["calls"] == []
    assert done["answer"]["status"] == "answer"
    assert "SAI" in done["answer"]["text"]


def test_tool_round_emits_delta_done_and_a_tool_call_log(monkeypatch):
    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "query_empty_classroom", "arguments": {}}]}

    async def fake_answer(messages, tool_choice="auto"):
        yield ("delta", "空教室要去教务系统查，我这边给不了实时名单。")

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    items = _drive()

    assert _events(items) == ["delta", "done"]
    done = _done(items)
    assert done["decision"] == "tool_call"
    assert len(done["calls"]) == 1
    assert done["calls"][0]["name"] == "query_empty_classroom"
    assert done["contexts"]


def test_the_graph_emits_exactly_one_done(monkeypatch):
    async def fake_decide(messages):
        return {"type": "text", "content": "答"}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    assert _events(_drive("你好")).count("done") == 1


def test_empty_model_reply_asks_instead_of_returning_an_empty_answer(monkeypatch):
    async def fake_decide(messages):
        return {"type": "text", "content": ""}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    items = _drive()

    assert _events(items) == ["delta", "done"]
    assert "换个说法" in items[0][1]
    assert _done(items)["decision"] == "clarify"


# ── 循环与停止 ──────────────────────────────────────────────────────────


def test_model_can_ask_for_more_tools_after_seeing_observations(monkeypatch):
    """看过 observation 之后的流式轮若仍要工具，调用必须被执行、不能丢。"""
    rounds = []

    async def fake_decide(messages):
        rounds.append("decide")
        return {"type": "tool_calls",
                "calls": [{"name": "query_empty_classroom", "arguments": {}}]}

    async def fake_answer(messages, tool_choice="auto"):
        rounds.append("answer")
        if rounds.count("answer") == 1:
            yield ("calls", [{"name": "query_course",
                              "arguments": {"major": "计算机科学与技术",
                                            "course": "数据库系统原理A"}}])
        else:
            yield ("delta", "答完了。")

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    done = _done(_drive("计算机科学与技术专业的数据库系统原理A学分是多少？"))

    assert [c["name"] for c in done["calls"]] == ["query_empty_classroom", "query_course"]
    assert done["decision"] == "tool_call"


def test_duplicate_call_is_not_executed_twice(monkeypatch):
    same = {"name": "query_empty_classroom", "arguments": {}}
    seen = []

    async def fake_decide(messages):
        return {"type": "tool_calls", "calls": [dict(same)]}

    async def fake_answer(messages, tool_choice="auto"):
        seen.append(messages)
        if len(seen) == 1:
            yield ("calls", [dict(same)])          # 第 2 轮又喊同一个
        else:
            yield ("delta", "答。")

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    done = _done(_drive("计算机科学与技术专业的数据库系统原理A学分是多少？"))

    assert [c["name"] for c in done["calls"]] == ["query_empty_classroom"]   # 只执行一次
    # ★ 拦下的那次必须**看得见** —— 模型没看到「查过了」就会一直重发
    assert any("未重复执行" in (m.get("content") or "")
               for m in seen[-1] if m.get("role") == "tool")


def test_repeated_duplicates_stop_research_instead_of_burning_the_budget(monkeypatch):
    """★ 一直重发同一个调用 → 连续两轮没新增证据就停，不再空转到步数预算用尽。

    这条是审查抓出来的洞的回归测试：被拦下的重复调用**不产生 ToolResult**，
    所以「看末尾两条结果的 sources 空不空」那套判据永远看不见它。

    ★ 第一轮用的必须是**真会产生 Evidence 的工具**（`query_course`）——
    用真实课程工具产生证据，避免用空结果夹具漏测「连续两轮」界线。
    """
    same = {"name": "query_course",
            "arguments": {"major": "计算机科学与技术", "course": "数据库系统原理A"}}
    trail = []

    async def fake_decide(messages):
        trail.append("decide")
        return {"type": "tool_calls", "calls": [dict(same)]}

    async def fake_answer(messages, tool_choice="auto"):
        trail.append(tool_choice)
        if tool_choice == "none":
            yield ("delta", "收尾：没查到更多东西。")
            return
        yield ("calls", [dict(same)])

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    done = _done(_drive("计算机科学与技术专业的数据库系统原理A学分是多少？"))

    assert trail == ["decide", "auto", "auto"]  # 两轮后进入受证据约束的收尾，不再流式放行
    assert done["answer_audit"]["selected_evidence_ids"]
    assert [c["name"] for c in done["calls"]] == ["query_course"]   # 只执行一次


def test_budget_exhaustion_wraps_up_with_tool_choice_none(monkeypatch):
    choices = []

    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "query_empty_classroom", "arguments": {}}]}

    async def fake_answer(messages, tool_choice="auto"):
        choices.append(tool_choice)
        yield ("delta", "已拿到的资料支持这一部分；还缺 XX。")

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    monkeypatch.setattr(settings, "agent_max_tool_calls", 1)
    items = _drive()

    assert choices == []  # 直接进入结构化最终答案，不再调用旧的自由流式收尾
    assert items[0] == ("delta", "已核实的答案。")
    assert _done(items)["answer_audit"]["selected_evidence_ids"]
    assert _done(items)["decision"] == "tool_call"


def test_two_empty_rounds_reach_wrap_up(monkeypatch):
    """连续两次工具调用没拿到新证据 → 收尾，而不是继续空转。"""
    calls = []

    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "query_empty_classroom", "arguments": {}}]}

    async def fake_answer(messages, tool_choice="auto"):
        calls.append(messages)
        if tool_choice == "none":
            yield ("delta", "库里没有这个专业的资料。")
            return
        yield ("calls", [{"name": "query_empty_classroom", "arguments": {}}])

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    items = _drive()

    assert _events(items)[0] == "delta"
    assert items[0][1] == "已核实的答案。"
    assert _done(items)["answer_audit"]["selected_evidence_ids"]


# ── PPT 交接 ────────────────────────────────────────────────────────────


def _fake_ppt():
    async def _s(history, question, **kwargs):
        yield ("ppt", {"ppt_task": {"task_id": "t1", "stage": "plan"}})
        yield ("done", {"contexts": [], "calls": [], "n_refs": 0})
    return _s


def test_model_chosen_create_ppt_hands_off_and_the_turn_ends(monkeypatch):
    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "create_ppt",
                           "arguments": {"goal": "讲转专业", "audience": "大一新生"}}]}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda owner, conv: "t1")
    monkeypatch.setattr(ppt_tool, "run_stream", _fake_ppt())
    items = _drive("帮我做个讲转专业的 PPT")

    assert _events(items) == ["ppt", "done"]
    assert items[0][1]["ppt_task"]["task_id"] == "t1"
    assert _done(items)["decision"] == "ppt"


def test_create_ppt_with_a_missing_brief_asks_and_never_starts_the_subgraph(monkeypatch):
    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "create_ppt", "arguments": {"goal": "讲转专业"}}]}

    async def _boom(*_a, **_k):
        raise AssertionError("缺参不该启动子图")

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(ppt_tool, "reserve_task", _boom)
    monkeypatch.setattr(ppt_tool, "run_stream", _boom)
    items = _drive()

    assert _events(items) == ["delta", "done"]
    assert "讲给谁听" in items[0][1]
    assert _done(items)["decision"] == "clarify"


def test_explicit_resume_goes_straight_to_the_subgraph(monkeypatch):
    """用户点「继续制作 PPT」—— 显式信号，与关键词无关，不再过 agent_decide。"""

    async def _boom(messages):
        raise AssertionError("续接不该再问模型")

    monkeypatch.setattr(graph.model, "decide", _boom)
    monkeypatch.setattr(ppt_tool, "run_stream", _fake_ppt())
    items = _drive("大一新生", ppt_task_id="t1", resume_stage="clarify",
                   conversation_id=2, owner_id=7)

    assert _events(items) == ["ppt", "done"]
    assert _done(items)["decision"] == "ppt"
