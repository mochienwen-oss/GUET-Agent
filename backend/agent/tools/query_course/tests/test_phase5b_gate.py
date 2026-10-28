# -*- coding: utf-8 -*-
"""阶段5B Gate-1 回归：矩阵粘连拆分、来源状态和候选库边界。"""
import json
import io
from pathlib import Path

import pytest

from agent.tools.query_course.scripts import build_plan_db as B


def _names(*values):
    return {B.join_key(value): value for value in values}


def test_compound_token_accepts_one_unique_full_segmentation():
    names = _names("计算机组成与系统结构", "数据库系统原理A", "数据库系统原理实验")
    result = B.split_compound_token("计算机组成与系统结构数据库系统原理A", names)
    assert result == ["计算机组成与系统结构", "数据库系统原理A"]


def test_compound_token_rejects_ambiguous_full_segmentations():
    names = _names("甲", "乙丙", "甲乙", "丙")
    assert B.split_compound_token("甲乙丙", names) is None


def test_compound_token_rejects_partial_coverage():
    names = _names("甲课程", "乙课程")
    assert B.split_compound_token("甲课程未知乙课程", names) is None


def test_compound_token_supports_one_unambiguous_matrix_basename():
    names = _names("写作与沟通1(结构性思维)", "毕业设计(论文)")
    result = B.split_compound_token("毕业设计(论文)写作与沟通1", names)
    assert result == ["毕业设计(论文)", "写作与沟通1(结构性思维)"]


def test_parse_matrix_records_the_original_token_and_unique_split():
    rows = [(12, "| 要求1 | 1.1 指标文本 | 甲课程乙课程 |")]
    splits = []
    _, inverse, unmatched = B.parse_matrix(
        rows, _names("甲课程", "乙课程"), lambda *_: None, split_log=splits
    )
    assert inverse == {"甲课程": ["1.1"], "乙课程": ["1.1"]}
    assert unmatched == []
    assert splits == [{
        "line": 12,
        "token": "甲课程乙课程",
        "courses": ["甲课程", "乙课程"],
        "indicator": "1.1",
        "requirement": "要求1",
        "reason": "unique_full_course_name_segmentation",
    }]


def test_estate8_missing_or_invalid_audit_is_a_hard_failure(monkeypatch):
    monkeypatch.setattr(B, "SEC8_TABLE", "__definitely_missing_sec8_audit__.json")
    with pytest.raises(RuntimeError, match="先修图审计表"):
        B._estate8("甲.md")

    payload = json.dumps({"甲.md": {"estate8": "unknown"}}, ensure_ascii=False)
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.StringIO(payload))
    with pytest.raises(RuntimeError, match="非法 estate8"):
        B._estate8("甲.md")


def test_default_build_output_is_candidate_not_runtime_database():
    output = Path(B.DEFAULT_OUTPUT_PATH)
    assert output.name == "plan_db.candidate.json"
    assert output.parent == Path(B.WORK_DIR)
    assert output != Path(B.OUT_DIR) / "plan_db.json"


def test_module_credit_parser_accepts_observed_wording_variants():
    rows = [(1, (
        "毕业总学分不低于165,其中:通识选修课程5个学分,"
        "专业教育课24学分,实践教学34分。"
    ))]
    modules, total = B.parse_module_credits(rows, lambda *_: None)
    assert total == 165
    assert modules == {
        "通识选修课程": 5,
        "专业教育课": 24,
        "实践教学": 34,
    }


def test_module_credit_parser_keeps_specialized_module_names_whole():
    rows = [(1, (
        "毕业总学分不低于158.5学分。其中:"
        "数学与自然科学基础课程27学分,"
        "专业类基础课程23学分,专业核心课程15.5学分。"
    ))]
    modules, total = B.parse_module_credits(rows, lambda *_: None)
    assert total == 158.5
    assert modules == {
        "数学与自然科学基础课程": 27,
        "专业类基础课程": 23,
        "专业核心课程": 15.5,
    }


def test_module_credit_sum_is_a_minimum_requirement_not_strict_equality():
    assert B._credits_meet_requirement(165.5, 163) is True
    assert B._credits_meet_requirement(163, 163) is True
    assert B._credits_meet_requirement(147, 148) is False
    assert B._credits_meet_requirement(147, None) is None


def test_missing_authoritative_cover_credit_requires_review():
    assert "cover_credits_missing" in B.REVIEW_CODES
