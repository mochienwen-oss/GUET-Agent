# -*- coding: utf-8 -*-
"""多轮 prior-context 的 10 条真实契约测试（仅定向）。

Run:
  D:\\venv-guet-ppt\\Scripts\\python.exe -m pytest backend/tests/test_multiturn_context.py -q
"""
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "训练集"))

from entities import entity_calls  # noqa: E402
from entities import entity_prior  # noqa: E402
from entities import entity_resolver as ER  # noqa: E402
from agent.runtime import graph  # noqa: E402
from compiler.multiturn import (  # noqa: E402
    MultiturnScenarioSpec, conversation_binding,
    build_multiturn_trajectory)
from compiler.models import (  # noqa: E402
    ExpectedEvidenceState, ToolStepSpec)

RESOLVER = ER.build_production_resolver()
CATALOG = json.loads((ROOT / "backend/data/entity_catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((ROOT / "backend/data/plan_db.json").read_text(encoding="utf-8"))
MAJOR_ID = {row["canonical_name"]: row["entity_id"]
            for row in CATALOG["entities"]["major"]}
MAJOR_CODE = {row["major"]: row["code"] for row in PLAN["majors"]}
SHA = "a" * 64


def course_id(name, major):
    code = MAJOR_CODE[major]
    return next(row["entity_id"] for row in CATALOG["entities"]["course"]
                if row["canonical_name"] == name and row["major_code"] == code)


def entity(entity_type, name, major=None, *, source="tool_canonical_args",
           active=True, catalog_valid=True):
    return entity_prior.PriorEntity(
        entity_type=entity_type, canonical_name=name,
        entity_id=(MAJOR_ID[name] if entity_type == "major"
                   else course_id(name, major)),
        source=source, source_turn=1,
        evidence_identity=(SHA if source == "tool_canonical_args" else None),
        active=active, catalog_valid=catalog_valid)


def fact(key, value, evidence=SHA):
    return entity_prior.PriorFact(
        fact_key=key, value=value, source="tool_observation",
        source_turn=1, evidence_identity=evidence)


def context(*entities, facts=()):
    return entity_prior.PriorContext(
        conversation_id="conv-test", entities=entities,
        facts=tuple(facts))


def validate(query, args, prior):
    return entity_calls.validate_call(
        "query_course", args, ER.resolve_question(RESOLVER, query),
        prior_context=prior)


# 1. 当前问句省略实体，但参数完整且与前序唯一实体一致 → allow
def test_1_omitted_entity_full_args_unique_prior_allows():
    prior = context(entity("major", "软件工程"))
    result = validate("那《数字逻辑与计算机组成》是多少学分？",
                      {"major": "软件工程", "course": "数字逻辑与计算机组成"},
                      prior)
    assert result.decision == "allow"
    assert result.canonical_args == {"major": "软件工程",
                                     "course": "数字逻辑与计算机组成"}
    binding = next(b for b in result.bindings if b.arg == "major")
    assert binding.narrowed_by == "prior_context"
    assert binding.canonical_name == "软件工程"


# 2. 参数缺必填 major → reject，不自动补全
def test_2_missing_required_arg_is_not_autofilled():
    prior = context(entity("major", "软件工程"))
    result = validate("那《数字逻辑与计算机组成》是多少学分？",
                      {"course": "数字逻辑与计算机组成"}, prior)
    assert result.decision == "reject"
    assert result.reason == "bad_arg:major"
    assert result.canonical_args == {}


# 3. 前序两个课程，当前说“那门课” → clarify
def test_3_two_prior_courses_with_pronoun_clarifies():
    prior = context(entity("major", "软件工程"),
                    entity("course", "数字逻辑与计算机组成", "软件工程"),
                    entity("course", "数据结构与算法(外文教材)", "软件工程"))
    result = validate("那门课是多少学分？",
                      {"major": "软件工程", "course": "数字逻辑与计算机组成"},
                      prior)
    assert result.decision == "clarify"
    assert result.reason == "unresolved:course"
    assert len(result.clarification["candidate_names"]) == 2
    assert result.canonical_args == {}


# 4. 前序唯一课程，当前模型传入另一课程 → reject
def test_4_unique_prior_course_with_other_course_rejects():
    prior = context(entity("major", "软件工程"),
                    entity("course", "数字逻辑与计算机组成", "软件工程"))
    result = validate("那门课是多少学分？",
                      {"major": "软件工程",
                       "course": "数据结构与算法(外文教材)"}, prior)
    assert result.decision == "reject"
    assert result.reason == "prior_entity_mismatch:course"
    assert result.canonical_args == {}


# 5. prior entity_id 被篡改 / catalog 不存在 → reject
def test_5_tampered_prior_entity_is_rejected():
    catalog_names = {row["entity_id"]: row["canonical_name"]
                     for row in CATALOG["entities"]["major"]
                     + CATALOG["entities"]["course"]}
    tampered = entity("major", "软件工程").model_copy(
        update={"entity_id": "major:missing"})
    with pytest.raises(ValueError, match="not|不符|catalog"):
        entity_prior.validate_prior_context(context(tampered), catalog_names)
    # loader 拒绝后，validation 侧也不会把它当成可用 prior。
    untrusted = context(tampered.model_copy(update={"catalog_valid": False}))
    result = validate("那《数字逻辑与计算机组成》是多少学分？",
                      {"major": "软件工程", "course": "数字逻辑与计算机组成"},
                      untrusted)
    assert result.decision == "reject"
    assert result.canonical_args == {}


# 6. prior fact 只来自未审核 assistant 文本 → 不得使用
def test_6_assistant_sourced_prior_fact_is_refused():
    with pytest.raises(ValidationError):
        entity_prior.PriorFact(
            fact_key="course.credits", value=3.0,
            source="easydataset_assistant", source_turn=1,
            evidence_identity=SHA)
    assert entity_prior.PriorContext(
        conversation_id="conv-test").trusted_facts() == ()


# 7. 用户明确纠正实体 → 新实体生效、旧实体失效
def test_7_user_correction_supersedes_old_entity():
    prior = context(
        entity("major", "交通工程", source="tool_canonical_args", active=False),
        entity("major", "土木工程", source="human_correction"),
        entity("course", "工程力学1", "交通工程",
               source="tool_canonical_args"))
    query = "不是，我问的是土木工程的《工程力学1》"
    new = validate(query, {"major": "土木工程", "course": "工程力学1"}, prior)
    assert new.decision == "allow"
    assert new.canonical_args["major"] == "土木工程"
    old = validate(query, {"major": "交通工程", "course": "工程力学1"}, prior)
    assert old.decision == "reject"
    assert old.canonical_args == {}


# 8. compiler 与 runtime 对同一多轮样本结论一致
def test_8_compiler_and_runtime_agree():
    prior = context(entity("major", "软件工程"))
    spec = MultiturnScenarioSpec.model_validate({
        "scenario_id": "mt-test-008",
        "conversation_id": "conv-test",
        "turn_index": 2,
        "conversation_history": [{
            "turn_index": 1, "role": "user",
            "content": "软件工程专业第 3 学期有哪些课程？",
            "source": "mechanism_template"}],
        "current_user_query": "那《数字逻辑与计算机组成》是多少学分？",
        "prior_context": prior.model_dump(mode="json"),
        "steps": [{"tool": "query_course",
                   "arguments": {"major": "软件工程",
                                 "course": "数字逻辑与计算机组成"},
                   "expect": "success"}],
        "expected_evidence": {"state": "success"},
        "needs_clarification": False,
        "stop_condition": "当前轮结束",
        "max_steps": 1,
        "provenance": {"source": "test"},
        "review_status": "pending",
        "assistant_target": None,
        "expected_behavior": "tool_call",
    })
    _messages, _probed, audit, _binding = build_multiturn_trajectory(
        spec, system_prompt="test")
    runtime = validate(spec.current_user_query,
                       {"major": "软件工程", "course": "数字逻辑与计算机组成"},
                       prior)
    assert audit[0]["decision"] == runtime.decision == "allow"
    assert audit[0]["canonical_args"] == runtime.canonical_args
    graph_out = graph.validate_entity_calls({
        "resolution": ER.resolve_question(RESOLVER, spec.current_user_query),
        "tool_calls": [{"name": "query_course",
                        "arguments": {"major": "软件工程",
                                      "course": "数字逻辑与计算机组成"}}],
        "prior_context": prior.model_dump(mode="json"),
    })
    assert graph_out["validation_decision"] == "allow"
    assert graph_out["validated_calls"] == [{
        "name": "query_course",
        "arguments": {"major": "软件工程",
                      "course": "数字逻辑与计算机组成"}}]


# 9. conversation binding 对历史问句/实体/证据变化敏感
def test_9_conversation_binding_changes_on_history_entity_or_evidence():
    prior = context(entity("major", "软件工程"), facts=[fact("course.credits", 4.0)])
    spec = MultiturnScenarioSpec.model_validate({
        "scenario_id": "mt-test-009", "conversation_id": "conv-test",
        "turn_index": 2,
        "conversation_history": [{
            "turn_index": 1, "role": "user", "content": "第一问",
            "source": "mechanism_template"}],
        "current_user_query": "那《数字逻辑与计算机组成》是多少学分？",
        "prior_context": prior.model_dump(mode="json"),
        "steps": [{"tool": "query_course",
                   "arguments": {"major": "软件工程",
                                 "course": "数字逻辑与计算机组成"},
                   "expect": "success"}],
        "expected_evidence": {"state": "success"},
        "needs_clarification": False, "stop_condition": "当前轮结束",
        "max_steps": 1, "provenance": {"source": "test"},
        "review_status": "pending", "assistant_target": None,
        "expected_behavior": "tool_call",
    })
    base = conversation_binding(spec)
    history_changed = spec.model_copy(update={
        "conversation_history": (spec.conversation_history[0].model_copy(
            update={"content": "另一个第一问"}),)})
    assert conversation_binding(history_changed) != base
    entity_changed = spec.model_copy(update={
        "prior_context": entity_prior.PriorContext(
            conversation_id="conv-test",
            entities=(entity("major", "软件工程").model_copy(
                update={"evidence_identity": "b" * 64}),),
            facts=(fact("course.credits", 4.0),))})
    assert conversation_binding(entity_changed) != base
    fact_changed = spec.model_copy(update={
        "prior_context": entity_prior.PriorContext(
            conversation_id="conv-test",
            entities=(entity("major", "软件工程"),),
            facts=(fact("course.credits", 4.0, "c" * 64),))})
    assert conversation_binding(fact_changed) != base
    probed_a = [{"tool": "query_course", "status": "success",
                 "observation": "a"}]
    probed_b = [{"tool": "query_course", "status": "success",
                 "observation": "b"}]
    assert conversation_binding(spec, probed_a) != conversation_binding(spec, probed_b)


# 10. 单轮行为零回归（同一纯函数、prior_context=None）
def test_10_single_turn_without_prior_context_is_unchanged():
    query = "软件工程专业的《数字逻辑与计算机组成》是多少学分？"
    args = {"major": "软件工程", "course": "数字逻辑与计算机组成"}
    result = validate(query, args, None)
    assert result.decision == "allow"
    assert result.reason in ("ok", "canonical_entity_absent_in_scope")
    assert result.canonical_args == args
