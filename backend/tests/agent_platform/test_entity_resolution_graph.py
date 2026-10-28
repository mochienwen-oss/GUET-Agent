# -*- coding: utf-8 -*-
"""实体解析进入主图后的安全边界。"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from entities import entity_resolver as ER
from agent.runtime import graph


def _run(awaitable):
    return asyncio.run(awaitable)


def _production_envelope(question):
    return ER.build_production_resolver().resolve(question)


def test_resolve_entities_preserves_the_envelope_and_runs_once():
    envelope = _production_envelope("交通工程专业的工程力学1多少学分")

    class Stub:
        def __init__(self):
            self.calls = []

        def resolve(self, question, context=None):
            self.calls.append((question, context))
            return envelope

    stub = Stub()
    out = graph.resolve_entities({"question": envelope.original_query}, resolver=stub)
    assert stub.calls == [(envelope.original_query, None)]
    assert out["resolution"] is envelope
    assert out["resolution_error"] is None


def test_resolver_construction_failure_is_a_stop_not_a_tool_failure():
    def broken():
        raise ER.ResolverError("catalog hash mismatch")

    out = graph.resolve_entities({"question": "工程力学"}, resolver_factory=broken)
    assert out["resolution"] is None
    assert out["resolution_error"] == "catalog hash mismatch"
    assert "实体目录" in out["final_answer"]
    assert out["tool_calls"] == []


def test_t4_call_is_clarified_before_registry(monkeypatch):
    """T4 层证据不足 → 必须在到达 Registry 之前澄清。

    ★ 夹具从「计科」换成「形策」：2026-10-17 人工裁决把 计科/软工 等落成正式别名后，
      计科 已是 T2（可解析），不再能代表「T4 未决」。形策 仍是 T4-only。
    """
    envelope = _production_envelope("形策有哪些课")
    seen = []
    monkeypatch.setattr(graph.registry, "call",
                        lambda *args, **kwargs: seen.append((args, kwargs)))
    out = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "list_courses", "arguments": {"major": "形策"}}],
    })
    assert out["validation_decision"] == "clarify"
    assert out["validated_calls"] == []
    assert out["tool_calls"] == []
    assert seen == []


def test_cross_type_exact_name_is_narrowed_for_the_major_parameter():
    envelope = _production_envelope("软件工程有哪些课")
    out = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "list_courses",
                         "arguments": {"major": "软件工程"}}],
    })
    assert out["validation_decision"] == "allow"
    assert out["validated_calls"] == [
        {"name": "list_courses", "arguments": {"major": "软件工程"}}]
    audit = out["call_validations"][0]
    assert audit["bindings"][0]["narrowed_by"] == "arg_type"
    assert audit["bindings"][0]["narrowed_from"] == ["course", "major"]


def test_execute_tools_uses_canonical_args_and_keeps_raw_args(monkeypatch):
    envelope = _production_envelope("计算机科学与技术有哪些课")
    validated = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "list_courses",
                         "arguments": {"major": "计算机科学与技术"}}],
    })
    seen = []

    result = SimpleNamespace(
        tool="list_courses", status="success", data={}, sources=[], error=None)
    monkeypatch.setattr(graph.registry, "call",
                        lambda name, args=None: seen.append((name, args)) or result)
    monkeypatch.setattr(graph.registry, "render", lambda _result: "ok")

    out = _run(graph.execute_tools({
        **validated,
        "messages": [], "executed": [], "calls": [], "contexts": [],
        "tool_results": [], "tool_calls_used": 0, "retries_used": 0,
        "rounds_done": 0,
    }))
    assert seen == [("list_courses", {"major": "计算机科学与技术"})]
    assert out["calls"][0]["raw_args"] == {"major": "计算机科学与技术"}
    assert out["calls"][0]["canonical_args"] == {"major": "计算机科学与技术"}


def test_canonical_args_drive_duplicate_detection(monkeypatch):
    envelope = _production_envelope("软件 工程有哪些课")
    validated = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "list_courses",
                         "arguments": {"major": "软件 工程"}}],
    })
    assert validated["validation_decision"] == "allow"
    canonical = validated["validated_calls"][0]
    seen = []
    monkeypatch.setattr(graph.registry, "call",
                        lambda *args, **kwargs: seen.append((args, kwargs)))

    out = _run(graph.execute_tools({
        **validated,
        "messages": [],
        "executed": [{"name": canonical["name"],
                      "args": canonical["arguments"]}],
        "calls": [], "contexts": [], "tool_results": [],
        "tool_calls_used": 0, "retries_used": 0, "rounds_done": 0,
    }))
    assert seen == []
    assert len(out["observations"]) == 1


def test_validate_calls_requires_a_resolution_envelope():
    with pytest.raises(RuntimeError, match="resolution"):
        graph.validate_entity_calls({
            "tool_calls": [{"name": "list_courses",
                             "arguments": {"major": "软件工程"}}],
        })


def test_search_uses_normalized_semantics_and_keeps_both_alias_terms():
    catalog = ER.EA.load_alias_bundle().catalog
    course = next(row for row in catalog["entities"]["course"]
                  if row["canonical_name"] == "高等数学A1")
    resolver = ER.Resolver(
        catalog,
        ({"alias_id": "alias:test:gaoshu", "alias": "高数",
          "entity_type": "course", "canonical": course["entity_id"],
          "canonical_family": None, "mapping_type": "one_to_one",
          "scope": {}, "review_status": "reviewed", "reviewed_by": "human:test",
          "review_note": None},),
        (), catalog_sha256="a" * 64, aliases_sha256="b" * 64)
    envelope = resolver.resolve("高数有什么规定")

    out = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "search_regulations",
                         "arguments": {"query": "高数有什么规定",
                                       "keywords": ["课程规定"]}}],
    })
    args = out["validated_calls"][0]["arguments"]
    assert args["query"] == "高等数学A1有什么规定"
    assert args["keywords"] == ["课程规定", "高数", "高等数学A1"]


def test_search_never_expands_an_unresolved_t4_candidate():
    envelope = _production_envelope("高数有什么规定")
    out = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "search_regulations",
                         "arguments": {"query": "高数有什么规定",
                                       "keywords": ["课程规定"]}}],
    })
    assert out["validated_calls"][0]["arguments"] == {
        "query": "高数有什么规定", "keywords": ["课程规定"]}


def test_real_graph_clarifies_t4_before_registry(monkeypatch):
    base = ER.build_production_resolver()

    class CountingResolver:
        calls = 0

        def resolve(self, question, context=None):
            self.calls += 1
            return base.resolve(question, context=context)

    resolver = CountingResolver()

    async def fake_decide(_messages):
        return {"type": "tool_calls", "calls": [
            {"name": "list_courses", "arguments": {"major": "形策"}}]}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("T4 未决调用不应到达 Registry")

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.registry, "call", forbidden)
    monkeypatch.setattr(graph.entity_resolver, "build_production_resolver", lambda: resolver)

    async def drive():
        return [item async for item in graph.run_stream([], "形策有哪些课")]

    items = _run(drive())
    assert resolver.calls == 1
    assert [event for event, _ in items] == ["delta", "done"]
    assert "完整" in items[0][1]
    done = items[-1][1]
    assert done["calls"] == []
    assert {"contexts", "calls", "n_refs", "decision"} <= set(done)
    assert done["call_validations"]
    assert done["call_validations"][0]["decision"] == "clarify"
    assert done["call_validations"][0]["raw_args"] == {"major": "形策"}


def test_weak_t4_noise_does_not_override_a_safe_clarification(monkeypatch):
    """T4 扫描仍有噪声；未调用结构化工具时不能全局截断正常文本。"""
    async def fake_decide(_messages):
        return {"type": "text", "content": "请问PPT要讲给谁听？"}

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph, "_APP", None)

    async def drive():
        return [item async for item in graph.run_stream(
            [], "帮我做一份讲学校学业预警制度的PPT，给大一新生班会用")]

    items = _run(drive())
    assert items[0] == ("delta", "请问PPT要讲给谁听？")


def test_query_explicit_context_resolves_an_unreviewed_abbreviation_end_to_end(monkeypatch):
    """★ 人工裁决 2026-10-17 的 C 类：**未知简称 + 问句自带的确定性上下文** → 可规范化查询。

    链路（全部真实，无 stub）：
      问句「测控技术与仪器专业的《计网》多少学分？」→ 图里解析两趟（第一趟推出
      `query_explicit` 上下文：唯一已确认专业 测控技术与仪器；第二趟用它硬过滤）
      → 「计网」由 T4 提升为 `resolved_by_context`，选中真实课程「计算机网络」
      → 调用校验 allow，**Registry 收到的是规范化后的参数**。

    同时把两条边界钉住：没有专业上下文时同一个简称必须澄清；多专业问句**不**推导上下文。
    """
    base = ER.build_production_resolver()

    # 边界一：不带专业上下文 → 仍必须澄清（绝不 top-1）
    bare = base.resolve("《计网》多少学分？")
    assert [m.raw_text for m in bare.mentions] == ["计网"]
    assert bare.mentions[0].selected is None
    assert bare.mentions[0].requires_clarification is True

    # 边界二：多专业问句 → 不推导上下文
    multi = graph.resolve_entities(
        {"question": "《大学英语1(艺术)》在产品设计和动画两边是同样的学分吗？"},
        resolver=base)["resolution"]
    assert multi.context.source == "query" and multi.context.major_code is None

    # 正路：图里两趟解析，第二趟带 query_explicit 上下文
    out = graph.resolve_entities(
        {"question": "测控技术与仪器专业的《计网》多少学分？"}, resolver=base)
    envelope = out["resolution"]
    assert out["resolution_error"] is None
    assert envelope.context.source == "query_explicit"
    mention = [m for m in envelope.mentions if m.raw_text == "计网"][0]
    assert mention.support_tier == "T4" and mention.selected is not None
    assert mention.selected.canonical_name == "计算机网络"
    assert mention.resolution_status == "resolved_by_context"
    assert mention.context_filter["rule"] == "structured_context_convergence"

    # 调用校验：allow，且 Registry 拿到的是规范化后的参数
    #   （走图的**真实两层节点**：validate_entity_calls → execute_tools，
    #    不驱动整条 LLM 回路 —— 机制覆盖在这两层，不需要假装模型）
    validated = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "query_course",
                        "arguments": {"major": "测控技术与仪器", "course": "计网"}}],
    })
    assert validated["validation_decision"] == "allow"
    assert validated["validated_calls"] == [
        {"name": "query_course",
         "arguments": {"major": "测控技术与仪器", "course": "计算机网络"}}]

    seen = []
    monkeypatch.setattr(
        graph.registry, "call",
        lambda name, args=None: seen.append((name, dict(args or {})))
        or SimpleNamespace(ok=True, status="success", data={}, sources=(),
                           error=None, meta={}, tool=name))
    monkeypatch.setattr(graph.registry, "render", lambda _result: "ok")

    out = _run(graph.execute_tools({
        **validated, "messages": [], "executed": [], "calls": [], "contexts": [],
        "tool_results": [], "tool_calls_used": 0, "retries_used": 0,
        "rounds_done": 0,
    }))
    assert seen, "应当真的执行了工具"
    assert seen[0][0] == "query_course"
    assert seen[0][1]["course"] == "计算机网络", "Registry 必须收到规范化后的课程名"
    assert seen[0][1]["major"] == "测控技术与仪器"
    assert out["calls"][0]["raw_args"]["course"] == "计网", "原始参数保留以供审计"


def test_query_course_scope_absent_graph_and_registry_agree():
    """规范课程存在、目标专业未开设：图 → validate → Registry 全部同一条结论。"""
    resolver = ER.build_production_resolver()
    question = "交通工程专业的《3D打印技术及实践》是多少学分？"
    out = graph.resolve_entities({"question": question}, resolver=resolver)
    envelope = out["resolution"]
    assert envelope.resolution_status == "resolved_scope_absent"

    validated = graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": [{"name": "query_course",
                        "arguments": {"major": "交通工程",
                                      "course": "3D打印技术及实践"}}],
    })
    assert validated["validation_decision"] == "allow"
    assert validated["validated_calls"] == [
        {"name": "query_course",
         "arguments": {"major": "交通工程", "course": "3D打印技术及实践"}}]

    executed = _run(graph.execute_tools({
        **validated, "messages": [], "executed": [], "calls": [], "contexts": [],
        "tool_results": [], "tool_calls_used": 0, "retries_used": 0,
        "rounds_done": 0,
    }))
    assert executed["calls"][0]["status"] == "no_evidence"
    assert executed["calls"][0]["canonical_args"] == {
        "major": "交通工程", "course": "3D打印技术及实践"}
