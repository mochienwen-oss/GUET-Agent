# tests/test_tablebuild.py —— 扫描页表格重建的单元测试（纯函数，不需要 OCR/PDF）
#
# 合成坐标即可覆盖：这里锁的是**两条实测踩出来的规则**，不是为了凑覆盖率。
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from documents import tablebuild as T  # noqa: E402


def box(x0, y0, x1, y1, text):
    return (x0, y0, x1, y1, text)


# 三列：类别 / 成绩A / 值（列间留大空隙，列内各行 x 有重叠——与真实表一致）
def grid_rows():
    return [
        box(100, 10, 160, 22, "CET4"), box(200, 10, 280, 22, "425≤A<567"), box(350, 10, 420, 22, "A×0.1764"),
        box(100, 30, 160, 42, "CET6"), box(200, 30, 280, 42, "A≥500"), box(350, 30, 420, 42, "100"),
        box(100, 50, 160, 62, "雅思"), box(200, 54, 278, 66, "A=6.5"), box(350, 54, 420, 66, "85"),
    ]


def test_column_bands_finds_three():
    bands = T.column_bands(grid_rows())
    assert len(bands) == 3, bands
    # 列缝要落在真实空隙里（三列 x 范围各自约 100~160 / 200~280 / 350~420）
    assert bands[0][1] < bands[1][0] and bands[1][1] < bands[2][0]


def test_build_table_grid():
    tb = T.build_table(grid_rows())
    assert tb["ok"] and tb["bands"] == 3
    assert tb["rows"] == [["CET4", "425≤A<567", "A×0.1764"],
                          ["CET6", "A≥500", "100"],
                          ["雅思", "A=6.5", "85"]]
    assert tb["empty"] == []


def test_empty_cell_stays_empty_and_is_reported():
    """★ 关键安全属性：空单元格**不许**被静默补值。

    空有两种来源——真合并 / 检测器漏检——坐标上分不开。补了就会把漏检格
    填成上一行的错值（实测踩过：托福那格的 `A` 检不到，补齐会填成 `100`）。
    所以一律留空 + 报进 `empty`，由调用方人工核。
    """
    bs = grid_rows()
    bs = [b for b in bs if not (b[4] == "100")]          # 挖掉一格
    tb = T.build_table(bs)
    assert ["CET6", "A≥500", ""] in tb["rows"]
    assert (1, 2) in tb["empty"]
    # 且**没有**从上一行补来 "A×0.1764"
    assert tb["rows"][1][2] == ""


def test_merged_cell_centered_in_gap_forms_own_row():
    """★ 记录一条**几何事实**（不是缺陷）：合并单元格的文字垂直居中，会落在行缝里。

    实测（《大学英语》表）：CET4 的 y 区间压在上下两行数据的**空隙**中，与两边
    **都不重叠** → 区间重叠聚类会把它单独聚成一行。调用方要还原成 markdown
    （不支持合并单元格）就得显式把它铺回两侧——见 scripts/documents/repair_flat_table.py
    的 MERGE_COLS。这里把这个行为锁住，免得以后有人「顺手优化」成别样。
    """
    bs = [box(200, 10, 280, 22, "425≤A<567"), box(350, 10, 420, 22, "A×0.1764"),
          box(100, 24, 160, 32, "CET4"),                  # 落在 22~? 的行缝里
          box(200, 34, 280, 46, "A≥567"), box(350, 34, 420, 46, "100")]
    tb = T.build_table(bs)
    assert ["CET4", "", ""] in tb["rows"], tb["rows"]     # 自成一行、两侧为空
    assert len(tb["rows"]) == 3


def test_to_markdown_pads_rectangular():
    md = T.to_markdown([["a", "b"], ["c"], ["d", "e", "f"]])
    assert md[1] == "|---|---|---|"
    assert md[0] == "| a | b |  |"
    assert md[2] == "| c |  |  |"
    assert md[3] == "| d | e | f |"


def test_to_markdown_needs_two_columns():
    assert T.to_markdown([["只有一列"], ["x"]]) == []      # 单列不是表
    assert T.to_markdown([]) == []


def test_build_table_falls_back_when_not_a_table():
    """列带不足 2 个 → `ok=False`（调用方应退回「按行文本」而不是硬造表）。"""
    tb = T.build_table([box(10, 10, 200, 22, "一整行正文"), box(10, 30, 210, 42, "又一整行")])
    assert tb["ok"] is False and tb["bands"] < 2


# ---------------------------------------------------------------- 整页区域切分
# split_regions 的那条铁律是**保序 + 正文零改动**：没识别出表格的页，
# 输出必须与改造前逐字节相同。所以下面一半测试是在锁「不被误改」。

