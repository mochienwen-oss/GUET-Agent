# tests/graph/test_ppt_subgraph.py —— PPT 子图：澄清、研究循环上限、共享台账
#
# ★ 阶段3 的子图：图结构 / 路由 / 预算 / 循环 / 落盘都是真的；
#   `plan` 是确定性占位（真机上的 LLM planner 属阶段6），`author` 是**桩**（阶段4）。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import pytest

from agent.core import evidence as ev  # noqa: E402
from agent.core.contracts import Source, ToolResult  # noqa: E402
from core.config import settings  # noqa: E402
from agent.tools.create_ppt.scripts.execution import subgraph  # noqa: E402
from agent.tools.create_ppt.scripts.execution.subgraph import build_ppt_subgraph  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))


def _src(i):
    t = "正文%d" % i
    return Source(title="某文件%d" % i, text=t, text_hash=ev.text_hash(t))


def _call(hits=1):
    def f(name, args):
        return ToolResult(tool=name, ok=True, data={},
                          sources=[_src(i) for i in range(hits)])
    return f


def _empty_call(name, args):
    return ToolResult(tool=name, ok=True, data={}, sources=[])


def _run(init, **kw):
    app = build_ppt_subgraph(**kw)
    last = {}
    for chunk in app.stream(init, stream_mode="values"):
        last = chunk
    return last


def _base(**over):
    st = {"task_id": "t1", "request_raw": "做个讲转专业的 PPT",
          "brief": {"goal": "讲转专业", "audience": "大一新生", "length": 10},
          "tool_calls": [], "evidence": {}, "coverage": {}, "errors": [],
          "budget": {"research_steps_left": 3, "tool_calls_left": 5, "repair_left": 2}}
    st.update(over)
    return st


# ── 澄清 ────────────────────────────────────────────────────────────
def test_missing_audience_asks_and_does_not_research():
    """★ 缺**会改变内容或检索方向**的关键项 → 只反问，**一次问全**，且**不往下跑**。"""
    st = _base(brief={"goal": "讲转专业"})          # 缺 audience
    last = _run(st, tool_call=_call())
    assert last["stage"] == "await_clarify"
    assert last["pending_user"]["missing"] == ["audience"]
    assert last["tool_calls"] == []                  # 一次检索都没做
    assert not last.get("outline")                   # 没走到大纲


def test_complete_brief_skips_clarify():
    last = _run(_base(), tool_call=_call())
    assert last.get("pending_user") is None
    assert last["stage"] == "author_stub"


def test_length_has_a_default_and_does_not_block():
    """篇幅是**非关键偏好** → 走默认值，**不打断用户**。"""
    st = _base(request_raw="做个讲转专业的 PPT", brief={"goal": "讲转专业", "audience": "新生"})
    last = _run(st, tool_call=_call())
    assert last["brief"]["length"] is not None


# ── 研究循环：上限必须生效 ─────────────────────────────────────────
def test_research_loop_stops_at_budget():
    """★ 只有 `research ⇄ evidence_check` 这一处循环会转；**预算尽必须停**，不许死循环。"""
    st = _base(budget={"research_steps_left": 3, "tool_calls_left": 5, "repair_left": 2})
    last = _run(st, tool_call=_empty_call)           # 永远查不到 → 一直有缺口
    assert last["budget"]["research_steps_left"] == 0
    assert last["budget"]["tool_calls_left"] == 2    # 只跑了 3 次，没超
    assert len(last["tool_calls"]) == 3


def test_research_stops_immediately_when_budget_is_zero():
    st = _base(budget={"research_steps_left": 0, "tool_calls_left": 0, "repair_left": 2})
    last = _run(st, tool_call=_call())
    assert last["tool_calls"] == []
    assert last["budget"]["tool_calls_left"] == 0


def test_gap_is_recorded_when_nothing_found():
    """查不到 → 覆盖度里记 `no_evidence`（**不是** tool_failed）。"""
    last = _run(_base(), tool_call=_empty_call)
    gaps = [c for c in last["coverage"].values() if not c["covered"]]
    assert gaps and gaps[0]["gap_reason"] == "no_evidence"


def test_gap_reason_keeps_tool_failed_distinct():
    """★ 工具失败必须与「没查到」**分开记** —— 两者不许塌成一个原因。"""
    def failing(name, args):
        return ToolResult(tool=name, ok=False, data={"detail": "timeout"},
                          sources=[], error="tool_failed", status="tool_failed")
    last = _run(_base(), tool_call=failing)
    # 工具执行失败必须在覆盖度和调用记录里都保持原状态。
    assert last["tool_calls"][0]["status"] == "tool_failed"
    assert last["tool_calls"][0]["ok"] is False
    assert last["coverage"]["讲转专业"]["gap_reason"] == "tool_failed"


# ── 共享台账 / 出参 ────────────────────────────────────────────────
def test_evidence_stays_in_caller_owned_containers():
    """★ 子图**不新建第二本台账** —— 证据与调用记录都在传入的容器里。"""
    st = _base()
    last = _run(st, tool_call=_call(hits=2))
    assert len(last["evidence"]) == 2
    assert len(last["tool_calls"]) == 1


def test_author_is_an_explicit_stub_not_a_fake_deliverable():
    """★ 阶段3 **不产出任何 PPTX** —— 必须显式标记，不许静默交付半成品。"""
    last = _run(_base(), tool_call=_call())
    assert last["artifacts"]["pptx_path"] is None
    assert last["artifacts"]["stage4_required"] is True
    errs = [e["error"] for e in last["errors"]]
    assert "stage4_not_implemented" in errs


def test_plan_records_derived_from_the_request():
    """计划必须写下本次目标/受众的**原话** —— 但★ 抄回原话**不等于**计划做得好。"""
    last = _run(_base(), tool_call=_call())
    assert last["plan"]["derived_from"]["goal"] == "讲转专业"
    assert last["plan"]["derived_from"]["audience"] == "大一新生"


def test_injected_graphs_are_not_shared_between_calls():
    """★ 编译缓存曾用 `id()` 做键 —— 被回收的 lambda 会让后一次调用撞上前一张图。"""
    first = subgraph._app(planner=lambda brief: {})
    second = subgraph._app(planner=lambda brief: {})
    assert first is not second
