# tests/agent_platform/test_runtime_graph_ppt.py —— create_ppt 的模型驱动交接（设计 §6.1）
#
# ★ 子图一律**注入假流**：真子图要连 vLLM 起草幻灯片，那是阶段9 的事。
#   这里验的是交接**决策**：什么时候追问、什么时候建任务、事件怎么转发、会不会回流。
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime import graph  # noqa: E402
from agent.tools.create_ppt import tool as ppt_tool  # noqa: E402


def _fake_ppt(counter=None, events=None):
    async def _s(history, question, **kwargs):
        if counter is not None:
            counter.append(kwargs)
        for event in (events or [("ppt", {"ppt_task": {"task_id": "t1", "stage": "plan"}})]):
            yield event
        yield ("done", {"contexts": [{"source": "培养方案"}], "calls": [
            {"name": "search_regulations", "args": {"query": "q"}, "ms": 1,
             "hits": 2, "status": "success"}], "n_refs": 1})
    return _s


def _state(args):
    return {"history": [], "question": "帮我做个讲转专业的 PPT",
            "owner_id": 7, "conversation_id": 42,
            "tool_calls": [{"name": "create_ppt", "arguments": args}]}


# ── 缺参 → 追问，不建任务也不启动子图 ───────────────────────────────────


def test_missing_audience_asks_instead_of_starting_the_subgraph(monkeypatch):
    started, reserved = [], []
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: reserved.append(a) or "t9")
    emitted = []

    out = asyncio.run(graph.ppt_handoff(
        _state({"goal": "讲转专业的条件与流程"}),
        emit=emitted.append, ppt_stream=_fake_ppt(started)))

    assert started == []                       # ★ 子图一次都没被调用
    assert reserved == []                      # ★ 也没建任务
    assert out["decision"] == "clarify"
    assert "讲给谁听" in out["final_answer"]
    assert out["pending_clarification"]["missing"] == ["讲给谁听"]
    assert [ev for ev, _ in emitted] == ["delta"]


def test_blank_goal_is_treated_as_missing(monkeypatch):
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t9")
    out = asyncio.run(graph.ppt_handoff(_state({"goal": "   ", "audience": "大一新生"}),
                                        ppt_stream=_fake_ppt()))
    assert out["decision"] == "clarify"
    assert "这份 PPT 要讲什么" in out["final_answer"]


def test_both_missing_asks_for_both(monkeypatch):
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t9")
    out = asyncio.run(graph.ppt_handoff(_state({}), ppt_stream=_fake_ppt()))
    assert "这份 PPT 要讲什么" in out["final_answer"]
    assert "讲给谁听" in out["final_answer"]


# ── 参数齐 → 建任务 + 转发事件 ──────────────────────────────────────────


def test_valid_handoff_reserves_one_task_and_forwards_ppt_events(monkeypatch):
    started, reserved = [], []
    monkeypatch.setattr(ppt_tool, "reserve_task",
                        lambda owner, conv: reserved.append((owner, conv)) or "t1")
    emitted = []

    out = asyncio.run(graph.ppt_handoff(
        _state({"goal": "讲转专业", "audience": "大一新生"}),
        emit=emitted.append,
        ppt_stream=_fake_ppt(started, events=[
            ("ppt", {"ppt_task": {"task_id": "t1", "stage": "plan"}}),
            ("ppt", {"ppt_draft": {"task_id": "t1", "stage": "draft", "pages": 5}}),
        ])))

    assert reserved == [(7, 42)]                   # 恰好一次，且带对归属
    assert out["task_id"] == "t1"
    assert out["decision"] == "ppt"
    assert [ev for ev, _ in emitted] == ["ppt", "ppt"]
    assert out["calls"][0]["name"] == "search_regulations"
    assert out["contexts"] == [{"source": "培养方案"}]


def test_subgraph_history_question_and_evidence_are_passed_through(monkeypatch):
    seen = []
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t1")
    monkeypatch.setattr(ppt_tool, "run_stream", _fake_ppt(seen))
    state = _state({"goal": "g", "audience": "a"})
    state["evidence"] = {"e1": object()}
    state["history"] = [{"role": "user", "content": "上一问"}]

    asyncio.run(graph.ppt_handoff(state, emit=None, ppt_stream=None))

    kwargs = seen[0]
    assert kwargs["owner_id"] == 7 and kwargs["conversation_id"] == 42
    assert kwargs["resume_stage"] is None
    assert kwargs["evidence"] == state["evidence"]


