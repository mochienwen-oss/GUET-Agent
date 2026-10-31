# -*- coding: utf-8 -*-
"""单表逻辑列校验：只规范化尾部空列，绝不搬运内容。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.tools.search_regulations.scripts.recall import table_check as tc  # noqa: E402


def test_a_well_formed_three_column_table_passes():
    block = "| 类别 | 成绩A | 大学英语成绩 |\n|---|---|---|\n| CET4 | 425≤A<567 | A×0.1764 |\n"
    result = tc.check_block(block)
    assert len(result) == 1
    assert result[0]["ok"] is True
    assert result[0]["cols"] == 3
    assert result[0]["padded"] == 0


def test_a_row_with_extra_content_beyond_the_header_fails():
    """第 width 列之后**有内容** → 失败。不做任何猜测性搬运。"""
    block = ("| 甲 | 乙 |\n|---|---|\n| 1 | 2 | 3 |\n")
    result = tc.check_block(block)[0]
    assert result["ok"] is False
    assert result["reason"] == "row_overflows"
    assert "溢出列有内容" in result["detail"]


def test_trailing_empty_cells_may_be_omitted():
    """尾部空列被省略是 markdown 常态 → 补齐即可，不算错。"""
    block = ("| 甲 | 乙 | 丙 |\n|---|---|---|\n| 1 | 2 |\n| 3 | 4 | 5 |\n")
    result = tc.check_block(block)[0]
    assert result["ok"] is True
    assert result["padded"] == 1


def test_separator_must_match_the_header_width():
    block = "| 甲 | 乙 |\n|---|\n| 1 | 2 |\n"
    result = tc.check_block(block)[0]
    assert result["ok"] is False
    assert result["reason"] == "separator_mismatch"


def test_a_pipe_run_without_a_separator_row_is_reported():
    """看着像表却没有分隔行 = 缺表头的碎片，不能静默当正文入库。"""
    block = "| 甲 | 乙 |\n| 1 | 2 |\n"
    result = tc.check_block(block)[0]
    assert result["ok"] is False
    assert result["reason"] == "no_separator"


def test_each_table_is_judged_on_its_own_width():
    """裁决二.2：不要求不同表具有相同列数。"""
    block = ("| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n\n"
             "| 丙 | 丁 | 戊 | 己 |\n|---|---|---|---|\n| 3 | 4 | 5 | 6 |\n")
    results = tc.check_block(block)
    assert [r["ok"] for r in results] == [True, True]
    assert [r["cols"] for r in results] == [2, 4]


def test_two_tables_in_one_block_one_bad_isolates_that_block():
    block = ("| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n\n"
             "| 丙 | 丁 |\n|---|---|\n| 1 | 2 | 3 |\n")
    report = tc.check_tables([{"chunk_id": "7:0", "title": "某办法", "text": block}])
    assert report["tables_seen"] == 2
    assert report["passed"] == 1
    assert len(report["failed"]) == 1
    assert report["failed"][0]["chunk_id"] == "7:0"
    assert report["failed"][0]["raw"] == block      # 隔离报告必须带原文


def test_prose_with_no_table_is_not_counted():
    report = tc.check_tables([{"chunk_id": "7:1", "title": "某办法", "text": "第一条 正文。\n"}])
    assert report["tables_seen"] == 0 and report["passed"] == 0 and report["failed"] == []