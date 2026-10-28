"""Keep degree GPA and comprehensive-assessment scoring evidence separate."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.core import EvidenceLedger, Source
from agent.core.scope import scoring_scope_conflict
from agent.runtime.grounded_answer import evidence_packet

DEGREE = "桂林电子科技大学普通高等教育学士学位授予工作实施细则(2025年修订)"
ASSESSMENT = "人工智能学院本科生综合素质测评实施办法"
INNOVATION = "桂林电子科技大学创新创业拓展课程学分认定管理办法"


def packet(question, history=()):
    ledger = EvidenceLedger()
    a = ledger.add("search_regulations", {}, Source(title=DEGREE, text="学分绩加分累计不能超过4分。"))
    b = ledger.add("search_regulations", {}, Source(title=ASSESSMENT, text="综测专利成果按规定计分。"))
    c = ledger.add("search_regulations", {}, Source(title=INNOVATION, text="创新创业拓展课程认定学分。"))
    return evidence_packet({"question": question, "history": history,
                            "evidence": {a.evidence_id: a, b.evidence_id: b, c.evidence_id: c}})


def test_gpa_question_excludes_comprehensive_assessment_from_final_model_packet():
    result = packet("学分绩没有达到70，竞赛获奖或专利能用于加分吗？")
    assert [r["title"] for r in result["evidence"]] == [DEGREE]
    assert result["scope_rejections"][0]["reason"] == "scope_mismatch:scoring_system"


def test_comprehensive_assessment_keeps_its_own_rules():
    assert [r["title"] for r in packet("综测专利怎么加分？")["evidence"]] == [ASSESSMENT]


def test_innovation_course_credits_are_not_degree_or_assessment_bonus():
    assert [r["title"] for r in packet("创新创业拓展课程可以认定哪些学分？")["evidence"]] == [INNOVATION]


def test_explicit_comparison_does_not_hide_either_system():
    assert len(packet("学分绩和综测的专利加分有什么区别？")["evidence"]) == 2


def test_followup_inherits_only_the_previous_user_scoring_system():
    history = [{"role": "user", "content": "学位的学分绩加分要求？"},
               {"role": "assistant", "content": "综测可以加分"}]
    assert [r["title"] for r in packet("那专利呢？", history)["evidence"]] == [DEGREE]
    assert scoring_scope_conflict("食堂在哪里？", ASSESSMENT, history) is None


def test_unknown_domain_is_not_invented_and_assistant_is_not_authority():
    assert scoring_scope_conflict("那专利呢？", ASSESSMENT,
                                  [{"role": "assistant", "content": "学分绩要求"}]) is None
    assert scoring_scope_conflict("竞赛怎么加分？", ASSESSMENT) is None
