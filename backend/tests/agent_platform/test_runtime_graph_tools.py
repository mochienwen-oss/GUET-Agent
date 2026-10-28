# tests/agent_platform/test_runtime_graph_tools.py —— 执行节点与 Evidence 台账
#
# ★ 全部走**纯函数工具**（query_course / compare_course / query_empty_classroom），
#   不碰 search_regulations（那要连 Qdrant）。
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import ToolError, ToolResult  # noqa: E402
from agent.runtime import graph  # noqa: E402
from core.config import settings  # noqa: E402

_CARD = {"machine": {"major": "计算机科学与技术", "course": "数据库系统原理A"}}
_CARD_ARGS = {"major": "计算机科学与技术", "course": "数据库系统原理A"}


def _run(state):
    return asyncio.run(graph.execute_tools(state))


# ── 闸门 ①：每轮上限 ────────────────────────────────────────────────────


def test_valid_calls_are_not_truncated_to_old_training_round_limit(monkeypatch):
    calls = [{"name": "query_course",
              "arguments": {"major": "计算机科学与技术", "course": "课%d" % i}}
             for i in range(5)]
    monkeypatch.setattr(graph.registry, 'call', lambda name, args, **kw: ToolResult(
        tool=name, ok=True, status='success', data={}))
    monkeypatch.setattr(graph.registry, 'render', lambda result: '测试工具执行完成')
    out = _run({"validated_calls": calls})
    assert len(out["round_calls"]) == len(calls) == 5
    assert len(out["observations"]) == 5
    assert out['tool_calls_used'] == 5


def test_all_calls_receive_observations_when_total_budget_is_exhausted(monkeypatch):
    calls = [{'name': 'query_course', 'arguments': {'major': '测试专业', 'course': f'课程{i}'}}
             for i in range(settings.agent_max_tool_calls + 2)]
    executed = []
    def call(name, args, **kwargs):
        executed.append(args)
        return ToolResult(tool=name, ok=True, status='success', data={})
    monkeypatch.setattr(graph.registry, 'call', call)
    monkeypatch.setattr(graph.registry, 'render', lambda result: '测试工具执行完成')
    out = _run({'validated_calls': calls})
    assert len(executed) == out['tool_calls_used'] == settings.agent_max_tool_calls
    assert len(out['round_calls']) == len(out['observations']) == len(calls)
    assert all('未执行' in text for text in out['observations'][settings.agent_max_tool_calls:])


# ── 闸门 ②：同参不重跑 ─────────────────────────────────────────────────


def test_duplicate_call_is_blocked_and_does_not_cost_budget():
    state = {"tool_calls": [{"name": "query_course", "arguments": dict(_CARD_ARGS)}],
             "executed": [{"name": "query_course", "args": dict(_CARD_ARGS)}]}
    out = _run(state)

    assert out["tool_results"] == []                 # 没执行 → 不产生新证据
    assert out["tool_calls_used"] == 0               # 也不占额度
    assert "未重复执行" in out["observations"][0]


def test_duplicates_inside_one_round_are_also_blocked():
    """★ 同一轮里发三遍同一个调用，只能执行一遍。

    只查跨轮的账（`executed`）会三遍全跑 —— 审查实测过这个洞。
    """
    same = {"name": "query_empty_classroom", "arguments": {}}
    out = _run({"tool_calls": [dict(same), dict(same), dict(same)]})

    assert out["tool_calls_used"] == 1
    assert "未重复执行" in out["observations"][1]
    assert "未重复执行" in out["observations"][2]


def test_duplicate_is_judged_by_args_not_by_tool_name():
    state = {"tool_calls": [{"name": "query_course",
                             "arguments": {"major": "计算机科学与技术",
                                           "course": "数据库系统原理"}}],
             "executed": [{"name": "query_course", "args": dict(_CARD_ARGS)}]}
    out = _run(state)
    assert out["tool_calls_used"] == 1               # 参数不同 → 照常执行


def test_blocked_call_still_gets_exactly_one_observation():
    """数量对不上 vLLM 直接 400 —— 拦下的调用也必须配一条 observation。"""
    state = {"tool_calls": [{"name": "query_empty_classroom", "arguments": {}},
                            {"name": "query_empty_classroom", "arguments": {}}],
             "executed": [{"name": "query_empty_classroom", "args": {}}]}
    out = _run(state)
    assert len(out["observations"]) == len(out["round_calls"]) == 2


# ── 闸门 ③：总量预算 ────────────────────────────────────────────────────


