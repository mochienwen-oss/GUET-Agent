"""真实子图路径（模型用确定性替身，工具不碰服务器）。"""
import asyncio
import sys
from pathlib import Path

from pptx import Presentation

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agent.core.contracts import Source, ToolResult  # noqa: E402
from core.config import settings  # noqa: E402
from agent.tools.create_ppt.scripts.execution import subgraph  # noqa: E402
from agent.tools.create_ppt.scripts.content.model import Claim, Slide  # noqa: E402


def test_two_different_tools_to_claim_checked_pptx(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    calls = []

    def planner(brief):
        assert brief["goal"] == "给大一新生讲转专业和课程"
        return {"research_tasks": [
            {"question": "转专业的规定", "name": "search_regulations",
             "args": {"query": "转专业 管理办法"}},
            {"question": "课程学分", "name": "query_course",
             "args": {"major": "计算机科学与技术", "course": "数据结构"}}],
            "research_questions": ["转专业的规定", "课程学分"]}

    def tool(name, args):
        calls.append((name, args))
        if name == "search_regulations":
            return ToolResult(tool=name, ok=True, sources=[
                Source(title="转专业管理办法", text="转专业申请须符合条件")])
        return ToolResult(tool=name, ok=True, sources=[])

    def authorer(state):
        ids = list(state["evidence"])
        assert len(ids) == 1
        assert state["coverage"]["课程学分"]["gap_reason"] == "no_evidence"
        return [Slide("cover", "转专业与课程", subtitle="给大一新生"),
                Slide("bullets", "依据与缺口", [
                    Claim("转专业申请须符合条件", ids),
                    Claim("课程学分本次未查到", gap="本次检索未找到课程依据")]),
                Slide("end", "谢谢")]

    async def collect(question, **kwargs):
        return [event async for event in subgraph.run_stream([], question, **kwargs)]

    first = asyncio.run(collect("给大一新生讲转专业和课程", task_id="gen1",
                                conversation_id=7, planner=planner, tool_call=tool,
                                authorer=authorer))
    assert any(ev == "ppt" and data.get("ppt_task", {}).get("stage") == "clarify"
               for ev, data in first)
    second = asyncio.run(collect("大一新生", task_id="gen1", conversation_id=7,
                                 resume_stage="clarify", planner=planner,
                                 tool_call=tool, authorer=authorer))
    assert [name for name, _ in calls] == ["search_regulations", "query_course"]
    assert not any("ppt_error" in data for ev, data in second if ev == "ppt")
    done = [data for ev, data in second if ev == "done"][0]
    assert done["artifacts"]["pages"] == 3
    path = Path(done["artifacts"]["pptx_path"])
    assert path.exists() and len(Presentation(path).slides) == 3
    assert (tmp_path / "gen1.sources.md").exists()
    saved = subgraph.load_state("gen1", for_resume=True)
    assert saved["stage"] == "draft" and saved["next_node"] is None
    # ★ 人工确认位：生成完成只到「待确认」，**这一步不给下载地址**。
    drafts = [data["ppt_draft"] for ev, data in second if ev == "ppt" and "ppt_draft" in data]
    assert drafts and "download_url" not in drafts[0] and drafts[0]["pages"] == 3
    assert not any(ev == "ppt" and "ppt_ready" in data for ev, data in second)
    # ★ 审阅所依赖的那条缝：生成的逐页内容**确实落进了 state.json**，
    #   且状态端点的投影能据此还原出「哪条要点挂在哪个来源上」。
    from routers.ppt import _review

    review = _review(saved)
    assert len(review["slides"]) == 3
    assert review["slides"][1]["claims"][0]["text"] == "转专业申请须符合条件"
    assert list(review["sources"]) == review["slides"][1]["claims"][0]["evidence_ids"]


def test_model_plan_validates_tool_arguments(monkeypatch):
    from agent.tools.create_ppt.scripts.content import generate

    monkeypatch.setattr(generate, "_json_reply", lambda *_: {"research_tasks": [
        {"question": "制度", "name": "search_regulations", "args": {"query": "转专业"}},
        {"question": "课程", "name": "query_course",
         "args": {"major": "计算机科学与技术", "course": "数据结构"}}]})
    result = generate.plan({"goal": "讲课程", "audience": "新生"})
    assert len(result["research_tasks"]) == 2


def test_named_major_course_repairs_wrong_retrieval_tool(monkeypatch):
    from agent.tools.create_ppt.scripts.content import generate

    replies = [
        {"research_tasks": [
            {"question": "课程学分", "name": "search_regulations", "args": {"query": "数据结构"}},
            {"question": "课程学期", "name": "search_regulations", "args": {"query": "课程学期"}}]},
        {"research_tasks": [
            {"question": "课程定位", "name": "query_course",
             "args": {"major": "计算机科学与技术", "course": "数据结构"}},
            {"question": "毕业要求", "name": "search_regulations",
             "args": {"query": "数据结构 毕业要求"}}]},
    ]
    monkeypatch.setattr(generate, "_json_reply", lambda *_: replies.pop(0))
    result = generate.plan({"goal": "计算机科学与技术专业《数据结构》培养方案学分",
                            "audience": "新生"})
    assert result["research_tasks"][0]["name"] == "query_course"
    assert not replies


def test_shipped_sources_file_is_what_gets_verified(tmp_path, monkeypatch):
    """★ 校验对象是**落盘的 `.sources.md`**，不是内存里那串 —— 写盘环节也在门内。

    来源清单是要跟 pptx 一起交到用户手里的交付物，所以「写出去以后还能不能原样
    解析回被引用的那组 ID」才是要验的东西（`ppt.qa` 的双向检查据此判定）。
    """
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    eid = "0123456789ab"

    result = subgraph._author_real(
        {"task_id": "rt", "evidence": {eid: {"title": "某来源", "text": "有据的事实"}}},
        lambda state: [Slide("cover", "标题"),
                       Slide("bullets", "内容", claims=[Claim("有据的事实", [eid])]),
                       Slide("end", "结束")])
    assert result["stage"] == "draft"
    on_disk = (tmp_path / "rt.sources.md").read_text(encoding="utf-8")
    assert ("- [%s] " % eid) in on_disk
    # ★ 校验用的那一串 == 从盘上读回来的那份（写之前那串不算数）。
    assert result["artifacts"]["sources_md"] == on_disk


def test_author_repairs_unrendered_items_without_delivering_first_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    calls = []
    eid = "a1b2c3d4e5f6"

    def authorer(state):
        calls.append(state.get("qa_feedback") or [])
        if len(calls) == 1:
            return [Slide("cover", "标题"), Slide("bullets", "内容", items=["事实"]),
                    Slide("end", "结束")]
        return [Slide("cover", "标题"),
                Slide("bullets", "内容", claims=[Claim("有据的事实", [eid])]),
                Slide("end", "结束")]

    result = subgraph._author_real({"task_id": "repair", "evidence": {
        eid: {"title": "测试来源", "text": "有据的事实"}}}, authorer)
    assert result["stage"] == "draft"
    assert len(calls) == 2 and calls[1][0]["code"] == "empty_page"
    assert (tmp_path / "repair.pptx").exists()


def test_author_retries_malformed_slide_before_rendering(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    calls = []

    def authorer(state):
        calls.append(state.get("qa_feedback") or [])
        if len(calls) == 1:
            raise ValueError("逐页内容格式无效")
        return [Slide("cover", "标题"),
                Slide("bullets", "缺口", claims=[Claim("暂无依据", gap="待补证")]),
                Slide("end", "结束")]

    result = subgraph._author_real({"task_id": "retry", "evidence": {}}, authorer)
    assert result["stage"] == "draft"
    assert len(calls) == 2 and calls[1][0]["code"] == "invalid_output"
