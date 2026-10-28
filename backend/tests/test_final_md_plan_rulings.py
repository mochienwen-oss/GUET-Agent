"""Deferred targeted checks for the owner's two source-bound credit rulings."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.tools.query_course.scripts import final_md_plan as P


def rows():
    return [{"code": code, "name": name, "credits": None, "corrections": [],
             "src": {"raw_fields": {"学分": "32"}},
             "confidence": {"level": "medium", "reasons": ["credits_unverified"]}}
            for code, name in (("030246", "软件安全"), ("030245", "侧信道分析技术"))]


def test_owner_ruling_changes_only_effective_values_and_preserves_raw_source():
    courses = rows()
    P.apply_reviewed_credits(Path("2025级密码科学与技术专业培养方案(主修).md"),
                             "8142d1d653bca6f06aac4e4c641ae5f9cf97d2bf9c94d41cdfb23cb4dc89bfd9", courses, [])
    assert [course["credits"] for course in courses] == [2.0, 2.0]
    assert all(course["src"]["raw_fields"]["学分"] == "32" for course in courses)
    assert all(course["corrections"][0]["by"] == "human:owner" for course in courses)


def test_changed_source_does_not_inherit_the_two_exceptions():
    courses = rows()
    before = deepcopy(courses)
    P.apply_reviewed_credits(Path("2025级密码科学与技术专业培养方案(主修).md"), "0" * 64, courses, [])
    assert courses == before


def test_changed_raw_value_is_not_silently_corrected():
    courses = rows()
    courses[0]["src"]["raw_fields"]["学分"] = "31"
    with pytest.raises(ValueError, match="源值已变化"):
        P.apply_reviewed_credits(Path("2025级密码科学与技术专业培养方案(主修).md"),
                                 "8142d1d653bca6f06aac4e4c641ae5f9cf97d2bf9c94d41cdfb23cb4dc89bfd9", courses, [])