def _page_body(n=6):
    """一整页公文正文：每行都从左边距起、接近满宽（真实正文的 x 区间必然重叠）。"""
    return [box(100, 10 + 30 * i, 900, 32 + 30 * i, f"第{i}行正文内容，占满整行排到右边距为止") for i in range(n)]


def test_body_text_page_is_all_text_and_keeps_order():
    """正文页：一个表都不该识别出来，且**顺序原样**（保序）。"""
    bs = _page_body()
    blks = T.split_regions(bs)
    assert [b["kind"] for b in blks] == ["text"] * len(bs)
    assert [b["boxes"][0][4] for b in blks] == [b[4] for b in bs]


def test_short_left_aligned_lines_are_not_a_table():
    """连续短行（如三行落款/标题）全都从同一 x 起 → 只并出 1 个列带 → 不是表。"""
    bs = [box(100, 10, 220, 22, "桂林电子科技大学"), box(100, 30, 240, 42, "二〇二五年九月"),
          box(100, 50, 230, 62, "教务处")]
    blks = T.split_regions(bs)
    assert all(b["kind"] == "text" for b in blks)


def test_full_page_table_detected_despite_no_body_line():
    """★ 锁住 `w_ref` 的取法：**整页都是表**时，参考宽度不能塌缩成「最宽的格子」。

    取「最宽 box」的话，最宽的那个格子就是参考宽 → 所有格子都判成「正文行」→
    整张表识别不出来。培养方案的教学进程表就是整页表格（实测这类不少），
    所以参考宽必须取**本页内容宽度**（最左到最右）。
    """
    bs = [box(100, 10, 160, 22, "CET4"), box(200, 10, 280, 22, "425≤A<567"), box(350, 10, 420, 22, "A×0.1764"),
          box(100, 30, 160, 42, "CET6"), box(200, 30, 280, 42, "A≥500"), box(350, 30, 420, 42, "100"),
          box(100, 50, 160, 62, "雅思"), box(200, 50, 278, 62, "A=6.5"), box(350, 50, 420, 62, "85")]
    blks = T.split_regions(bs)
    assert [b["kind"] for b in blks] == ["table"], blks
    assert blks[0]["tb"]["rows"] == [["CET4", "425≤A<567", "A×0.1764"],
                                     ["CET6", "A≥500", "100"],
                                     ["雅思", "A=6.5", "85"]]


def test_body_and_table_on_same_page():
    """正文 + 表格混排：正文块顺序不变，表格独立成块。"""
    bs = _page_body(2) + [
        box(100, 200, 160, 212, "CET4"), box(200, 200, 280, 212, "425≤A<567"), box(350, 200, 420, 212, "A×0.1764"),
        box(100, 220, 160, 232, "CET6"), box(200, 220, 280, 232, "A≥500"), box(350, 220, 420, 232, "100"),
        box(100, 240, 160, 252, "雅思"), box(200, 240, 278, 252, "A=6.5"), box(350, 240, 420, 252, "85"),
    ] + [box(100, 300, 900, 322, "表后接着的一段正文，照样占满整行")]
    blks = T.split_regions(bs)
    assert [b["kind"] for b in blks] == ["text", "text", "table", "text"]
    assert blks[0]["boxes"][0][4].startswith("第0行")          # 表前的两行正文，顺序不变
    assert blks[3]["boxes"][0][4].startswith("表后接着")


def test_two_column_layout_is_not_a_table():
    """★ 双栏正文**不许**被判成 2 列表：两栏都满、填充率也高，只有「≥3 列」挡得住它。

    把一份公文误判成表格比漏一张表严重得多 —— 这是明知代价的取舍（见 `_try_table`）。
    """
    bs = []
    for i in range(6):                       # 左栏 6 行 + 右栏 6 行，纵向对齐
        bs.append(box(100, 10 + 30 * i, 380, 32 + 30 * i, f"左栏第{i}行文字内容"))
        bs.append(box(600, 10 + 30 * i, 880, 32 + 30 * i, f"右栏第{i}行文字内容"))
    blks = T.split_regions(bs)
    assert all(b["kind"] == "text" for b in blks), blks


def test_sparse_grid_rejected_by_fill_ratio():
    """列带够、行数够，但大片空格 → 不是表（填充率挡掉）。"""
    bs = [box(100, 10, 160, 22, "甲"), box(200, 10, 260, 22, "乙"), box(350, 10, 410, 22, "丙"),
          box(100, 30, 160, 42, "只此一格"), box(100, 50, 160, 62, "也只有一格")]
    blks = T.split_regions(bs)
    assert all(b["kind"] == "text" for b in blks)


