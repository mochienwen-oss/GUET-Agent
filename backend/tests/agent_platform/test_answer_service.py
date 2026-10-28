import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.core.task import interpret_task
from agent.runtime import answer_service as A, graph, grounded_answer as G
from test_grounded_answers import ledger


def test_reference_material_can_explain_comparison_but_not_personal_applicability():
    question = "我是2024级人工智能学生，能按2025级培养方案判断修业年限吗？"
    task = interpret_task(question)
    packet = G.evidence_packet({"question": question, "task": task, "request_scope": task.subject_scope,
                               "evidence": ledger("修业年限3至6年。", "2025级人工智能专业培养方案(主修)")})
    assert packet["evidence"] and packet["evidence"][0]["applicability"] == "reference_only"
    action = A.decide_next(task, packet)
    assert action.status == "missing_policy" and action.action == "deliver"
    assert "2024" in action.text and "2025" in action.text and "尚缺少" in action.text
    assert "请提供你的年级" not in action.text


def test_reference_cohort_does_not_waive_other_scope_conflicts():
    from agent.core.scope import RequestScope
    question = "我是2024级人工智能学生，能按2025级培养方案判断吗？"
    scope = RequestScope(cohort="2024", majors=("人工智能",), reference_cohorts=("2025",))
    allowed, rejected = G.allowed_evidence({"question": question, "request_scope": scope,
        "evidence": ledger("修业年限3至6年。", "2025级英语专业培养方案(主修)")})
    assert not allowed and rejected


def test_missing_score_row_is_policy_gap_not_school_wide_absence():
    task = interpret_task("四级考了450分，大学英语能认定多少分？")
    packet = {"question": task.goal, "evidence": [{"text": "CET6规则"}], "calculation_rules": []}
    action = A.decide_next(task, packet)
    assert action.status == "missing_policy"
    assert action.gaps[0]["exam"] == "CET4"
    assert "不表示学校没有" in action.text


def test_confirmed_gap_does_not_call_composition_model(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Confirmed scope gap must not ask model to invent applicability")
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", forbidden)
    question = "我是2024级学生，能按2025级培养方案判断修业年限吗？"
    result = asyncio.run(graph._grounded_final({"question": question,
        "evidence": ledger("修业年限3至6年。", "2025级人工智能专业培养方案(主修)")}, [], 0, None))
    assert result["answer"]["status"] == "missing_policy"
    assert "2024" in result["answer"]["text"] and "尚缺少" in result["answer"]["text"]


def test_program_gaps_and_model_suggestions_are_separate():
    answer = A.from_result({"final_answer": "本次未取得依据。", "contexts": [],
        "answer_audit": {"audit_id": "a1", "draft": {"status": "clarify", "missing": ["学校禁止所有2024级学生申请"]}}})
    assert answer["status"] == "no_evidence"
    assert answer["gaps"] == [] and answer["model_suggestions"]
    assert "禁止" not in answer["text"]
    assert answer["audit"] == {"audit_id": "a1"}


def test_real_identity_conflict_asks_for_input_not_policy():
    task = interpret_task("我是2024级，也是2025级学生。")
    result = A.decide_next(task, {"evidence": []})
    assert result.status == "needs_input" and result.gaps[0]["kind"] == "input"


def test_tool_failure_and_empty_evidence_are_distinct():
    from agent.core import ToolResult
    task = interpret_task("学业预警规定？")
    assert A.decide_next(task, {"evidence": []}).status == "no_evidence"
    failed = ToolResult(tool="search_regulations", ok=False, status="tool_failed")
    assert A.decide_next(task, {"evidence": []}, [failed]).status == "failed"


def test_delivery_does_not_expose_full_model_requests():
    answer = A.deliver("答案", [], audit={"audit_id": "a1", "final_requests": [{"messages": "internal"}]})
    assert answer.audit == {"audit_id": "a1"}


def test_invalid_model_draft_is_bounded_and_keeps_failed_status(monkeypatch):
    calls = []
    async def invalid(*args, **kwargs):
        calls.append(1)
        return ["invalid"]
    monkeypatch.setattr(graph.model, "compose", invalid)
    result = asyncio.run(graph._grounded_final({"question": "修业年限？", "evidence": ledger("3至6年。")}, [], 0, None))
    assert len(calls) == 2
    assert result["answer"]["status"] == "failed"
    assert "未能通过证据核验" in result["answer"]["text"]
