# tablebuild.py —— 扫描页**表格重建**（纯函数：只做坐标聚类，不碰 OCR / 文件）

# 为什么需要它：
#   `ocr2md.py` 对**整页扫描件**只做「整页 OCR → 逐行文本」。逐行文本对公文正文是对的
#   （「第X条」在行首，合并成段会让标题识别失效），但**表格会被拍平成一段连排文字**：
#   单元格之间没有分隔，行/列关系全部丢失。
#   实测后果（2026-09-28，本语料唯一一处重级乱码）：
#     《“大学英语”课程替换及成绩认定办法》的「认定标准」表（10 行 3 列）被 OCR 成
#     「类别成绩A大学英语成绩425≤A<567 A×0.1764 CET4 A≥567 …」
#     —— 折算分 100 / A / 80 / 85 / 95 **整批丢失**，答案里查不到「六级 500 分折算多少」。
#
# 为什么按坐标聚类可行：
#   RapidOCR 返回的每个文本块都带 4 点框。表格的**列**在 x 上是分离的（列间有空白带），
#   **行**在 y 上是对齐的。所以「x 投影找空白带 → 列」「y 投影聚类 → 行」就能还原网格，
#   不需要表格线检测模型。
#
# 实测数据（上述表，scale=2 的像素坐标）：
#   列带：类别 x205~270 · 成绩A x433~561 · 大学英语成绩 x736~881 → 列间空白 170+ px
#   行高：约 55~57 px（≈ 字号），行间无重叠
#
# ⚠️ 已知边界：**单个西文字母可能不被检测器检出**。实测该表「托福 75≤A<100」行的
#    折算值是单独的 `A`，全页 OCR 与 6 倍放大都检不到 —— 所以调用方必须把空单元格
#    报出来人工核，不能靠「补上一行的值」蒙混（那会静默填错值）。

import statistics

MIN_BANDS = 2          # 少于 2 个列带 → 不算表格，退回单列