def test_cell_containing_pipe_falls_back_to_text():
    """★ 这一条是**会真触发**的闸门：格子里的 `|` 会让下游按列切错位（静默）。"""
    bs = [box(100, 10, 160, 22, "a|b"), box(200, 10, 260, 22, "乙"), box(350, 10, 410, 22, "丙"),
          box(100, 30, 160, 42, "甲2"), box(200, 30, 260, 42, "乙2"), box(350, 30, 410, 42, "丙2"),
          box(100, 50, 160, 62, "甲3"), box(200, 50, 260, 62, "乙3"), box(350, 50, 410, 62, "丙3")]
    blks = T.split_regions(bs)
    assert all(b["kind"] == "text" for b in blks), blks


def test_no_text_lost_is_vacuous_by_construction():
    """★ 把「判据恒真」这件事**写进测试**，免得后人以为它是安全网。

    列带是各 box x 区间的并集，每个 box 的中心必然落在自己那个区间里 →
    归属必然成立。这条测试的价值不是「验证它有效」，而是**记录它无效**：
    真正兜底的是 `_has_pipe`，以及**人工核验重建结果**（见 ⑧ 的复核流程）。
    """
    import itertools
    rng = itertools.product((100, 130), (10, 40, 70))
    bs = [box(x, y, x + 60, y + 12, f"t{x}{y}") for x, y in rng]
    tb = T.build_table(bs)
    assert T._no_text_lost(bs, tb["rows"]) is True


# ------------------------------------------------------- 合并单元格铺开（spread_merged）
# 用**实测的真实输出**当输入：这是《大学英语》认定标准表 OCR 后、铺开**之前**的样子
# （合并的「类别」列纵向居中 → 自成一个孤儿行，行数因此翻倍）。
# 真值见 scripts/documents/repair_flat_table.py 的 EXPECTED（人工目视读值那份）。

_MERGED_BEFORE = [
    ["", "425≤A<567", "A×0.1764"],
    ["CET4", "", ""],                    # ← 孤儿行（合并标签被单独聚成一行）
    ["", "A≥567", "100"],
    ["", "425≤A<500", "A×0.2"],
    ["CET6", "", ""],                    # ← 孤儿行
    ["", "A≥500", "100"],
    ["", "75≤A<100", ""],
    ["托福", "", ""],                    # ← 孤儿行
    ["", "A≥100", "100"],
    ["", "A=6.0", "80"],
    ["雅思", "A=6.5", "85"],             # ← **不是**孤儿行（值列有字）→ 不当锚点、也不删
    ["", "A=7.0", "95"],
    ["", "A≥7.5", "100"],
]
# 行 y 中心：孤儿行落在两侧数据行的**行缝**里（这正是它自成一行的原因）
_Y = [61, 73, 85, 116, 128, 140, 171, 183, 195, 226, 239, 252, 265]


def _tb(rows, y):
    return {"rows": [list(r) for r in rows], "row_ranges": [(c - 6, c + 6) for c in y],
            "empty": [], "cell_centers": {}, "bands": len(rows[0])}


def test_spread_merged_nearest_anchor_labels_every_row():
    """★ 铺开必须**按 y 找最近锚点**，且跨 2 行（CET4/CET6/托福）与跨 4 行（雅思）都对。

    这条锁的是一个**真踩过的错**：先前写成「填满连续空格」，CET4 就漏进了紧随其后的
    CET6 段，把「425≤A<500」标成了 CET4 —— 静默贴错标签，比留空严重。
    """
    tb = _tb(_MERGED_BEFORE, _Y)
    T.spread_merged(tb)
    assert tb["rows"] == [
        ["CET4", "425≤A<567", "A×0.1764"],
        ["CET4", "A≥567", "100"],
        ["CET6", "425≤A<500", "A×0.2"],
        ["CET6", "A≥500", "100"],
        ["托福", "75≤A<100", ""],          # ← 折算值 `A` 检测器漏检，**留空不补**（见下条）
        ["托福", "A≥100", "100"],
        ["雅思", "A=6.0", "80"],
        ["雅思", "A=6.5", "85"],
        ["雅思", "A=7.0", "95"],           # ← 雅思的锚点落在跨度**第一行**，靠外推才铺得上
        ["雅思", "A≥7.5", "100"],
    ]


