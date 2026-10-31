# -*- coding: utf-8 -*-
"""表格读取与统计工具：理解需求由模型负责，计算与文件产出由程序负责。

对应实施计划 3.3 的三条硬要求：

1. 只向模型暴露列名/行数/少量样本，不把整表塞进上下文；
2. 空值、缺考、零分不默认等价 —— 缺分不参与均值，也不当 0；
3. 结果写成新的会话文件（role='result'），原上传文件不改写。
"""
from __future__ import annotations

import io
from pathlib import Path

from agent.runtime.context import RuntimeContext

DESCRIBE = ("读取会话内一个表格的结构：工作表、列名、行数、前几行样本，以及公式情况。"
            "处理表格前先用它确认列名，不要猜列名。")
COMPUTE = ("对会话内的表格执行明确的统计并生成新的 .xlsx："
           "group_stats 按列分组求均值/有效数/缺分数；weighted 按给定权重算加权总分；"
           "missing 统计各列缺失；duplicates 按指定列查重。只做用户明确要求的计算。")


def _session():
    from database.db import SessionLocal
    return SessionLocal()


def _file(ctx: RuntimeContext, file_id: str):
    from documents import chat_files
    db = _session()
    try:
        row = chat_files.get(db, file_id, user_id=ctx.user_id,
                             conversation_id=ctx.conversation_id)
        db.expunge(row)
        return row
    finally:
        db.close()


def describe_table(runtime_context: RuntimeContext, file_id: str, sheet: str | None = None):
    from documents import chat_files
    row = _file(runtime_context, file_id)
    info = chat_files.describe(row, sheet)
    notes = []
    if info.formula_cells and info.uncached_formulas:
        notes.append("该表有 %d 个公式单元格没有缓存值，程序无法按公式重新计算；"
                     "涉及这些单元格的统计可能不完整。" % info.uncached_formulas)
    elif info.formula_cells:
        notes.append("该表含 %d 个公式，已使用文件里已保存的计算结果，未重新计算。" % info.formula_cells)
    if info.encoding:
        notes.append("CSV 编码：%s。" % info.encoding)
    return {"status": "success", "data": {
        "file_id": row.id, "filename": row.filename, "sheet": info.sheet,
        "sheets": info.sheets, "columns": info.columns, "rows": info.rows,
        "sample": info.sample, "notes": notes}}


def _need(frame, columns, label):
    missing = [name for name in columns if name not in frame.columns]
    if missing:
        raise ValueError("%s里没有这些列：%s。实际列有：%s"
                         % (label, "、".join(missing), "、".join(map(str, frame.columns))))


def _number(frame, column):
    import pandas as pd
    converted = pd.to_numeric(frame[column], errors="coerce")
    # 原始为空 → 缺失；原始非空但转不成数字 → 也按缺失处理，但在汇总里计数说明。
    broken = frame[column].notna() & converted.isna()
    return converted, int(broken.sum())


def compute_table(runtime_context: RuntimeContext, file_id: str, operation: str,
                  sheet: str | None = None, group_by: list[str] | None = None,
                  values: list[str] | None = None, weights: dict[str, float] | None = None,
                  id_column: str | None = None):
    import pandas as pd
    from documents import chat_files

    row = _file(runtime_context, file_id)
    info = chat_files.describe(row, sheet)
    if info.formula_cells and info.uncached_formulas:
        raise ValueError("该表有 %d 个公式单元格没有缓存值，无法可靠计算。"
                         "请在 Excel 里打开并保存一次，或提供已算好的数值列。" % info.uncached_formulas)

    frame, _ = chat_files._frame(Path(row.path), info.sheet)
    group_by, values = list(group_by or []), list(values or [])
    warnings: list[str] = []

    if operation == "group_stats":
        _need(frame, group_by + values, "表格")
        rows = []
        for key, part in frame.groupby(group_by, dropna=False, sort=True):
            key = key if isinstance(key, tuple) else (key,)
            record = {name: (None if pd.isna(value) else value) for name, value in zip(group_by, key)}
            record["行数"] = int(len(part))
            for column in values:
                numbers, broken = _number(part, column)
                record["%s_均值" % column] = None if numbers.notna().sum() == 0 else round(float(numbers.mean()), 2)
                record["%s_有效数" % column] = int(numbers.notna().sum())
                record["%s_缺分数" % column] = int(len(part) - numbers.notna().sum())
                if broken:
                    warnings.append("列 %s 有 %d 个非空值不是数字，已按缺失处理。" % (column, broken))
            rows.append(record)
        result = pd.DataFrame(rows)
        summary = "按 %s 分成 %d 组，统计了 %s 的均值、有效数与缺分数。" % (
            "、".join(group_by), len(rows), "、".join(values))

    elif operation == "weighted":
        columns = list((weights or {}).keys())
        _need(frame, columns, "表格")
        total = sum(float(weight) for weight in weights.values())
        if abs(total - 1.0) > 1e-6:
            warnings.append("权重之和为 %s，不等于 1，结果按给定权重直接加权。" % round(total, 4))
        parts, broken_total = [], 0
        for column in columns:
            numbers, broken = _number(frame, column)
            broken_total += broken
            parts.append(numbers * float(weights[column]))
        stacked = pd.concat(parts, axis=1)
        complete = stacked.notna().all(axis=1)
        result = frame.copy()
        result["加权总分"] = stacked.sum(axis=1, skipna=False).round(2)
        missing_rows = int((~complete).sum())
        if missing_rows:
            warnings.append("有 %d 行因为缺少加权项而未计算（保留为空，未按 0 处理）。" % missing_rows)
        if broken_total:
            warnings.append("有 %d 个非空值不是数字，已按缺失处理。" % broken_total)
        summary = "按 %s 计算加权总分；%d 行完整计算，%d 行因缺分留空。" % (
            "、".join("%s×%s" % (column, weights[column]) for column in columns),
            int(complete.sum()), missing_rows)

    elif operation == "missing":
        rows = []
        for column in frame.columns:
            empty = int(frame[column].isna().sum())
            rows.append({"列": str(column), "缺失数": empty, "有效数": int(len(frame) - empty),
                         "缺失比例": round(empty / len(frame), 4) if len(frame) else 0})
        result = pd.DataFrame(rows)
        summary = "共 %d 行，逐列统计了缺失情况。" % len(frame)

    elif operation == "duplicates":
        _need(frame, [id_column], "表格")
        counts = frame[id_column].value_counts(dropna=True)
        duplicated = counts[counts > 1]
        result = pd.DataFrame({"值": duplicated.index, "出现次数": duplicated.values})
        summary = "%d 个不同的值中有 %d 个重复。" % (int(counts.size), int(duplicated.size))
        if int(duplicated.size) == 0:
            summary = "按 %s 检查，没有重复值。" % id_column

    else:
        raise ValueError("不支持的统计方式：%s" % operation)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        result.to_excel(writer, index=False, sheet_name="统计结果")
        if warnings:
            pd.DataFrame({"说明": warnings}).to_excel(writer, index=False, sheet_name="处理说明")
    db = _session()
    try:
        saved = chat_files.save_result(db, user_id=runtime_context.user_id,
                                       conversation_id=runtime_context.conversation_id,
                                       filename="%s_%s.xlsx" % (Path(row.filename).stem, operation),
                                       data=buffer.getvalue())
        payload = chat_files.public(saved)
    finally:
        db.close()
    return {"status": "success",
            "data": {"summary": summary, "warnings": warnings, "rows": int(len(result)),
                     "result_filename": payload["filename"]},
            "file": payload}
