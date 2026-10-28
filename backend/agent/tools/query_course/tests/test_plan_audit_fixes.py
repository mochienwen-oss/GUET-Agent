# -*- coding: utf-8 -*-
"""阶段5B 审计 F1 / F2 的回归测试。

F1：课程名含冒号的**真课程**不得被当汇总行丢掉；
F2：学分笔误的特判必须按**内容键**（课号），不得按行号。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.tools.query_course.scripts import build_plan_db as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[5]
SRC = ROOT / "_md" / "培养方案"


# ── F1：汇总行判据 ──────────────────────────────────────────────────────


def test_a_real_course_name_with_a_colon_is_not_a_summary_row():
    """★ F1 的核心：`模拟法学实践:模拟仲裁` 是真课程，不是模块汇总行。"""
    cells = ["实践教学课程", "独立设置的实验课程", "090052",
             "模拟法学实践:模拟仲裁", "独立设置的实验课程", "必修",
             "1", "16", "", "", "4周", "2- 夏", "必修", "法学院"]
    assert B._is_summary_name(cells[3]) is False


def test_a_summary_marker_in_the_course_name_cell_is_a_summary_row():
    """汇总标记出现在**课程名那一格**时才算汇总行。"""
    for marker in ("要求学分:无", "要求学分:2", "要求子模块数:1", "要求学分：21"):
        assert B._is_summary_name(marker) is True, marker


def test_a_summary_marker_in_a_module_cell_does_not_kill_a_real_course():
    """★★ 回归：**模块列**带 `要求学分:4` 的行是一节真课，不许整行丢掉。

    实测（高分子材料 293 行）：`|  | 思维模块课程 | 要求学分:4 | 000230 |
    计算思维与人工智能导论(理工版) | …` —— 扫所有列会把 000230 一起误杀；
    那一次错误让全库课程数从 7149 掉到 6731、unmatched 从 152 涨到 2345。
    """
    name = "计算思维与人工智能导论(理工版)"
    assert B._is_summary_name(name) is False

    rec = B.build_one(str(SRC / "2025级高分子材料与工程专业培养方案(主修).md")) \
        if SRC.is_dir() else None
    if rec is not None:
        assert any(c["code"] == "000230" for c in rec["courses"]), "000230 被误杀"


def test_a_summary_marker_in_the_course_code_cell_is_a_duplicate_artifact():
    """合并单元格展开会把前一门课复制数行，只把课号换成「要求学分」。"""
    idx = {"课程代码": 3, "课程名称": 4, "学分": 7}
    cells = ["", "", "四史教育", "要求学分:1", "中共党史", "通识必修", "必修", "1"]
    assert B._is_data_row(cells, idx) is False


def test_code_anchored_rows_with_the_same_code_merge_name_fragments():
    rows = [
        (10, "| | | | 000228 | 写作与沟通1(结构性思 | 通识必 | 必修 | 1 | 16 | 16 | | | 3 | 必 | 学院 |"),
        (11, "| | | | 000228 | 维) | 修 | 必修 | 1 | 16 | 16 | | | 3 | 修 | 学院 |"),
    ]
    courses, _ = B.parse_plan_table_code_anchored(rows, lambda *_: None)
    assert len(courses) == 1
    assert courses[0]["code"] == "000228"
    assert courses[0]["name"] == "写作与沟通1(结构性思维)"
    assert courses[0]["src_line"] == 10
    assert courses[0]["_src_end_line"] == 11


def test_a_cell_merely_containing_the_words_is_not_a_summary_row():
    """整格匹配 —— 不能因为格子里出现「要求学分」四个字就误杀。"""
    assert B._is_summary_name("关于要求学分认定的说明") is False


# ── F2：特判键 ──────────────────────────────────────────────────────────


def test_credit_fix_keys_are_content_based_not_line_based():
    """★ F2：键里不许出现行号（int）—— 行号会随语料重洗漂移。"""
    for key in B.CREDIT_FIXES:
        assert len(key) == 2
        assert isinstance(key[0], str) and key[0].endswith(".md")
        assert isinstance(key[1], str), "特判键必须是课号（内容），不得是行号：%r" % (key,)
        assert not isinstance(key[1], int)


def test_credit_fix_targets_are_named_by_course_code():
    assert B.CREDIT_FIXES[("2025级密码科学与技术专业培养方案(主修).md", "030246")] == 2.0
    assert B.CREDIT_FIXES[("2025级密码科学与技术专业培养方案(主修).md", "030245")] == 2.0


# ── 真实语料上的效果（需要本地 _md）─────────────────────────────────────

pytestmark_corpus = pytest.mark.skipif(not SRC.is_dir(), reason="需要本地 _md/培养方案")


@pytest.mark.skipif(not SRC.is_dir(), reason="需要本地 _md/培养方案")
def test_f1_recovers_the_colon_courses_in_the_real_corpus():
    """法学 090049/090050/090051/090052 必须全部被抽到。"""
    rec = B.build_one(str(SRC / "2025级法学专业培养方案(主修).md"))
    codes = {c["code"] for c in rec["courses"]}
    for code in ("090049", "090050", "090051", "090052"):
        assert code in codes, "F1 未修好：%s 仍被丢弃" % code


@pytest.mark.skipif(not SRC.is_dir(), reason="需要本地 _md/培养方案")
def test_f2_fixes_the_right_courses_and_leaves_others_alone():
    """★ 两条一起验：目标课学分被修正，**且** 军事技能保持原文值（F2 曾修错行）。"""
    rec = B.build_one(str(SRC / "2025级密码科学与技术专业培养方案(主修).md"))
    by_code = {c["code"]: c for c in rec["courses"]}
    assert by_code["030246"]["credits"] == 2.0, "软件安全 学分未被修正"
    assert by_code["030245"]["credits"] == 2.0, "侧信道分析技术 学分未被修正"

    # 源里军事技能的学分是 0.0 —— 它**不该**被任何特判碰到
    src = [ln for ln in (SRC / "2025级密码科学与技术专业培养方案(主修).md")
           .read_text(encoding="utf-8").split("\n") if "军事技能" in ln]
    assert src, "语料里找不到军事技能行"
    military = [c for c in rec["courses"] if "军事技能" in c["name"]]
    assert military, "军事技能没被抽到"
    assert military[0]["credits"] == 0.0, "F2 又修错了行：军事技能被改成了 %s" % military[0]["credits"]


@pytest.mark.skipif(not SRC.is_dir(), reason="需要本地 _md/培养方案")
def test_verified_extended_course_code_is_accepted():
    """PDF 已人工核实：`7 位数字 + X + 1 位数字` 是合法扩展课号。"""
    rec = B.build_one(str(SRC / "2025级信息安全专业培养方案(主修).md"))
    course = next(c for c in rec["courses"] if c["name"] == "内容安全")
    assert course["code"] == "0300295X0"
    assert not any(item["code"] == "course_code_invalid"
                   for item in rec["quality"]["review_items"])


def test_known_course_code_formats_are_explicitly_bounded():
    assert B.VALID_COURSE_CODE_RE.fullmatch("030055")
    assert B.VALID_COURSE_CODE_RE.fullmatch("0300295X0")
    assert not B.VALID_COURSE_CODE_RE.fullmatch("0300295X 0")
    assert not B.VALID_COURSE_CODE_RE.fullmatch("ABC")
