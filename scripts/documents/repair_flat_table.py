# -*- coding: utf-8 -*-
"""修复「扫描页表格被拍平」的 md 产物 —— 从原 PDF 按坐标重建表格。

用法（**项目根目录**执行）：
    PYTHONIOENCODING=utf-8 python scripts/documents/repair_flat_table.py            # 只看结果，不改文件
    PYTHONIOENCODING=utf-8 python scripts/documents/repair_flat_table.py --apply    # 落盘

做法：
  1. 把目标页按 SCALE 渲染 → OCR（**带坐标**）
  2. 取指定区域内的 box → `tablebuild.build_table` 按坐标聚类成网格
  3. 对**空单元格**做第二遍：按单元格矩形裁出来放大 ZOOM 倍再 OCR（实测能把
     「雅思 A=6.5 → 85」这种低置信度格救回来）
  4. 与脚本内 `EXPECTED` 真值逐格对比（真值是我对渲染图的**目视读值**），
     不一致就报出来
  5. `--apply` 时把 md 里那段拍平文字整行替换成 markdown 管道表

⚠️ 本脚本是**定点修复**（只认这一份文档这一张表），不是通用解析改造。
   通用化的位置在 `ocr2md.py`（整页扫描件目前不做表格重建），留作后续。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np            # noqa: E402
import pymupdf               # noqa: E402
from documents.ingest import tablebuild            # noqa: E402
from documents.ingest.ocr_engine import load_engine, ocr_image_with_boxes  # noqa: E402

SCALE = 2.0        # 整页渲染倍率（与 ocr2md 一致）
ZOOM = 8.0         # 空单元格二次 OCR 的放大倍率

# 目标：唯一一处「重级」乱码（audit_parse --md-dir _md 会报出来）
TARGET_PDF = ("guet数据文件/规章制度/培养方案管理制度/"
              "《桂林电子科技大学“大学英语”课程替换及成绩认定办法（试行）》（校教〔2025〕5号）.pdf")
TARGET_MD = ("_md/规章制度/培养方案管理制度/"
             "《桂林电子科技大学“大学英语”课程替换及成绩认定办法(试行)》(校教〔2025〕5号).md")
TARGET_PAGE = 3                     # 1-based（表在第 3 页）
TARGET_REGION = (45, 275, 560, 592)  # PDF 点：(x0, y0, x1, y1)

# ★ 验收真值：对渲染图的**目视读值**（`run/script-assets/_table_crop.png`）。
#   按**列**给，不按行给 —— 行的归属受「合并单元格垂直居中」影响（`雅思` 会落在
#   它 4 行里的第 2 行），那是排版事实不是缺陷；**要验的是「哪些折算分丢了」**，
#   也就是列里的值集合，与几何无关。
#   「托福 75≤A<100 → A」这一格 OCR 全页与放大都检不到（单个西文字母），
#   所以它是**人工补的**，在此显式记录，不假装是自动结果。
EXPECTED = [
    ["类别", "成绩A", "大学英语成绩"],
    ["CET4", "425≤A<567", "A×0.1764"],
    ["CET4", "A≥567", "100"],
    ["CET6", "425≤A<500", "A×0.2"],
    ["CET6", "A≥500", "100"],
    ["托福", "75≤A<100", "A"],
    ["托福", "A≥100", "100"],
    ["雅思", "A=6.0", "80"],
    ["雅思", "A=6.5", "85"],
    ["雅思", "A=7.0", "95"],
    ["雅思", "A≥7.5", "100"],
]
# 人工补值：按「同行 成绩A 的值」定位（不用行列号，几何无关）
MANUAL_FIX = {"75≤A<100": ("大学英语成绩", "A")}
# ★ 合并列：源表里「类别」是**纵向合并单元格**（CET4 跨 2 行、雅思跨 4 行）。
#   它在扫描图上的文字是**垂直居中**的，落在两行数据的**行缝里**（实测 CET4 的
#   y 区间 330~342，上下两行数据是 316~328 / 344~358 —— 一个都不重叠），
#   所以坐标聚类会把它单独聚成一行。要还原成 markdown（不支持合并单元格），
#   就得把它铺到它两侧的数据行上 —— 但**只有声明过的列才铺**：
#   值列留空是刻意的（空可能是「检测器漏检」，铺了就会静默填错值）。
MERGE_COLS = (0,)


def fill_merged(tb) -> int:
    """把合并列里「垂直居中」的值，铺到同列最近的数据行上；随后丢掉只剩它的孤儿行。"""
    rows, centers = tb["rows"], tb["cell_centers"]
    n_fill = 0
    for c in MERGE_COLS:
        anchors = [(centers[(r, c)], rows[r][c]) for r in range(len(rows)) if rows[r][c].strip()]
        if not anchors:
            continue
        for r in range(len(rows)):
            if rows[r][c].strip():
                continue
            y = centers.get((r, c))
            if y is None:
                y = sum(tb["row_ranges"][r]) / 2
            rows[r][c] = min(anchors, key=lambda a: abs(a[0] - y))[1]
            n_fill += 1
    # 孤儿行：整行只有合并列有内容 → 值已铺到邻居，删掉这行
    keep = [r for r in rows
            if any(x.strip() for i, x in enumerate(r) if i not in MERGE_COLS)]
    dropped = len(rows) - len(keep)
    tb["rows"] = keep
    return n_fill * 100 + dropped


def render(page, clip=None, scale=SCALE):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return arr[:, :, :3]


def main() -> int:
    ap = argparse.ArgumentParser(description="修复扫描页被拍平的表格")
    ap.add_argument("--pdf", default=os.path.join(ROOT, TARGET_PDF))
    ap.add_argument("--md", default=os.path.join(ROOT, TARGET_MD))
    ap.add_argument("--page", type=int, default=TARGET_PAGE, help="1-based 页码")
    ap.add_argument("--region", type=float, nargs=4, default=TARGET_REGION,
                    metavar=("X0", "Y0", "X1", "Y1"), help="表格区域（PDF 点）")
    ap.add_argument("--apply", action="store_true", help="落盘（默认只打印）")
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    page = doc[a.page - 1]
    clip = pymupdf.Rect(*a.region)

    # ---- 1. 整页 OCR（带坐标），只取区域内的 ----
    #  ⚠️ OCR 的 box 是**渲染像素**，而 --region 是 **PDF 点** —— 必须换算，
    #     否则区域过滤一个都框不住（实测踩过：直接比只剩 3 个 box）。
    boxes = [(b[0] / SCALE, b[1] / SCALE, b[2] / SCALE, b[3] / SCALE, b[4])
             for b in ocr_image_with_boxes(render(page))]
    x0, y0, x1, y1 = a.region
    inside = [b for b in boxes if x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1]
    print(f"区域 {a.region} 内 {len(inside)} 个 box（整页 {len(boxes)} 个）")

    tb = tablebuild.build_table(inside)
    print(f"列带 {tb['bands']} 个，行 {len(tb['rows'])} 行，空单元格 {len(tb['empty'])} 个")

    # ---- 2. 空单元格：按格子矩形放大重 OCR ----
    fixed_by_zoom = {}
    for ri, ci in list(tb["empty"]):
        if ri >= len(tb["row_ranges"]):
            continue
        ry0, ry1 = tb["row_ranges"][ri]
        bx0, bx1 = tb["band_ranges"][ci]
        pad = 3
        cell = pymupdf.Rect(bx0 - pad, ry0 - pad, bx1 + pad, ry1 + pad)
        got = ocr_image_with_boxes(render(page, cell, ZOOM))
        text = " ".join(t for *_, t in sorted(got, key=lambda b: b[0])).strip()
        print(f"  空格 (行{ri}, 列{ci}) 放大 {ZOOM}x 重 OCR → {text!r}")
        if text:
            tb["rows"][ri][ci] = text
            fixed_by_zoom[(ri, ci)] = text

    # ---- 3. 合并列铺开（把「类别」还给它的每一行）----
    code = fill_merged(tb)
    print(f"  合并列铺开：补 {code // 100} 格、删除孤儿行 {code % 100} 行")

    # ---- 4. 人工补值（按同行「成绩A」的值定位，几何无关）----
    for row in tb["rows"]:
        key = (row[1] if len(row) > 1 else "").replace(" ", "")
        if key in MANUAL_FIX:
            col_name, val = MANUAL_FIX[key]
            ci = tb["rows"][0].index(col_name) if col_name in tb["rows"][0] else -1
            if ci >= 0 and not row[ci].strip():
                row[ci] = val
                print(f"  行「{key}」的「{col_name}」按目视读值人工补 → {val!r}")

    # ---- 5. 与真值逐行比对 ----
    print("\n重建结果：")
    for r in tb["rows"]:
        print("   " + " | ".join(r))
    ok = True
    if len(tb["rows"]) != len(EXPECTED):
        print(f"\n❌ 行数不符：重建 {len(tb['rows'])} vs 真值 {len(EXPECTED)}")
        ok = False
    for i, (got, exp) in enumerate(zip(tb["rows"], EXPECTED)):
        got = [c.replace(" ", "") for c in got]
        exp = [c.replace(" ", "") for c in exp]
        if got != exp:
            print(f"\n❌ 第 {i} 行不符：\n   重建 {got}\n   真值 {exp}")
            ok = False
    print("\n" + ("✅ 与目视真值**逐行逐格**一致（含原 md 丢失的 100 / A / 80 / 85 / 95）"
                  if ok else "⚠️ 存在不一致（见上）"))
    print(f"   自动重建 {len(tb['rows']) * tb['bands'] - len(tb['empty'])} 格、"
          f"放大救回 {len(fixed_by_zoom)} 格、人工补 {len(MANUAL_FIX)} 格")

    md_lines = tablebuild.to_markdown(tb["rows"])
    if not md_lines:
        print("❌ 未生成表格，中止")
        return 1

    # ---- 5. 替换 md 里的拍平段落 ----
    md = open(a.md, encoding="utf-8").read()
    lines = md.split("\n")
    hit = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("类别") and "≤" in ln:
            hit = i
            break
    if hit is None:
        print("❌ 在 md 里找不到拍平段落（应以「类别」开头且含 ≤）")
        return 1
    print(f"\nmd 命中行 {hit + 1}（{len(lines[hit])} 字）：{lines[hit][:70]}…")
    new = lines[:hit] + md_lines + lines[hit + 1:]
    print("替换后该处：")
    for l in new[hit: hit + len(md_lines)]:
        print("   " + l)

    if a.apply:
        open(a.md, "w", encoding="utf-8").write("\n".join(new))
        print(f"\n✅ 已写回 {a.md}")
    else:
        print("\n[DRY-RUN] 未写盘（加 --apply 落盘）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())