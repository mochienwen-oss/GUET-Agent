# tests/agent_platform/test_runtime_budgets.py —— 主图的预算与停止规则（设计 §5.3）
#
# ★ 这几条是「超预算失控 0%」与「重复同参调用率 0%」两道门槛的**唯一落点**，
#   而它们都是纯函数 —— 用假 state 就能把边界钉死，不必起真机。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import ToolError, ToolResult  # noqa: E402
from agent.runtime import budgets  # noqa: E402
from core.config import settings  # noqa: E402


def test_budget_exhausted_reports_steps_then_tool_calls():
    assert budgets.budget_exhausted({"steps_used": 3, "tool_calls_used": 0}) is None
    assert budgets.budget_exhausted({"steps_used": 4, "tool_calls_used": 0}) == "steps"
    assert budgets.budget_exhausted({"steps_used": 1, "tool_calls_used": 6}) == "tool_calls"


def test_remaining_tool_calls_never_goes_negative():
    assert budgets.remaining_tool_calls({}) == settings.agent_max_tool_calls
    assert budgets.remaining_tool_calls({"tool_calls_used": 6}) == 0
    assert budgets.remaining_tool_calls({"tool_calls_used": 99}) == 0


def test_same_tool_same_args_is_a_duplicate_but_different_args_is_not():
    state = {"executed": [{"name": "query_course",
                           "args": {"major": "A", "course": "B"}}]}
    assert budgets.is_duplicate_call(
        state, "query_course", {"major": "A", "course": "B"}) is True
    assert budgets.is_duplicate_call(
        state, "query_course", {"major": "A", "course": "C"}) is False
    assert budgets.is_duplicate_call(
        state, "compare_course", {"major": "A", "course": "B"}) is False


def test_duplicate_detection_ignores_argument_key_order():
    """键序不同、内容相同 → 仍是同一个调用（`args_hash` 排过序）。"""
    state = {"executed": [{"name": "query_course",
                           "args": {"major": "A", "course": "B"}}]}
    assert budgets.is_duplicate_call(
        state, "query_course", {"course": "B", "major": "A"}) is True


def test_two_consecutive_rounds_without_new_evidence_stop_research():
    """判据是**台账有没有长**，不是「结果里 sources 是不是空」。

    后者看不见两种真实的「没有新增」：被拦下的重复调用根本不产生 ToolResult；
    「查到了、但同样的来源台账里已经有了」sources 也非空。
    """
    assert budgets.should_stop_research({"no_new_evidence_rounds": 0}) is False
    assert budgets.should_stop_research({"no_new_evidence_rounds": 1}) is False
    assert budgets.should_stop_research({"no_new_evidence_rounds": 2}) is True


def test_call_key_is_order_insensitive_and_tool_scoped():
    assert budgets.call_key("query_course", {"major": "A", "course": "B"}) == \
           budgets.call_key("query_course", {"course": "B", "major": "A"})
    assert budgets.call_key("query_course", {"major": "A"}) != \
           budgets.call_key("compare_course", {"major": "A"})


def test_invalid_args_is_never_retried():
    """参数校验失败不得用原参数重试（设计 §5.3）—— 原样再发一次结果不会变。"""
    failed = ToolResult(tool="compare_course", ok=False, status="tool_failed",
                        error=ToolError(code="invalid_args", detail="majors: 必填",
                                        retryable=False))
    assert budgets.can_retry(failed, 0) is False


def test_recoverable_failure_retries_once_then_stops():
    failed = ToolResult(tool="search_regulations", ok=False, status="tool_failed",
                        error=ToolError(code="tool_failed", detail="连接超时",
                                        retryable=True))
    assert budgets.can_retry(failed, 0) is True
    assert budgets.can_retry(failed, settings.agent_max_retries) is False


def test_can_retry_needs_a_retryable_error():
    """没失败过（或失败但没标可重试）→ 不重试，否则会凭空多跑一次。"""
    assert budgets.can_retry(ToolResult(tool="x", ok=True), 0) is False
    no_error = ToolResult(tool="x", ok=False, status="tool_failed")
    assert budgets.can_retry(no_error, 0) is False