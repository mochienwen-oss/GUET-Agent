"""阶段 3 的真实流入口与证据状态回归。"""
import asyncio
import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.config import settings  # noqa: E402
from agent.runtime import graph  # noqa: E402
from agent.tools.create_ppt import tool as ppt_tool  # noqa: E402
from agent.tools.create_ppt.scripts import subgraph  # noqa: E402


def test_real_ppt_stream_emits_typed_events(monkeypatch, tmp_path):
    """使用真正的 PPT 子图；假流无法发现自定义事件形状不一致。

    ★ 阶段3：入口不再是关键词路由 —— 由模型选 `create_ppt` 触发。这里把模型
      换成替身，主图、任务持久化与子图是真的；模型提取的受众不能在交接时丢失。
    """
    from agent.tools.create_ppt.scripts import generate, workflow
    from agent.tools.create_ppt.scripts.model import Slide
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    monkeypatch.setattr(generate, "plan", lambda brief: {"research_questions": [], "research_tasks": []})
    monkeypatch.setattr(generate, "create_slides", lambda state: [Slide("cover", "转专业"),
        Slide("agenda", "说明范围", items=["适用对象", "办理依据"]), Slide("end", "结束")])
    def preview_double(path, output, ids):
        from PIL import Image
        output.mkdir(parents=True, exist_ok=True)
        result = []
        for sid in ids:
            target = output / (sid + ".png")
            Image.new("RGB", (4, 4), "white").save(target)
            result.append(str(target))
        return result
    monkeypatch.setattr(workflow, "render_previews", preview_double)

    async def fake_decide(messages):
        return {"type": "tool_calls",
                "calls": [{"name": "create_ppt",
                           "arguments": {"goal": "讲转专业", "audience": "大一新生"}}]}

    monkeypatch.setattr(graph.model, "decide", fake_decide)

    async def collect():
        return [event async for event in graph.run_stream([], "做个转专业PPT")]

    events = asyncio.run(collect())
    assert events
    assert all(isinstance(event, tuple) and len(event) == 2 for event in events)
    assert any(kind == "ppt" for kind, _ in events)
    assert sum(kind == "done" for kind, _ in events) == 1
    task_id = next(value["ppt_task"]["task_id"] for kind, value in events
                   if kind == "ppt" and "ppt_task" in value)
    state = ppt_tool.owned_state(task_id, None)
    assert state["brief"]["audience"] == "大一新生"
    assert state["stage"] == "draft"


def test_failed_tool_is_not_labeled_no_evidence():
    state = {
        "plan": {"research_questions": ["转专业"]},
        "tool_calls": [{"args": {"query": "转专业"}, "ok": False,
                        "status": "tool_failed"}],
        "evidence": {},
        "budget": {"research_steps_left": 0, "tool_calls_left": 0},
    }
    result = subgraph._evidence_check(state)
    assert result["coverage"]["转专业"]["gap_reason"] == "tool_failed"


def test_completed_nodes_are_saved_with_resume_context(monkeypatch):
    snapshots = []
    monkeypatch.setattr(subgraph, "save_state",
                        lambda task_id, state: snapshots.append(dict(state)) or "unused")

    async def collect():
        return [event async for event in subgraph.run_stream([], "做个转专业PPT")]

    asyncio.run(collect())
    assert len(snapshots) >= 2  # intake、clarify 各自完成后落盘
    assert snapshots[0]["next_node"] == "clarify"
    assert snapshots[-1]["stage"] == "await_clarify"
    assert snapshots[-1]["request_raw"] == "做个转专业PPT"
    assert "conversation_id" in snapshots[-1]
    assert "tool_calls" in snapshots[-1]
    assert "pending_user" in snapshots[-1]


def test_clarification_resumes_same_task_from_clarify(monkeypatch):
    saved = {}
    snapshots = []

    def save(task_id, state):
        saved[task_id] = copy.deepcopy(state)
        snapshots.append(copy.deepcopy(state))
        return "unused"

    monkeypatch.setattr(subgraph, "save_state", save)
    monkeypatch.setattr(subgraph, "load_state",
                        lambda task_id, for_resume: copy.deepcopy(saved[task_id]))

    async def collect(question, **kwargs):
        return [event async for event in subgraph.run_stream([], question, **kwargs)]

    asyncio.run(collect("做个转专业PPT", task_id="t1", conversation_id=7))
    assert saved["t1"]["stage"] == "await_clarify"
    before = len(snapshots)
    planner = lambda brief: {"research_questions": [], "outline_seed": []}
    resumed = asyncio.run(collect("大一新生", task_id="t1", conversation_id=7,
                                  resume_stage="clarify", planner=planner, authorer=False))
    assert snapshots[before]["next_node"] == "plan"  # 从 clarify 继续，未重跑 intake
    assert saved["t1"]["request_raw"] == "做个转专业PPT"
    assert saved["t1"]["brief"]["audience"] == "大一新生"
    assert any(kind == "ppt" and event.get("ppt_error", {}).get("error")
               == "stage4_not_implemented" for kind, event in resumed)