def test_calls_beyond_the_total_budget_are_not_executed():
    calls = [{"name": "query_course",
              "arguments": {"major": "计算机科学与技术", "course": course}}
             for course in ("数据库系统原理A", "数据库系统原理实验", "操作系统")]
    state = {"tool_calls": calls, "tool_calls_used": settings.agent_max_tool_calls - 1}
    out = _run(state)
    assert out["tool_calls_used"] == settings.agent_max_tool_calls
    assert "额度已经用完" in out["observations"][1]
    assert "额度已经用完" in out["observations"][2]


# ── 失败语义 ────────────────────────────────────────────────────────────


def test_unknown_tool_renders_the_failure_boundary_not_no_evidence():
    out = _run({"tool_calls": [{"name": "no_such_tool", "arguments": {}}]})
    text = out["observations"][0]
    assert "调用未成功" in text
    assert "未检索到" not in text          # ★ 技术故障**不得**说成「查不到」


def test_invalid_args_is_not_retried_with_the_same_args(monkeypatch):
    """参数校验失败不得用原参数重试（设计 §5.3）。"""
    seen = []
    real_call = graph.registry.call

    def spy(name, args=None):
        seen.append((name, args))
        return real_call(name, args)

    monkeypatch.setattr(graph.registry, "call", spy)
    # compare_course 的冻结 schema 里 majors 必填 → invalid_args
    out = _run({"tool_calls": [{"name": "compare_course",
                                "arguments": {"course": "交通工程学"}}]})

    assert len(seen) == 1
    assert out["retries_used"] == 0
    assert out["calls"][0]["status"] == "tool_failed"
    assert out["calls"][0]["retryable"] is False
    assert "调用未成功" in out["observations"][0]


def test_recoverable_failure_retries_exactly_once(monkeypatch):
    attempts = []
    real_call = graph.registry.call

    def flaky(name, args=None):
        attempts.append(name)
        if len(attempts) == 1:
            return ToolResult(tool=name, ok=False, status="tool_failed",
                              error=ToolError(code="tool_failed", detail="连接超时",
                                              retryable=True))
        return real_call(name, args)          # 第二次真跑，拿到真的指针卡

    monkeypatch.setattr(graph.registry, "call", flaky)
    out = _run({"tool_calls": [{"name": "query_empty_classroom", "arguments": {}}]})

    assert len(attempts) == 2
    assert out["retries_used"] == 1
    assert out["tool_calls_used"] == 1      # 重试算**同一次**逻辑调用，不占 6 次额度
    assert "【空教室查询】" in out["observations"][0]


def test_recoverable_failure_gives_up_after_the_retry(monkeypatch):
    def always_down(name, args=None):
        return ToolResult(tool=name, ok=False, status="tool_failed",
                          error=ToolError(code="tool_failed", detail="连接超时",
                                          retryable=True))

    monkeypatch.setattr(graph.registry, "call", always_down)
    out = _run({"tool_calls": [{"name": "query_empty_classroom", "arguments": {}}]})
    assert out["retries_used"] == 1
    assert "调用未成功" in out["observations"][0]


# ── 调用日志与 contexts ─────────────────────────────────────────────────


def test_call_log_keeps_the_five_keys_and_hits():
    out = _run({"tool_calls": [{"name": "query_course", "arguments": dict(_CARD_ARGS)}]})
    entry = out["calls"][0]
    assert set(entry) >= {"name", "args", "ms", "hits", "status"}
    assert entry["hits"] > 0 and entry["status"] == "success"
    assert isinstance(entry["ms"], int)


def test_contexts_carry_the_retrieved_sources():
    out = _run({"tool_calls": [{"name": "query_course", "arguments": dict(_CARD_ARGS)}]})
    assert out["contexts"]
    assert out["contexts"][0]["source"]


# ── Evidence 台账 ───────────────────────────────────────────────────────


def test_append_evidence_is_built_from_sources_not_from_observation_text():
    """★ 设计 §4.2：Evidence 只能从 `ToolResult.sources` 构造。

    这条结果**没有任何 sources**，但它的 observation 里写着《某文件》。
    台账必须是空的 —— 从文本反解析出来的「来源」正是设计禁止的东西。
    """
    fake = ToolResult(tool="query_course", ok=True, status="no_evidence", sources=[])
    out = graph.append_evidence({"tool_results": [fake]})
    assert out["evidence"] == {}
    assert out["evidence_added"] == 0


def test_append_evidence_dedupes_and_accumulates_across_rounds():
    first = _run({"tool_calls": [{"name": "query_course", "arguments": dict(_CARD_ARGS)}]})
    state = {"tool_results": first["tool_results"]}
    one = graph.append_evidence(state)
    assert one["evidence_added"] == len(first["tool_results"])

    state2 = {"tool_results": first["tool_results"], "evidence": one["evidence"]}
    two = graph.append_evidence(state2)
    assert two["evidence_added"] == 0                    # 同一份来源不重复落账
    assert two["evidence"].keys() == one["evidence"].keys()
