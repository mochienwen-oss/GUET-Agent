# -*- coding: utf-8 -*-
"""名称归一化 —— **单一来源**。

`scripts/build_plan_db.py`（底座抽取）与 `backend/tools/plan.py`（工具层）
都必须用这一份。各写一份必然漂移，而这份漂移**不会报错、只会让匹配率悄悄掉下来**
—— 本项目已经在这类静默失配上栽过（半角/全角副本、`毕业设计（论文）` vs `毕业设计(论文)`）。
"""
import re

CJK = r"一-鿿㐀-䶿"
FW = str.maketrans({"（": "(", "）": ")", "，": ",", "：": ":", "；": ";", "、": ",", "－": "-"})


def norm_cjk(s):
    """去掉夹在两个汉字之间的空白（PDF 字距装饰），但**保留**汉字与西文之间的空格。

    `马克思 主义学 院` → `马克思主义学院`   ✓
    `高阶英语 1(六级)` → 不变（空格在 语|1 之间，不是 CJK-CJK）  ✓
    """
    return re.sub(r"(?<=[%s])\s+(?=[%s])" % (CJK, CJK), "", (s or "").strip())


def join_key(s):
    """**跨表匹配键**：归一化 + 全角→半角 + 去全部空白。

    两张表用不同写法指同一门课时必须靠它对齐。
    """
    return re.sub(r"\s+", "", norm_cjk(s).translate(FW))


def cell_num(s):
    """数值单元格：顺带修 `0.2 5` → `0.25`（数字被断开）。修不了就原样返回。"""
    s = re.sub(r"\s+", " ", (s or "").strip())
    return re.sub(r"(?<=\d)\s+(?=\d)", "", s).strip()


def to_float(s):
    try:
        return float(cell_num(s))
    except (TypeError, ValueError):
        return None