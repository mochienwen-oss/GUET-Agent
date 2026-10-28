# -*- coding: utf-8 -*-
"""最小 Skill v1：白名单、动态 Registry schema、审计与绑定身份。

Run:
  D:\\venv-guet-ppt\\Scripts\\python.exe -m pytest backend/tests/agent_platform/test_skills.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "训练集"))

from conversations import conversation_context as CC  # noqa: E402
from entities import entity_prior  # noqa: E402
from entities import entity_resolver as ER  # noqa: E402
from llm import llm_client  # noqa: E402
from agent.runtime import graph, registry  # noqa: E402
from agent.runtime.skills import (  # noqa: E402
    SkillToolNotAllowed, load_skill, parse_skill, skill_instructions_message,
    skill_tool_schemas, validate_skill_call)
from compiler.multiturn import MultiturnScenarioSpec, conversation_binding  # noqa: E402

RESOLVER = ER.build_production_resolver()


def _manifest_data():
    path = ROOT / "backend/agent/skills/campus_information_query.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_allowlisted_tool_passes_and_manifest_loading_is_strict():
    skill = load_skill("campus_information_query")
    validate_skill_call(skill, "query_course")          # 不抛
    names = [one["function"]["name"] for one in skill_tool_schemas(skill)]
    assert names == list(skill.allowed_tools)
    assert set(names) == {
        "search_regulations", "query_course", "compare_course",
        "query_empty_classroom", "list_courses", "query_major_requirements", "calculate"}

    base = _manifest_data()
    bad_cases = [
        {**base, "version": "  "},
        {**base, "allowed_tools": ["no_such_tool"]},
        {**base, "side_effect_level": "none", "approval_policy": "user_confirmation"},
        {**base, "undeclared_field": 1},
    ]
    for data in bad_cases:
        with pytest.raises(ValueError):
            parse_skill(data)


def test_globally_registered_but_non_allowlisted_tool_is_blocked():
    skill = load_skill("create_presentation")
    assert "query_course" in registry.REGISTRY, "前提：该工具在全局 Registry 中"
    with pytest.raises(SkillToolNotAllowed):
        validate_skill_call(skill, "query_course")

    envelope = ER.resolve_question(
        RESOLVER, "软件工程专业的《数字逻辑与计算机组成》是多少学分？")
    out = graph.validate_entity_calls({
        "resolution": envelope,
        "skill_manifest": skill,
        "tool_calls": [{"name": "query_course",
                        "arguments": {"major": "软件工程",
                                      "course": "数字逻辑与计算机组成"}}],
    })
    assert out["validation_decision"] == "reject"
    assert out["validated_calls"] == []
    assert out["call_validations"][0]["reason"] == "skill_tool_not_allowed"
    assert out["call_validations"][0]["skill_id"] == "create_presentation"


def test_unloaded_skill_instructions_are_absent():
    assert skill_instructions_message(None) is None
    skill = load_skill("create_presentation")
    message = skill_instructions_message(skill)
    assert skill.instructions in message["content"]
    assert skill.instructions not in llm_client.SYSTEM_PROMPT

    prior = entity_prior.PriorContext(conversation_id="conv-skill")
    spec = MultiturnScenarioSpec.model_validate({
        "scenario_id": "mt-skill-instructions", "conversation_id": "conv-skill",
        "turn_index": 2, "conversation_history": [],
        "current_user_query": "你好", "prior_context": prior.model_dump(mode="json"),
        "compressed_history": None,
        "skill_context": {"skill_id": skill.skill_id, "version": skill.version,
                          "allowed_tools": list(skill.allowed_tools)},
        "steps": [], "expected_evidence": {"state": "no_evidence"},
        "needs_clarification": False, "stop_condition": "当前轮结束",
        "max_steps": 0, "provenance": {"source": "test"},
        "review_status": "pending", "assistant_target": None,
        "expected_behavior": "no_tool",
    })
    from compiler.multiturn import build_multiturn_trajectory
    messages, _probed, _audit, _binding = build_multiturn_trajectory(
        spec, system_prompt="SYS")
    assert skill.instructions in messages[1]["content"]

    plain = spec.model_copy(update={"skill_context": None})
    messages, _probed, _audit, _binding = build_multiturn_trajectory(
        plain, system_prompt="SYS")
    assert all(skill.instructions not in one["content"] for one in messages)


def test_skill_version_changes_audit_and_conversation_binding():
    skill_v1 = load_skill("campus_information_query")
    skill_v2 = skill_v1.model_copy(update={"version": "3"})
    envelope = ER.resolve_question(RESOLVER, "软件工程有哪些课？")
    state = {
        "resolution": envelope,
        "tool_calls": [{"name": "list_courses",
                        "arguments": {"major": "软件工程"}}],
        "skill_manifest": skill_v1,
    }
    audit_v1 = graph.validate_entity_calls(state)["call_validations"][0]
    audit_v2 = graph.validate_entity_calls(
        {**state, "skill_manifest": skill_v2})["call_validations"][0]
    assert audit_v1["skill_version"] == skill_v1.version
    assert audit_v2["skill_version"] == skill_v2.version
    assert audit_v1["skill_allowed_tools"] == list(skill_v1.allowed_tools)

    prior = entity_prior.PriorContext(conversation_id="conv-skill")
    spec = MultiturnScenarioSpec.model_validate({
        "scenario_id": "mt-skill-001", "conversation_id": "conv-skill",
        "turn_index": 2,
        "conversation_history": [],
        "current_user_query": "软件工程有哪些课？",
        "prior_context": prior.model_dump(mode="json"),
        "steps": [], "expected_evidence": {"state": "no_evidence"},
        "needs_clarification": False, "stop_condition": "当前轮结束",
        "max_steps": 0, "provenance": {"source": "test"},
        "review_status": "pending", "assistant_target": None,
        "expected_behavior": "no_tool",
    })

    def context(version):
        return CC.ConversationContext(
            conversation_id="conv-skill", revision=1, prior_context=prior,
            skill_context=CC.SkillContext(
                skill_id=skill_v1.skill_id, version=version,
                allowed_tools=skill_v1.allowed_tools))

    assert conversation_binding(spec, context=context("1")) != \
        conversation_binding(spec, context=context("2"))