def _row_height(boxes) -> float:
    hs = sorted(b[3] - b[1] for b in boxes)
    return hs[len(hs) // 2] if hs else 10.0


def column_bands(boxes, gap_min: float | None = None):
    """按 x 投影找**列带**：把各 box 的 x 区间并起来，相邻并集之间的空隙就是列缝。

    gap_min 默认取中位行高的 1.5 倍 —— 比字间距大、比列间距小。
    """
    if not boxes:
        return []
    if gap_min is None:
        gap_min = _row_height(boxes) * 1.5
    ivs = sorted((b[0], b[2]) for b in boxes)
    merged = [list(ivs[0])]
    for x0, x1 in ivs[1:]:
        if x0 - merged[-1][1] <= gap_min:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    return [(a, b) for a, b in merged]


def cluster_rows(boxes):
    """按 **y 区间重叠**聚类成行（返回 [[box, ...], ...]，行内按 x 排序）。

    ⚠️ 不能用「中心点距离 + 容差」：表格里**合并单元格的文字是垂直居中**的，
    它的中心正好落在两行数据之间（实测 `CET4` 的中心 671，上下两行数据是 655 / 711），
    中心距离法会把它单独聚成一行，于是 `CET4` 与它的数据行错位、整表垮掉。
    区间重叠法没有这个问题：`CET4` 的 y 区间压在上一行数据的区间里 → 归到那一行。
    """
    if not boxes:
        return []
    rows, cur = [], []
    for b in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        if cur:
            lo = min(x[1] for x in cur)
            hi = max(x[3] for x in cur)
            if b[1] >= hi or b[3] <= lo:      # 与当前行**完全没有重叠** → 换行
                rows.append(cur)
                cur = []
        cur.append(b)
    if cur:
        rows.append(cur)
    return [sorted(r, key=lambda b: b[0]) for r in rows]


def _cell_text(row_boxes, band) -> str:
    """落在该列带里的 box 文本拼起来（同一格可能有多个 box）。"""
    lo, hi = band
    parts = [b[4] for b in row_boxes if lo - 1 <= (b[0] + b[2]) / 2 <= hi + 1]
    return " ".join(p.strip() for p in parts if p.strip())


def build_table(boxes, gap_min: float | None = None):
    """坐标框 → 网格。

    boxes: [(x0, y0, x1, y1, text), ...]
    返回 {"rows": [[cell, ...], ...], "bands": int, "empty": [(r, c), ...], "ok": bool}

    ★ **不做「向下补齐」**（合并单元格的常见做法）。理由：空单元格有两种来源——
      真的合并了（该补）和**检测器漏检**（补了就填错值）。两者在坐标上分不开，
      所以一律留空并报进 `empty`，由调用方人工核对。实测该表就踩了这个：
      「托福 75≤A<100」的折算值是单独的 `A`，检测器漏检；若向下补齐会填成上一行的 `100`。
    """
    bands = column_bands(boxes, gap_min)
    rows = cluster_rows(boxes)
    if len(bands) < MIN_BANDS:
        return {"rows": [[b[4] for b in r] for r in rows], "bands": len(bands),
                "empty": [], "ok": False, "band_ranges": bands, "row_ranges": []}
    grid, empty = [], []
    for ri, r in enumerate(rows):
        cells = [_cell_text(r, band) for band in bands]
        for ci, c in enumerate(cells):
            if not c:
                empty.append((ri, ci))
        grid.append(cells)
    # 行/列的几何范围一并回传 —— 调用方要用它去**放大重 OCR** 空单元格，
# 以及把**合并单元格**的值铺回它两侧的数据行（见 scripts/repair_flat_table.py）。
    row_ranges = [(min(b[1] for b in r), max(b[3] for b in r)) for r in rows]
    cell_centers = {}
    for ri, r in enumerate(rows):
        for ci, band in enumerate(bands):
            bs = [b for b in r if band[0] - 1 <= (b[0] + b[2]) / 2 <= band[1] + 1]
            if bs:
                cell_centers[(ri, ci)] = (min(b[1] for b in bs) + max(b[3] for b in bs)) / 2
    return {"rows": grid, "bands": len(bands), "empty": empty, "ok": True,
            "band_ranges": bands, "row_ranges": row_ranges, "cell_centers": cell_centers}


# ---------------------------------------------------------------- 整页区域切分
# 上面是「已知这是表格，把它的 box 拼成网格」；下面是「**整页扫描件里哪一块是表格**」。
# 后者是做进 `ocr2md.py` 的那一步（原先是定点修复脚本 `repair_flat_table.py` 人工指定区域）。

MIN_ROWS = 3          # 少于 3 行不成表（2 行的「表」基本是两行排版文字）
MIN_FILL = 0.6        # 格子填充率下限 —— 见下「为什么填充率是必要的」
WIDE_RATIO = 0.62     # box 宽度 ≥ 本页最宽 box 的这个比例 → 判为「整行正文」
GAP_ROWS = 2.5        # 垂直间隔 > 中位行高的这么多倍 → 断开，另起一块


def _median_height(boxes) -> float:
    hs = sorted(b[3] - b[1] for b in boxes)
    return hs[len(hs) // 2] if hs else 10.0


def _fill_ratio(grid) -> float:
    n = sum(len(r) for r in grid)
    if not n:
        return 0.0
    return sum(1 for r in grid for c in r if c.strip()) / n


def _no_text_lost(run, grid) -> bool:
    """这一块里**每个 box 的文字都得落在网格里**。

    ⚠️ **实测：这条判据在当前实现下恒真**（不是「有效但漏」，是**永远不会失败**）——
      `column_bands` 返回的是各 box x 区间的**并集**，而每个 box 的中心必然落在
      自己那个区间里，`_cell_text` 又按「中心落在带内」归属 → 每个 box 必被某个格子收走。
      留着它是**为将来兜底**（改 `build_table` 的归属规则时它会立刻变成有效判据），
      不是当前的安全保证 —— 别把「判据通过」读成「表格没错」（见下表）。
    """
    blob = "".join(" ".join(r) for r in grid).replace(" ", "")
    for b in run:
        t = str(b[4]).strip().replace(" ", "")
        if t and t not in blob:
            return False
    return True


def _has_pipe(run) -> bool:
    """★ 这一条**才**是会真触发的闸门：单元格文字里含 `|`。

    网格要被渲染成 markdown 管道表，格子里的 `|` 会被下游（`vector._split_table`）
    当成列分隔符 → **整表的列数错位**，而且是静默的。OCR 把「丨 / |」这类竖笔
    误识成 `|` 并非不可能，所以宁可退回逐行，也不出结构错的表。
    """
    return any("|" in str(b[4]) for b in run)


def _try_table(run, min_rows, min_fill) -> dict | None:
    if len(run) < min_rows:
        return None
    tb = build_table(run)
    if not tb["ok"] or len(tb["rows"]) < min_rows:
        return None
    if tb["bands"] < MIN_BANDS + 1:
        # ★ 要求 **≥3 列**，2 列一律不认。这是一笔**明知代价的取舍**：
        #   2 个列带正是「双栏排版」的签名 —— 双栏正文按 y 聚行后，两栏都满、
        #   填充率也高，会**整页**被误判成一张 2 列表（把一份公文变成表格，
        #   比漏掉一张表严重得多）。而 2 列的真表格只是**漏**，不丢内容。
        #   ⚠️ 已知代价：2 列的真表（如「课程名 | 学分」）识别不出来，仍是逐行文本。
        #   本语料实测**真双栏 0 页**，若将来进双栏数据，这里要重新权衡。
        return None
    if _fill_ratio(tb["rows"]) < min_fill:
        return None
    if not _no_text_lost(run, tb["rows"]):
        return None
    if _has_pipe(run):
        return None
    return tb


def split_regions(boxes, min_rows: int = MIN_ROWS, min_fill: float = MIN_FILL,
                  wide_ratio: float = WIDE_RATIO):
    """整页 box → 按阅读顺序的若干块：`{"kind": "table"/"text", "boxes": [...], "tb": …}`。

    ★ **保序**：`boxes` 按**调用方给的顺序**（RapidOCR 的阅读顺序）编号，`text` 块**按原序号
      输出**，不按坐标重排。这条是为了让「没识别出表格的页」的输出与改造前**逐字节相同** ——
      否则 A/B 里混进了「正文顺序也变了」这个额外变量，测出什么都说不清。

    为什么靠坐标聚类就能判「这是表格」：
      · **列带**：正文各行 x 区间互相重叠（都从左边界起），必然并成 **1 个列带** →
        被 `MIN_BANDS` 挡掉。要凑出 ≥2 个分离的列带，文字必须真的分列排版。
      · **行数 + 填充率**：正文即使凑出 2 个列带（如「首行缩进 + 悬挂」），
        格子也大多是空的 → 填充率挡掉。
      · **整行正文打断**：宽度接近满页的 box 判为正文，直接断开当前块 ——
        表格单元格都是短的，正文行是长的。
      三重条件叠加后，本语料（实测**真双栏 0 页**）的误判面很小；
      万一误判，还有 `_no_text_lost` 兜底退回逐行。
    """
    items = list(enumerate(boxes))
    if not items:
        return []
    # ★ 参考宽度取**本页内容宽度**（最左到最右），**不是**「最宽的那个 box」。
    #   取最宽 box 会在「整页都是表」时自我塌缩：表里最宽的那个格子就是 w_ref，
    #   于是所有格子都够宽 → 全判成正文行 → 整张表识别不出来。
    #   （培养方案的教学进程表就是整页表格，实测培养方案 63 份里这类不少。）
    w_ref = (max(b[2] for _, b in items) - min(b[0] for _, b in items)) or 1.0
    gap = _median_height(boxes) * GAP_ROWS
    is_wide = lambda b: (b[2] - b[0]) >= wide_ratio * w_ref        # noqa: E731

    out, cur = [], []

    def flush():
        if not cur:
            return
        # 正文块按**原序号**还原（保序），表格块内部本就按 x 排序、不受影响
        run = [b for _, b in sorted(cur)]
        tb = _try_table(run, min_rows, min_fill)
        if tb is not None:
            out.append({"kind": "table", "boxes": run, "tb": tb})
        else:
            out.append({"kind": "text", "boxes": run})
        cur.clear()

    for i, b in items:
        if is_wide(b):
            flush()
            out.append({"kind": "text", "boxes": [b]})
            continue
        if cur and b[1] - max(x[3] for _, x in cur) > gap:
            flush()
        cur.append((i, b))
    flush()
    return out


def spread_merged(tb, cols=(0,)) -> list[tuple]:
    """把**合并单元格**的值铺回它跨越的数据行。返回铺过的 `[(行, 列, 值), ...]`。

    背景（这是几何事实，不是缺陷）：纵向合并单元格的文字是**垂直居中**的，落在两行
    数据的行缝里，与两边**都不重叠** → `cluster_rows` 会把它单独聚成一行。实测该表：

        | 类别 | 成绩A        | 大学英语成绩 |      ← 表头
        |      | 425≤A<567   | A×0.1764   |      ← 数据行（第 1 列空）
        | CET4 |             |            |      ← 「CET4」自成一行（合并单元格）
        |      | A≥567       | 100        |      ← 数据行

    markdown 不支持合并单元格，所以要么铺开、要么整列留空、要么行数翻倍。

    ★ **只在调用方声明的列上铺**（`cols`，默认第 0 列 = 最典型的「行标签列」）。
      为什么不敢全表自动铺：铺的前提是「这格空 = 被合并了」，但空还有另一个来源
      **检测器漏检**（实测「托福 75≤A<100」的折算值是个单字母 `A`，全页与 8 倍放大都
      检不到）。两者在坐标上分不开 —— 铺错了就是**静默填错值**，比留空严重得多。
      所以默认只铺第 0 列（行标签列，绝大多数表的第一列都是合并标签），
      其余列一律留空并报进 `empty`，交人工核验。

    ★ **按 y 找最近的锚点插值，不是「填满连续空格」** —— 实测踩过：填连续空格的写法
      会一路填到下一个非空为止，于是 `CET4`（跨 2 行）漏进了紧随其后的 `CET6` 段，
      把「425≤A<500」标成了 CET4（**静默贴错标签**）。最近锚点法能同时对上
      CET4 跨 2 行、雅思跨 4 行，因为它比的是几何距离：

        row0 | 425≤A<567 | A×0.1764   ← 最近锚点是 row1(CET4)     → CET4 ✓
        row1 | CET4（锚点，孤儿行）
        row2 | A≥567     | 100        ← 最近锚点是 row1(CET4)     → CET4 ✓
        row3 | 425≤A<500 | A×0.2      ← 距 row4(CET6) 更近          → CET6 ✓

    ★ **填充范围 = 该列全部空格，按最近的锚点取值**（与 `scripts/repair_flat_table.py`
      的 `fill_merged` 同规则，那份是**人工核验过**的）。曾试过「只插值不外推」
      （限于首个锚点~末个锚点之间），结果雅思的最后两行**丢标签** —— 因为雅思的锚点
      落在它 4 行跨度的**第一行**，下面 3 行都在「末个锚点」之外。合并跨度本来就可能
      顶到表尾，外推在这里是对的。代价：表格末尾若有跨全宽的小结行，会继承最后一个
      标签。**所以每次填充都回传 `fills`，由调用方报出来给人核**。
    """
    rows = tb["rows"]
    ranges = tb.get("row_ranges") or []
    # 行中心：优先用真实 box 的 y 中心（更准），退化时用行号
    centers = [((ranges[i][0] + ranges[i][1]) / 2 if i < len(ranges) else float(i))
               for i in range(len(rows))]
    ncol = max((len(r) for r in rows), default=0)
    fills: list[tuple] = []
    anchors: list[int] = []
    for c in cols:
        if c >= ncol:
            continue
        have = [r for r in range(len(rows)) if rows[r][c].strip()]
        if len(have) < 2:
            continue
        for r in range(len(rows)):
            if rows[r][c].strip():
                continue
            near = min(have, key=lambda a: abs(centers[a] - centers[r]))
            rows[r][c] = rows[near][c]
            fills.append((r, c, rows[near][c]))
        for r in have:
            # 孤儿行 = 只有第 c 列有字（其余全空）→ 合并标签被单独聚成了一行
            if not any(x.strip() for i, x in enumerate(rows[r]) if i != c):
                anchors.append(r)
    # ★ 只删**锚点行本身**（它的值已经铺到两侧），不删别的空行 ——
    #   删「非 cols 列全空」的行会连「值列全部漏检」的真数据行一起删掉，那是丢内容。
    if anchors:
        drop = set(anchors)
        kept = [i for i in range(len(rows)) if i not in drop]
        tb["rows"] = [rows[i] for i in kept]
        # empty 重新数一遍（删行后行号全变了，增量维护必错）
        tb["empty"] = [(ri, c) for ri, row in enumerate(tb["rows"])
                       for c, v in enumerate(row) if not v.strip()]
        # ⚠️ 行号变了 → 这两个索引失效。调用方要用它们（如空格的放大重 OCR）
        #    必须在调用本函数**之前**用完。
        tb["row_ranges"] = []
        tb["cell_centers"] = {}
    return {"fills": fills, "dropped": len(anchors)}


def apply_manual(tb, patches) -> tuple[list, list]:
    """按**声明式人工补值表**填空格。返回 `(应用了的, 没找到的)`。

    为什么需要它：OCR 有**检测器漏检**（实测「托福 75≤A<100」的折算值是个单字母 `A`，
    全页与 8 倍放大都检不到）。这类格只能人读图补，而 `ocr2md.py` 重跑会**重新生成**整份 md ——
    没有这个机制的话，人工补的值会被自动结果**覆盖掉**（本语料实测就会：
    《大学英语》那张表的 `A` 是人工补的，重跑一次就没了）。

    定位方式**与几何无关**（跟着 `repair_flat_table.MANUAL_FIX` 的先例）：
      · 行 = 按**同行某个已知单元格的值**定位（`row_key`），不用行号 —— 行号会随
        「合并单元格铺开」「检测器漏检」变动
      · 列 = 按**表头名**定位，不用列号

    ★ **只填空格，绝不覆盖**：已经有字的格子一律不动（自动识别出来的值才是第一手证据）。
    ★ **没找到的要报出来**：静默跳过 = 又回到「人工补的值悄悄丢了」那个坑。
    """
    rows = tb.get("rows") or []
    applied, missed = [], []
    if not rows:
        return applied, missed
    head = [str(x).strip() for x in rows[0]]
    for p in patches or []:
        col, key = str(p.get("col", "")).strip(), str(p.get("row_key", "")).replace(" ", "")
        if col not in head:
            missed.append((p, f"表头里没有「{col}」"))
            continue
        ci = head.index(col)
        hit = None
        for r in range(1, len(rows)):
            if any(str(x).replace(" ", "") == key for x in rows[r]):
                hit = r
                break
        if hit is None:
            missed.append((p, f"没有一行含「{p.get('row_key')}」"))
            continue
        if str(rows[hit][ci]).strip():
            missed.append((p, "该格已有值（不覆盖）"))
            continue
        rows[hit][ci] = str(p.get("value", ""))
        applied.append((hit, ci, rows[hit][ci], p.get("why", "")))
        # ★ 补上之后要把它从 `empty` 里摘掉 —— 否则「空格清单」会报一个**已经填好的**格，
        #   与上一行的「人工补值 → …」自相矛盾（实测踩过）。`empty` 是给人核验的队列，
        #   报假条目既浪费人力又让人怀疑整份清单。
        tb["empty"] = [(r, c) for r, c in (tb.get("empty") or []) if (r, c) != (hit, ci)]
    return applied, missed


def to_markdown(rows) -> list[str]:
    """网格 → markdown 管道表（表头重复由 vector._split_table 负责，这里只出完整表）。"""
    n = max((len(x) for x in rows), default=0)
    if not rows or n < 2:
        return []
    pad = lambda x: (list(x) + [""] * n)[:n]        # noqa: E731
    out = ["| " + " | ".join(pad(rows[0])) + " |", "|" + "---|" * n]
    for x in rows[1:]:
        out.append("| " + " | ".join(pad(x)) + " |")
    return out