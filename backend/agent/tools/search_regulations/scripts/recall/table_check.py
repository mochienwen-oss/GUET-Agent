# -*- coding: utf-8 -*-
"""单表逻辑列校验 —— 纯函数：只读文本、不连库、不加载模型。

为什么需要（阶段5A 裁决 二）：规章里的换算表/条件表要**整表**入索引，
而"表被拍平/列错位"是这份语料已经发生过的事故（见 `backend/tablebuild.py` 头注释：
《大学英语》那张 3 列表被 OCR 拍成一行连排文字，折算值整批丢失）。
一张列对不齐的表静默入库，比不入库更坏 —— 模型会照着错位的列读数字。

判据（严格按裁决）：
  · **校验单位是单张表**，不要求不同表具有相同列数；
  · **允许尾部空单元格规范化补齐**（markdown 里尾部空列常被省略）；
  · 表头 / 分隔行 / 数据行的逻辑列数**对齐不了 → 该表不得入库**，进隔离报告；
  · **禁止自动猜测错位单元格** —— 只做"去尾部空列 / 补尾部空列"，
    任何在第 n 列之后**有内容**的单元格一律判失败，不搬运、不填补。
"""
from __future__ import annotations

from agent.tools.search_regulations.scripts.index_ops import indexing


def cells(line: str) -> list[str]:
    """一行 markdown 表格 → 单元格列表（去掉最外层那对 `|`）。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [part.strip() for part in text.split("|")]


def find_tables(text: str) -> list[list[str]]:
    """块内**每一段连续的 `|` 行**各算一张候选表，原样返回行列表。

    一个块里可能不止一张表（例如"认定标准"表后面还有别的表），
    所以按连续段切，而不是"整个块当一张表"。
    """
    tables, current = [], []
    for line in (text or "").splitlines():
        if line.lstrip().startswith("|"):
            current.append(line)
        else:
            if current:
                tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def check_table(lines: list[str]) -> dict:
    """校验**一张**表。返回 {"ok", "reason", "cols", "rows", "padded"}。

    失败原因取值：`no_separator` / `separator_mismatch` / `row_overflows`。
    """
    if len(lines) < 2:
        return {"ok": False, "reason": "no_separator", "cols": 0, "rows": len(lines), "padded": 0}
    header = cells(lines[0])
    if not indexing._is_table_sep(lines[1]):
        return {"ok": False, "reason": "no_separator", "cols": len(header),
                "rows": len(lines), "padded": 0}
    separator = cells(lines[1])
    if len(separator) != len(header):
        return {"ok": False, "reason": "separator_mismatch", "cols": len(header),
                "rows": len(lines), "padded": 0}

    width, padded = len(header), 0
    for offset, line in enumerate(lines[2:]):
        row = cells(line)
        # 只规范化**尾部**：第 width 列之后必须全空，否则判失败（不猜、不搬运）
        if any(part for part in row[width:]):
            return {"ok": False, "reason": "row_overflows", "cols": width,
                    "rows": len(lines), "padded": padded,
                    "detail": "第 %d 行列数 %d > 表头 %d，且溢出列有内容" % (offset + 3, len(row), width)}
        if len(row) < width:
            padded += 1                      # 尾部空列被省略 → 补齐（不算错）
    return {"ok": True, "reason": "", "cols": width, "rows": len(lines), "padded": padded}


def check_block(text: str) -> list[dict]:
    """校验块内**每一张**表，逐张返回结果。没有表 → 空列表。"""
    return [check_table(lines) for lines in find_tables(text)]


def check_tables(units: list[dict]) -> dict:
    """对一批分块跑校验。返回汇总 + 隔离报告（失败表的原文样张）。

    `units` 元素需含 `chunk_id` / `text`。没有任何表的块不计入。
    """
    passed, failed, tables_seen = 0, [], 0
    for unit in units:
        for result in check_block(unit.get("text") or ""):
            tables_seen += 1
            if result["ok"]:
                passed += 1
            else:
                failed.append({"chunk_id": unit.get("chunk_id"),
                               "title": unit.get("title", ""),
                               "reason": result.get("reason"),
                               "detail": result.get("detail", ""),
                               "cols": result["cols"], "rows": result["rows"],
                               "raw": unit.get("text") or ""})
    return {"tables_seen": tables_seen, "passed": passed, "failed": failed}