def test_spread_merged_never_touches_value_columns():
    """★ 值列的**空格一律不许铺**：空可能是「合并」也可能是「检测器漏检」，
    坐标上分不开 —— 铺错了就是静默填错值（「托福 A≥100 → 100」的 `100` 不得漏进上一行）。"""
    tb = _tb(_MERGED_BEFORE, _Y)
    T.spread_merged(tb)
    assert tb["rows"][4] == ["托福", "75≤A<100", ""]      # 值列留空，没被上一行的 100 填上


def test_spread_merged_only_drops_orphan_label_rows():
    """只删**孤儿行**（整行只有标签列有字）。值列有字的行即使标签列空，也必须保留。"""
    tb = _tb(_MERGED_BEFORE, _Y)
    r = T.spread_merged(tb)
    assert r["dropped"] == 3                                # CET4 / CET6 / 托福 三个孤儿行
    assert len(tb["rows"]) == len(_MERGED_BEFORE) - 3
    # 雅思那行（标签列有字、值列也有字）不在删除之列
    assert ["雅思", "A=6.5", "85"] in tb["rows"]


def test_spread_merged_skips_single_anchor_column():
    """该列只有 1 个非空值时**不铺**（无从判断跨度，铺了就是全列刷同一个值）。"""
    tb = _tb([["A", "1"], ["", "2"], ["", "3"]], [10, 30, 50])
    assert T.spread_merged(tb)["fills"] == []
    assert tb["rows"][1][0] == ""


def test_spread_merged_respects_declared_columns():
    """只铺调用方**声明**的列（默认第 0 列）；声明外的列原样不动。"""
    rows = [["甲", "x", "v1"], ["乙", "", "v2"], ["丙", "", "v3"]]
    tb = _tb(rows, [10, 30, 50])
    fills = T.spread_merged(tb)["fills"]                    # 第 1 列有空格，但没声明
    assert fills == []
    assert [r[1] for r in tb["rows"]] == ["x", "", ""]


# ------------------------------------------------------------ 人工补值（apply_manual）
# 存在的理由：检测器漏检的格只能人读图补，而 ocr2md 重跑会重新生成整份 md ——
# 没有它，人工补的值会被自动结果覆盖掉（实测《大学英语》表的 `A` 就是这个情况）。

def _tbl():
    return {"rows": [["类别", "成绩A", "大学英语成绩"],
                     ["托福", "75≤A<100", ""],
                     ["托福", "A≥100", "100"]]}


def test_apply_manual_fills_empty_cell():
    tb = _tbl()
    use, miss = T.apply_manual(tb, [{"row_key": "75≤A<100", "col": "大学英语成绩", "value": "A"}])
    assert tb["rows"][1][2] == "A"
    assert len(use) == 1 and miss == []


def test_apply_manual_never_overwrites_detected_value():
    """★ 只填空格：已有的值（第一手识别结果）**不许**被人工声明盖掉。"""
    tb = _tbl()
    use, miss = T.apply_manual(tb, [{"row_key": "A≥100", "col": "大学英语成绩", "value": "错值"}])
    assert tb["rows"][2][2] == "100"
    assert use == [] and len(miss) == 1 and "已有值" in miss[0][1]


def test_apply_manual_reports_misses_instead_of_skipping_silently():
    """★ 没套上必须报出来：静默跳过 = 又回到「人工补的值悄悄丢了」那个坑。"""
    tb = _tbl()
    use, miss = T.apply_manual(tb, [
        {"row_key": "不存在的行", "col": "大学英语成绩", "value": "x"},
        {"row_key": "75≤A<100", "col": "没有这一列", "value": "x"},
    ])
    assert use == [] and len(miss) == 2
    assert "没有一行含" in miss[0][1] and "表头里没有" in miss[1][1]


def test_apply_manual_locates_by_value_not_by_index():
    """定位**与几何无关**：行按同行已知单元格的值找、列按表头名找 —— 行号列号变了也套得上。"""
    tb = _tbl()
    tb["rows"].insert(1, ["雅思", "A=6.5", "85"])           # 前面插一行，行号全变
    T.apply_manual(tb, [{"row_key": "75≤A<100", "col": "大学英语成绩", "value": "A"}])
    assert tb["rows"][2] == ["托福", "75≤A<100", "A"]


def test_apply_manual_clears_the_cell_from_empty_list():
    """★ 补上之后**必须**把它从空格清单里摘掉。

    清单是给人核验的队列。留着已填好的格会与日志里上一行的「人工补值 → 行5 列2」自相矛盾
    —— 实测踩过：同一个位置既报「已补」又报「还是空的」。
    """
    tb = _tbl()
    tb["empty"] = [(1, 2)]
    T.apply_manual(tb, [{"row_key": "75≤A<100", "col": "大学英语成绩", "value": "A"}])
    assert tb["empty"] == []