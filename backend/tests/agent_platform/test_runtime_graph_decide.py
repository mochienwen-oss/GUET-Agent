# tests/agent_platform/test_runtime_graph_decide.py —— 决策节点与条件边（设计 §5）
#
# ★ 这里同时守**消息形状**：`assistant(tool_calls)` 的 `arguments` 必须是
#   `ensure_ascii=False` 的 JSON 字符串。LangChain 的 `ChatOpenAI` 会把它转义成
#   `\uXXXX`，token 序列就变了 —— 不报错、不抛异常、日志一切正常。
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime import graph  # noqa: E402


# ── 条件边 ──────────────────────────────────────────────────────────────


def test_text_reply_ends_the_turn():
    assert graph.route_after_decide({"tool_calls": [], "final_answer": "你好"}) == "answer"


def test_create_ppt_routes_to_the_handoff_not_back_to_decide():
    state = {"tool_calls": [{"name": "create_ppt",
                             "arguments": {"goal": "讲转专业", "audience": "大一"}}]}
    assert graph.route_after_decide(state) == "ppt"


def test_create_ppt_wins_even_when_other_tools_are_called_in_the_same_round():
    state = {"tool_calls": [{"name": "search_regulations", "arguments": {"query": "q"}},
                            {"name": "create_ppt", "arguments": {"goal": "g",
                                                                 "audience": "a"}}]}
    assert graph.route_after_decide(state) == "ppt"


def test_other_calls_go_through_validate_and_execute():
    state = {"tool_calls": [{"name": "search_regulations",
                             "arguments": {"query": "转专业"}}]}
    assert graph.route_after_decide(state) == "tools"


def test_empty_reply_asks_instead_of_returning_nothing():
    """模型既没喊工具、也没说出一句话 —— 绝不静默返回空。"""
    assert graph.route_after_decide({"tool_calls": [], "final_answer": ""}) == "clarify"
    assert graph.route_after_decide({"tool_calls": [], "final_answer": "   "}) == "clarify"
    assert graph.route_after_decide({}) == "clarify"


# ── 消息形状（冻结层）───────────────────────────────────────────────────


def test_round_messages_matches_the_trained_shape():
    calls = [{"name": "query_course",
              "arguments": {"major": "计算机科学与技术", "course": "数据库系统原理A"}}]
    messages = graph.round_messages(calls, ["【课程定位卡】…"], 0)

    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == ""
    call = messages[0]["tool_calls"][0]
    assert call["id"] == "call_0" and call["type"] == "function"
    assert call["function"]["name"] == "query_course"
    # ★ 中文**不得**被转义；且必须是字符串（不是 dict）
    assert isinstance(call["function"]["arguments"], str)
    assert "计算机科学与技术" in call["function"]["arguments"]
    assert "\\u" not in call["function"]["arguments"]
    assert json.loads(call["function"]["arguments"]) == calls[0]["arguments"]

    assert messages[1] == {"role": "tool", "tool_call_id": "call_0",
                           "content": "【课程定位卡】…"}


def test_round_messages_numbering_continues_across_rounds():
    first = graph.round_messages([{"name": "a", "arguments": {}}], ["o1"], 0)
    second = graph.round_messages([{"name": "b", "arguments": {}},
                                   {"name": "c", "arguments": {}}], ["o2", "o3"], 1)
    assert first[0]["tool_calls"][0]["id"] == "call_0"
    assert [c["id"] for c in second[0]["tool_calls"]] == ["call_1", "call_2"]
    assert [m["tool_call_id"] for m in second[1:]] == ["call_1", "call_2"]


def test_round_messages_refuses_a_call_without_an_observation():
    """数量对不上 vLLM 直接 400 —— 在拼消息这一层就拦住，别留给真机。"""
    import pytest
    with pytest.raises(ValueError, match="等长"):
        graph.round_messages([{"name": "a", "arguments": {}}], [], 0)


def test_init_messages_puts_system_history_and_question_in_order():
    history = [{"role": "user", "content": "上一问"},
               {"role": "assistant", "content": "上一答"}]
    messages = graph.init_messages(history, "本轮问")
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1] == {"role": "user", "content": "本轮问"}
    assert messages[1:3] == history


# ── 决策节点 ────────────────────────────────────────────────────────────


def test_agent_decide_counts_steps_and_reports_the_call(monkeypatch):
    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "search_regulations", "arguments": {"query": "q"}}]}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    state = {"history": [], "question": "转专业", "steps_used": 1}
    out = asyncio.run(graph.agent_decide(state))

    assert out["steps_used"] == 2
    assert out["tool_calls"] == [{"name": "search_regulations",
                                  "arguments": {"query": "q"}}]
    assert out["final_answer"] is None


def test_agent_decide_keeps_the_history_in_the_messages(monkeypatch):
    seen = {}

    async def fake_decide(messages):
        seen["messages"] = messages
        return {"type": "text", "content": "答"}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    state = {"history": [{"role": "user", "content": "上一问"}], "question": "本轮问"}
    out = asyncio.run(graph.agent_decide(state))

    assert out["final_answer"] is None
    assert out["tool_calls"][0]["name"] == "search_regulations"
    assert [m["role"] for m in seen["messages"]] == ["system", "user", "user"]