def test_resume_at_research_skips_completed_nodes(monkeypatch):
    saved = {
        "task_id": "t2", "request_raw": "做个转专业PPT", "conversation_id": 7,
        "stage": "planned", "next_node": "research",
        "brief": {"goal": "转专业", "audience": "大一新生", "length": 6},
        "plan": {"research_questions": []}, "open_questions": [],
        "tool_calls": [], "evidence": {}, "coverage": {}, "errors": [],
        "outline": [], "slides": [], "pending_user": None,
        "budget": {"research_steps_left": 1, "tool_calls_left": 1, "repair_left": 0},
    }
    snapshots = []
    monkeypatch.setattr(subgraph, "load_state",
                        lambda task_id, for_resume: copy.deepcopy(saved))
    monkeypatch.setattr(subgraph, "save_state",
                        lambda task_id, state: snapshots.append(copy.deepcopy(state)) or "unused")

    async def collect():
        return [event async for event in subgraph.run_stream(
            [], "续做", task_id="t2", conversation_id=7, resume_stage="research",
            authorer=False)]

    asyncio.run(collect())
    assert snapshots[0]["next_node"] == "evidence_check"
    assert snapshots[0]["request_raw"] == "做个转专业PPT"


def test_state_file_preserves_clarification_context(tmp_path, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))

    async def collect():
        return [event async for event in subgraph.run_stream(
            [], "做个转专业PPT", task_id="t-real", conversation_id=7)]

    asyncio.run(collect())
    stored = subgraph.load_state("t-real", for_resume=True)
    assert stored["stage"] == "await_clarify"
    assert stored["request_raw"] == "做个转专业PPT"
    assert stored["conversation_id"] == 7
    assert stored["pending_user"]["missing"] == ["audience"]
    assert stored["next_node"] is None


def _final_state(app, init):
    async def _go():
        final = None
        async for mode, payload in app.astream(init, stream_mode=["custom", "values"]):
            if mode == "values":
                final = payload
        return final
    return asyncio.run(_go())


def test_handoff_merges_child_evidence(monkeypatch):
    """台账是**共享的一本**（设计 §4.2）：子图取证的结果要并回主图。"""

    async def fake_ppt(history, question, **kwargs):
        assert kwargs["evidence"] == {"pre": {"title": "原有资料"}}
        assert kwargs["conversation_id"] == 7
        yield ("done", {"contexts": [], "calls": [{"name": "search_regulations"}],
                        "evidence": {"e1": {"title": "学籍管理规定"}}})

    app = graph.build_agent_graph(ppt_stream=fake_ppt, emit=lambda _event: None)
    init = {"question": "大一新生", "history": [], "conversation_id": 7, "owner_id": 3,
            "task_id": "t1", "resume_stage": "clarify",
            "evidence": {"pre": {"title": "原有资料"}},
            "tool_calls": [], "calls": [], "contexts": []}
    final = _final_state(app, init)

    assert final["evidence"] == {"pre": {"title": "原有资料"},
                                 "e1": {"title": "学籍管理规定"}}
    assert final["calls"] == [{"name": "search_regulations"}]


def test_handoff_forwards_resume_identity(monkeypatch):
    seen = []

    async def fake_ppt(history, question, **kwargs):
        seen.append(kwargs)
        yield ("done", {"contexts": [], "calls": []})

    app = graph.build_agent_graph(ppt_stream=fake_ppt, emit=lambda _event: None)
    init = {"question": "大一新生", "history": [], "conversation_id": 7, "owner_id": 3,
            "task_id": "t1", "resume_stage": "clarify", "evidence": {},
            "tool_calls": [], "calls": [], "contexts": []}
    asyncio.run(app.ainvoke(init))

    assert seen[0]["task_id"] == "t1"
    assert seen[0]["conversation_id"] == 7
    assert seen[0]["resume_stage"] == "clarify"


def test_wall_clock_budget_pauses_instead_of_delivering(tmp_path, monkeypatch):
    """★ 墙钟预算用尽 → **只存进度、不自动交付**，且 `next_node` 保留（可续跑）。

    用**卡住的节点**来验：预算只在节点之间生效的话，这条测试会一直等到节点跑完
    （1.5s > 1s 的预算），所以它同时钉住了「逐块限时」这个实现方式。
    """
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    monkeypatch.setattr(settings, "agent_ppt_timeout_sec", 1)

    def slow_intake(state):
        time.sleep(1.5)
        return {"brief": state.get("brief") or {}, "stage": "intake"}

    monkeypatch.setattr(subgraph, "_intake", slow_intake)

    async def collect():
        return [event async for event in subgraph.run_stream(
            [], "做个讲转专业的 PPT", task_id="to1", conversation_id=7, authorer=False)]

    events = asyncio.run(collect())
    errors = [data["ppt_error"] for kind, data in events if kind == "ppt" and "ppt_error" in data]
    assert errors and errors[0]["error"] == "ppt_timeout"
    assert errors[0]["recoverable"] is True          # 可续跑，不是终局失败
    assert sum(kind == "done" for kind, _ in events) == 1
    stored = subgraph.load_state("to1", for_resume=True)
    assert stored["stage"] == "paused"
    assert stored["next_node"] == "intake"           # 没跑完的节点保留 → 能接着跑
    assert not (tmp_path / "to1.pptx").exists()      # **不自动交付半成品**
