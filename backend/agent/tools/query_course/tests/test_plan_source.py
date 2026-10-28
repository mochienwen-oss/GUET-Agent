# -*- coding: utf-8 -*-
"""来源记录：行内容指纹是稳定身份，不受行号漂移影响。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.tools.query_course.scripts import plan_source as ps  # noqa: E402


def test_row_hash_is_stable_for_the_same_line():
    line = "| 实践教学课程 | 独立设置的实验课程 | 090052 | 模拟法学实践:模拟仲裁 |"
    assert ps.row_hash(line) == ps.row_hash(line)


def test_row_hash_ignores_cosmetic_whitespace():
    """排版性空白变化（重洗时常见）不算内容变了。"""
    assert ps.row_hash("| 甲 | 乙 |") == ps.row_hash("|甲|乙|")
    assert ps.row_hash("高阶英语 1") == ps.row_hash("高阶英语1")


def test_row_hash_changes_when_content_changes():
    """学分从 32 改成 2 —— 这正是 F2 要分辨的那种改动。"""
    a = "| 专业限选课程 | 030246 | 软件安全 | 专业限选 | 32 | 32 |"
    b = "| 专业限选课程 | 030246 | 软件安全 | 专业限选 | 2 | 32 |"
    assert ps.row_hash(a) != ps.row_hash(b)


def test_row_hash_is_full_width_insensitive():
    """半角/全角同义（本项目栽过这类副本）。"""
    assert ps.row_hash("大学英语（2）") == ps.row_hash("大学英语(2)")


def test_read_lines_folds_crlf(tmp_path):
    path = tmp_path / "a.md"
    path.write_bytes("甲\r\n乙\r\n".encode("utf-8"))
    assert ps.read_lines(path) == ["甲", "乙", ""]


def test_source_fingerprint_is_deterministic_and_byte_sensitive(tmp_path):
    (tmp_path / "培养方案").mkdir()
    f = tmp_path / "培养方案" / "甲.md"
    f.write_text("第一条\n", encoding="utf-8")
    first = ps.source_fingerprint(tmp_path / "培养方案")
    assert first == ps.source_fingerprint(tmp_path / "培养方案")
    f.write_text("第一条。\n", encoding="utf-8")
    assert first != ps.source_fingerprint(tmp_path / "培养方案")


def test_original_chunk_idx_style_line_numbers_are_not_identity():
    """把这条写下来：行号**不是**身份 —— 同一行换个位置，row_hash 不变。"""
    line = "| 000042 | 思想道德与法治 | 通识必修 | 必修 | 3 | 48 |"
    assert ps.row_hash(line) == ps.row_hash(line)          # 内容相同
    assert ps.row_hash("第 1 行" + line) != ps.row_hash(line)   # 内容不同才变