def test_the_subgraphs_done_event_is_not_forwarded(monkeypatch):
    """主图只有一条 `done` —— 子图的 done 转发出去，前端会收到两个收尾信号。"""
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t1")
    emitted = []
    asyncio.run(graph.ppt_handoff(_state({"goal": "g", "audience": "a"}),
                                  emit=emitted.append, ppt_stream=_fake_ppt()))
    assert "done" not in [ev for ev, _ in emitted]


# ── 显式续接（与关键词无关）─────────────────────────────────────────────


def test_resume_goes_straight_to_the_subgraph_without_validation(monkeypatch):
    seen = []
    monkeypatch.setattr(ppt_tool, "run_stream", _fake_ppt(seen))

    async def _boom(*_a):
        raise AssertionError("续接不该再去建任务")

    monkeypatch.setattr(ppt_tool, "reserve_task", _boom)
    out = asyncio.run(graph.ppt_handoff(
        {"history": [], "question": "大一新生", "task_id": "t1",
         "resume_stage": "clarify", "owner_id": 7, "conversation_id": 42,
         "tool_calls": []}, emit=None, ppt_stream=None))

    assert seen[0]["task_id"] == "t1"
    assert seen[0]["resume_stage"] == "clarify"
    assert out["decision"] == "ppt"


def test_sibling_calls_are_logged_as_skipped_not_silently_dropped(monkeypatch):
    """★ 一轮里同时选了别的工具与 create_ppt：那些调用不会执行（§5.3 不交叉循环），
    但**绝不静默丢** —— 记进调用日志，让「这轮它还想查什么」看得见。"""
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t1")
    state = {"history": [], "question": "做个 PPT，顺带查下转专业",
             "owner_id": 7, "conversation_id": 42,
             "tool_calls": [{"name": "search_regulations",
                             "arguments": {"query": "转专业"}},
                            {"name": "create_ppt",
                             "arguments": {"goal": "g", "audience": "a"}}]}
    out = asyncio.run(graph.ppt_handoff(state, ppt_stream=_fake_ppt()))

    skipped = [c for c in out["calls"] if c["status"] == "skipped_ppt_handoff"]
    assert [c["name"] for c in skipped] == ["search_regulations"]
    assert skipped[0]["args"] == {"query": "转专业"}


def test_existing_call_log_entries_appear_exactly_once(monkeypatch):
    """★ 主图已有记录 + 本轮跳过的兄弟 + 子图自己的 —— 各出现**一次**。

    复审实测过把 `state["calls"]` 计两遍的版本：日志变成
    `['search','compare','search','search']`，于是「这轮它还想查什么」
    这份证据本身就不可信了 —— 而它正是 #5 要守的东西。
    """
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t1")
    state = {"history": [], "question": "做个 PPT", "owner_id": 7,
             "conversation_id": 42,
             "calls": [{"name": "search_regulations", "args": {"query": "前一轮查的"},
                        "ms": 3, "hits": 2, "status": "success"}],
             "tool_calls": [{"name": "compare_course", "arguments": {"course": "X"}},
                            {"name": "create_ppt",
                             "arguments": {"goal": "g", "audience": "a"}}]}
    out = asyncio.run(graph.ppt_handoff(state, ppt_stream=_fake_ppt()))

    # 主图已有的那条**逐字只出现一次**（子图自己也回了一条同名、但 args 不同的记录）
    pre = [c for c in out["calls"] if c["args"] == {"query": "前一轮查的"}]
    assert len(pre) == 1
    assert [c["name"] for c in out["calls"]] == \
        ["search_regulations", "compare_course", "search_regulations"]
    assert [c["name"] for c in out["calls"] if c["status"] == "skipped_ppt_handoff"] \
        == ["compare_course"]


def test_handoff_is_terminal_and_leaves_nothing_to_loop_on(monkeypatch):
    """交出去之后**没有** tool_calls 留下来 —— 主图没有可以回流的理由。"""
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda *a: "t1")
    out = asyncio.run(graph.ppt_handoff(_state({"goal": "g", "audience": "a"}),
                                        ppt_stream=_fake_ppt()))
    assert out["tool_calls"] == []
    assert out["decision"] == "ppt"