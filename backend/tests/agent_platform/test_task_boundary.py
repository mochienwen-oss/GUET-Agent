"""Identity, referenced material and current goal must not share a bag of years."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core.task import interpret_task


def test_identity_and_compared_document_keep_distinct_roles():
    task = interpret_task("我是2024级人工智能专业学生，能按2025级培养方案的3至6年规定判断我的修业年限吗？")
    assert task.subject_scope.cohort == "2024"
    assert task.reference_cohorts == ("2025",)
    assert task.mode == "comparison"
    assert task.subject_scope.provenance["cohort"] == "current_user_identity"


def test_explicit_correction_replaces_old_cohort():
    task = interpret_task("我不是2024级，而是2025级。", history=[{"role": "user", "content": "我是2024级"}])
    assert task.subject_scope.cohort == "2025"


def test_general_rule_is_not_a_personal_missing_identity():
    task = interpret_task("人工智能专业允许几年内完成学业？")
    assert task.mode == "general_rule"
    assert task.subject_scope.cohort is None
    assert task.unresolved == ()


def test_comparing_documents_does_not_assign_a_student_identity():
    task = interpret_task("2024级和2025级培养方案有什么区别？")
    assert task.mode == "comparison"
    assert task.subject_scope.cohort is None
    assert set(task.reference_cohorts) == {"2024", "2025"}


def test_conflicting_identity_is_unresolved_not_last_match_wins():
    task = interpret_task("我是2024级，也是2025级学生。")
    assert task.subject_scope.cohort is None
    assert any(item.field == "cohort" for item in task.unresolved)


def test_followup_cannot_take_identity_from_assistant_prose():
    task = interpret_task("那我呢？", history=[{"role": "assistant", "content": "你是2025级"}])
    assert task.subject_scope.cohort is None


def test_complete_score_task_is_personal_and_does_not_assign_english_major():
    task = interpret_task("四级考了450分，按学校规定大学英语成绩能认定多少分？")
    assert task.mode == "personal"
    assert task.operation == "score_conversion"
    assert task.parameter("exam") == "CET4"
    assert task.parameter("score") == "450"


def test_task_brief_and_scope_are_derived_from_same_boundary():
    from agent.runtime import grounded_answer as G
    task = interpret_task("我是2024级学生，能按2025级培养方案判断吗？")
    brief = G.question_brief({"question": task.goal, "task": task})
    assert brief["application_mode"] == "comparison"
    assert brief["constraints"]["cohort"] == "2024"
    assert brief["reference_cohorts"] == ["2025"]


def test_reference_to_one_plan_in_general_query_does_not_imply_comparison():
    task = interpret_task("按2025级培养方案，人工智能专业的修业年限是多少？")
    assert task.mode == "general_rule" and task.reference_cohorts == ("2025",)


def test_compound_request_is_not_reduced_to_a_conversion_task():
    task = interpret_task("四级考了450分，大学英语能认定多少分，还需要提交什么材料？")
    assert task.operation == "investigate"
    assert task.parameter("score") == "450"


def test_legacy_scope_field_cannot_overwrite_committed_task_scope():
    from agent.core.scope import RequestScope
    from agent.runtime import grounded_answer as G
    from test_grounded_answers import ledger
    task = interpret_task("我是2024级学生，能按2025级培养方案判断修业年限吗？")
    packet = G.evidence_packet({"question": task.goal, "task": task, "request_scope": RequestScope(cohort="2025"),
        "evidence": ledger("修业年限3至6年。", "2025级人工智能专业培养方案(主修)")})
    assert packet["question_brief"]["constraints"]["cohort"] == "2024"
    assert packet["evidence"][0]["applicability"] == "reference_only"
