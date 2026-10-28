# ocr2md.py —— 扫描件 PDF 的 OCR 转换（PyMuPDF 渲染 + RapidOCR 识别，纯 CPU，不占显存）
#
# 为什么需要它：
#   政教文件里有一部分是**扫描件**——PDF 里装的是图片，没有文字层，pypdf 抽出来是空的。
#   这类文件 mdify 救不了，必须走 OCR。
#
# 和 pdf2md.py 的分工（先跑 pdf2md.py，标 ⚠️ 的那些才是指名要 ocr2md.py 处理的）：
#   pdf2md.py —— 有文字层的 PDF，直接抽文字（快、准、零识别误差）★优先
#   ocr2md.py —— 没有文字层的扫描件，渲染成图再 OCR（慢，有识别误差）
#
# 用法（在项目根目录执行）：
#   # 单个文件
#   python scripts/ocr2md.py "guet数据文件/规章制度/学校章程/桂林电子科技大学章程（2023年核准稿）_01.pdf" _md/规章制度
#
#   # 只体检：逐页报告「文字层有多少字 / 需要 OCR 多少行」，不写盘
#   python scripts/ocr2md.py --check "guet数据文件/规章制度/学校章程/桂林电子科技大学章程（2023年核准稿）_01.pdf"
#
#   # 递归扫描整个目录，找出所有需要 OCR 的 PDF 并全部处理
#   python scripts/ocr2md.py --dir "guet数据文件/规章制度" _md/规章制度
#
# 关键实现细节：
#   · 每行 OCR 结果单独输出一行（不合并成一段）——因为公文把「第一条」放在行首，
#     合并成一段会让 mdify 的 _ARTICLE_RE（^第X条）再也匹配不到，标题层级全丢。
#   · 渲染倍率默认 3（约 216 DPI）：中文小字号在 2 倍下识别率会明显下降。
#   · 渲染顺序 = OCR 顺序，RapidOCR 按阅读顺序返回，单栏公文不会串行。

import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from documents import mdify  # noqa: E402
from documents import pdfextract  # noqa: E402
from documents import tablebuild  # noqa: E402

_EXTS = (".pdf",)

ZOOM = 8.0   # 空单元格二次 OCR 的放大倍率（与 repair_flat_table 同值，实测能救回低置信度格）

# 人工补值表（`table_manual.json`，项目根）—— 检测器漏检的格只能人读图补，
# 而本脚本重跑会**重新生成**整份 md，没有它人工补的值会被覆盖掉。
MANUAL_FILE = os.path.join(ROOT, "table_manual.json")


def load_manual() -> dict:
    try:
        with open(MANUAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"⚠️ {os.path.basename(MANUAL_FILE)} 读不动（{type(e).__name__}: {e}），本次不套用人工补值")
        return {}


from documents import ocr_engine  # noqa: E402  （BACKEND_DIR 已加进 sys.path）
from documents.ocr_engine import TEXT_LAYER_MIN  # noqa: E402


def _load_engine():
    """加载 OCR 引擎。缺依赖时给一句人话提示，而不是抛栈。"""
    try:
        import fitz  # noqa: F401
    except ImportError:
        print("❌ 缺少依赖，先装：\n    pip install pymupdf")
        sys.exit(1)
    try:
        return ocr_engine.load_engine()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)


def page_to_lines(engine, img, to_bgr: bool, page, scale: float, tables: bool,
                  zoom: float = ZOOM, log=print, patches=None) -> tuple[list[str], dict]:
    """一页扫描图 → 逐行文本（**表格区域换成 markdown 管道表**）。返回 (行列表, 统计)。

    ★ 这是 ⑧ 的落点：原先 `ocr_page` 只回文本、把 RapidOCR 的坐标框丢掉了，
      于是扫描页上的表格被拍平成一段连排文字（单元格之间没有分隔，行/列关系全丢）。
      坐标一直在手上，`backend/tablebuild.py` 就是为它写的纯函数库。

    ★ **保序 + 正文零改动**：`tablebuild.split_regions` 按「列带 + 行数 + 填充率」
      切出表格区域，**其余部分按 RapidOCR 的原始顺序逐行输出**（不按坐标重排）——
      没识别出表格的页，输出与改造前**逐字节相同**。这样 A/B 里只有一个变量。

    ⚠️ **空格只报不补**：`build_table` 刻意不做「合并单元格向下补齐」——空有两种来源
      （真合并 / 检测器漏检），坐标上分不开，补了就会静默填错值。这里做的是
      **按格子矩形放大重 OCR**（`zoom` 倍），只认「重新识别出来的字」，识别不出就留空并计数。
    """
    raw = ocr_engine.ocr_image_with_boxes(img, to_bgr, engine=engine)
    if not tables:
        return [b[4] for b in raw], {"tables": 0, "empty": 0, "zoom_fixed": 0}

    # 坐标统一到 **PDF 点**（÷scale）：表格聚类与后面的裁剪都按点算，两套单位混用
    # 会一个 box 都框不住（repair_flat_table 实测踩过）。
    boxes = [(b[0] / scale, b[1] / scale, b[2] / scale, b[3] / scale, b[4]) for b in raw]
    lines: list[str] = []
    n_tab = n_empty = n_fixed = 0
    for blk in tablebuild.split_regions(boxes):
        if blk["kind"] == "text":
            lines.extend(b[4] for b in blk["boxes"])
            continue
        tb = blk["tb"]
        n_tab += 1
        if tb["empty"] and zoom > 1:
            for ri, ci in list(tb["empty"]):
                if ri >= len(tb["row_ranges"]):
                    continue
                ry0, ry1 = tb["row_ranges"][ri]
                bx0, bx1 = tb["band_ranges"][ci]
                pad = 3
                got = ocr_engine.ocr_image_with_boxes(
                    render_clip(page, (bx0 - pad, ry0 - pad, bx1 + pad, ry1 + pad), zoom),
                    to_bgr, engine=engine)
                text = " ".join(t for *_, t in sorted(got, key=lambda b: b[0])).strip()
                if text and "|" not in text:
                    tb["rows"][ri][ci] = text
                    n_fixed += 1
            tb["empty"] = [(r, c) for r, c in tb["empty"] if not tb["rows"][r][c].strip()]
        # ★ 顺序不能反：铺开合并单元格会**删除锚点行、行号全变**，之后 row_ranges 就失效了 —
        #   而空格的放大重 OCR 恰恰要用 row_ranges。所以「先救空格、后铺合并」。
        sp = tablebuild.spread_merged(tb)
        if sp["fills"]:
            log(f"     表{n_tab} 合并单元格铺开 {len(sp['fills'])} 格"
                f"（删孤儿行 {sp['dropped']}）→ 例 {sp['fills'][:3]}")
        # ★ 人工补值（声明式）：只填空格、绝不覆盖；**没找到要报出来** ——
        #   静默跳过就等于「人工补的值又悄悄丢了」，那正是这张表存在的理由。
        use, miss = tablebuild.apply_manual(tb, patches)
        for r, c, v, why in use:
            log(f"     表{n_tab} 人工补值 → 行{r} 列{c} = {v!r}（{why[:40]}）")
        for p, why in miss:
            log(f"     ⚠️ 表{n_tab} 人工补值**没套上**：{p.get('row_key')} / {p.get('col')} —— {why}")
        # ★ 空格数在**铺开 + 补值之后**数 —— 这两个步骤都会填掉一批格子，
        #   在它们之前数出来的数是虚高的（实测：16 → 实际 0）。这个数要给人核验，
        #   虚高就等于让人去查一批根本不存在的空格。
        left = sum(1 for r in tb["rows"] for c in r if not c.strip())
        n_empty += left
        md = tablebuild.to_markdown(tb["rows"])
        if md:
            lines.extend(md)
            if left:
                log(f"     ⚠️ 表{n_tab} 剩 {left} 个空格"
                    f"（可能是合并单元格，也可能是检测器漏检）→ 位置 {tb['empty'][:6]}")
        else:                                   # 生不出 markdown 就退回逐行，别丢内容
            lines.extend(b[4] for b in blk["boxes"])
    return lines, {"tables": n_tab, "empty": n_empty, "zoom_fixed": n_fixed}


def page_text_layer(path: str) -> list[str]:
    """每页的文字层内容（判断这页要不要 OCR）。

    ⚠️ 用 pymupdf 而不是 pypdf：必须与 pdfextract 同源。两个抽取器对同一份 PDF
    可能给出不同的页数/字数，那就会出现「按 A 判断这页要 OCR、按 B 这页又有版面分析」
    两套互相打架的判断。原先这里用 pypdf、pdfextract 用 pymupdf，是个隐患。
    """
    import fitz
    doc = fitz.open(path)
    try:
        return [(p.get_text() or "") for p in doc]
    finally:
        doc.close()


def render_page(page, scale: float):
    """把一页渲染成 numpy 图像（RGB）。"""
    import fitz
    import numpy as np

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def render_clip(page, rect_pts, scale: float):
    """只渲染一页里的一个矩形（**PDF 点**）→ numpy 图像（RGB）。

    空单元格二次 OCR 用它：整页渲染一次再裁是浪费，`clip` 直接只栅格化那一小块。
    """
    import fitz
    import numpy as np

    clip = fitz.Rect(*rect_pts)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def doc_key(path: str) -> str:
    """PDF 路径 → 文档键（= 输出 md 的文件名去扩展名，NFKC + 去空白）。

    ★ 单一来源：**输出文件名**与**人工补值表的键**都必须用它算，
      否则两处各写一遍 NFKC 规则，迟早对不上 —— 对不上的表现是「人工补值静默失效」。
    """
    import re as _re
    import unicodedata
    stem = os.path.splitext(os.path.basename(path))[0]
    return _re.sub(r"\s+", "", unicodedata.normalize("NFKC", stem)) or stem


def convert_one(engine, path: str, scale: float, check_only: bool, to_bgr: bool,
                tables: bool = True, manual: dict | None = None):
    """转换单个 PDF。返回 (md 文本, 统计 dict)。

    ★ 有文字层的页走 pdfextract 的**版面分析**（去页眉页脚 / 按坐标合段落 / 还原表格），
      只有真正没有文字层的页才整页 OCR，最后按页码拼回去。

      原实现对「有文字层的页」直接用 pypdf 抽的原始文本（`parts.append(layers[i])`），
      等于整段跳过 pdfextract。后果不是理论上的：--dir 的筛法是「这个 PDF 里**有任何一页**
      无文字层」，于是那些「正文完好、只有 1~2 页是扫描件」的培养方案会被整份命中
      （实测 18 份），里面 15~21 张识别得好好的表格全部被拍平成一行行文字——
      拿 19 页内容换掉 300+ 张表格。

      另外只渲染**需要 OCR 的页**：原实现 render_pages 会把每页都渲染一遍
      （scale=3.0 相当贵），24 页的文档只为 1 页 OCR 也要渲染 24 页。
    """
    import fitz

    layers = page_text_layer(path)
    n_pages = len(layers)
    need = [i for i, t in enumerate(layers) if len(t.strip()) < TEXT_LAYER_MIN]
    print(f"\n{'=' * 88}\n{os.path.basename(path)}")
    print(f"  共 {n_pages} 页，其中 {len(need)} 页没有文字层（需 OCR）")

    if check_only:
        for i, t in enumerate(layers[:80]):
            tag = "需OCR" if i in need else f"{len(t.strip()):>5}字"
            print(f"     第 {i + 1:>3} 页  {tag}")
        return None, {"pages": n_pages, "need": len(need)}

    # 逐页结构化：有文字层的页拿到版面分析结果，扫描页是空串（下面用 OCR 补）
    page_mds: list[str] = []
    try:
        with open(path, "rb") as f:
            page_mds, _ = pdfextract.to_markdown_pages(f.read())
    except Exception as e:
        print(f"     ⚠️ 版面分析不可用（{type(e).__name__}: {e}），退回逐页纯文本")
    if len(page_mds) != n_pages:
        # 页数对不上就不敢按页对齐（错位会把 A 页的文字插到 B 页的位置）
        print(f"     ⚠️ 页数不一致（pymupdf {len(page_mds)} vs 文字层 {n_pages}），退回逐页纯文本")
        page_mds = []

    parts: list[str] = []
    t0 = time.time()
    n_tab = n_empty = n_fixed = 0
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc):
            if i not in need:
                parts.append(page_mds[i] if page_mds else mdify._plain_to_md(layers[i]))
                continue
            lines, tstat = page_to_lines(engine, render_page(page, scale), to_bgr, page,
                                         scale, tables,
                                         patches=(manual or {}).get(doc_key(path)))
            parts.append("\n".join(lines))       # ★ 逐行，不要合并成段
            n_tab += tstat["tables"]
            n_empty += tstat["empty"]
            n_fixed += tstat["zoom_fixed"]
            extra = (f"　表 {tstat['tables']} · 救回 {tstat['zoom_fixed']} 格 · 空格 {tstat['empty']}"
                     if tstat["tables"] else "")
            print(f"     第 {i + 1}/{n_pages} 页 OCR → {len(lines)} 行{extra}"
                  f"　({time.time() - t0:.0f}s)", flush=True)
    finally:
        doc.close()

    raw = "\n\n".join(p for p in parts if p.strip())
    # 复用后端同一条路径：NFKC 修康熙部首 + 清噪音 + 合并强制换行 + 认「第X章/一、」建标题
    # 最后过一道 OCR 错字表（师凤→师风、造谐→造诣、3一8→3—8 之类）。
    # ⚠️ 它在**整篇**上跑，所以有文字层的页也会过一遍错字表。表里都是「正常中文里
    #    不可能出现的误识形」，对原生文本无副作用；而放在整篇上跑是必须的——
    #    「3一8」这类跨行的情况要等 _plain_to_md 合并完换行才成形。
    md = mdify.fix_ocr_text(mdify._plain_to_md(raw))
    stat = {"pages": n_pages, "need": len(need), "chars": len(md),
            "elapsed": time.time() - t0,
            "tables": n_tab, "empty": n_empty, "zoom_fixed": n_fixed}
    return md, stat


def _norm_md(s: str) -> str:
    """比较用归一：去每行尾空白、丢空行。

    为什么不做「去掉所有空白」：空白变化**也是变化**（会影响切块），不该被抹掉。
    只抹掉「排版噪声」这一层（行尾空格、多余空行），保住行结构。
    """
    return "\n".join(l.rstrip() for l in (s or "").strip().split("\n") if l.strip())


def _out_path(fp: str, dst: str, root: str | None) -> str:
    rel = os.path.relpath(os.path.dirname(fp), root) if root else ""
    return os.path.join(dst, "" if rel == "." else rel, doc_key(fp) + ".md")


def _diff_head(a: str, b: str, n: int = 6) -> tuple[list[str], int]:
    """a（现有）→ b（重建后）的差异摘要：返回 (前 n 条差异行, 差异块总数)。"""
    import difflib
    d = list(difflib.unified_diff(_norm_md(a).split("\n"), _norm_md(b).split("\n"),
                                  lineterm="", n=0))
    body = [x for x in d if x[:1] in "+-" and not x.startswith(("+++", "---"))]
    nblk = sum(1 for x in d if x.startswith("@@"))
    return body[:n], nblk


def main():
    p = argparse.ArgumentParser(description="扫描件 PDF OCR 转 Markdown（本地 CPU）")
    p.add_argument("src", nargs="?", help="PDF 文件路径；或用 --dir 指定目录")
    p.add_argument("dst", nargs="?", default=None, help="输出目录；不填 = 只体检")
    p.add_argument("--dir", default=None, help="递归处理该目录下所有「文字层不足」的 PDF")
    p.add_argument("--check", action="store_true", help="只体检，逐页报告是否需 OCR")
    p.add_argument("--scale", type=float, default=3.0, help="渲染倍率（默认 3 ≈ 216DPI）")
    p.add_argument("--rgb", action="store_true",
                   help="按 RGB 传图（默认转 BGR；若识别结果全是乱码可加这个开关）")
    p.add_argument("--tables", action="store_true",
                   help="对**所有**文档开启扫描页的表格重建。**默认关** —— 实测当前语料跑它会给"
                        " 3 份培养方案的教学进程表造出错误的表（净收益 0）。逐份放行用 "
                        "`table_manual.json` 的 `_tables_on`")
    p.add_argument("--dry-run", action="store_true",
                   help="**不写盘**：逐份比对「重建后 vs 磁盘上现有的 md」，报出**几份内容真变了**"
                        "与差异摘要。落盘前必须先看这个 —— 它同时会暴露「有人工改动的文档」")
    p.add_argument("--report", default=None, help="把 dry-run 报告写到文件（便于存档/复核）")
    args = p.parse_args()
    # 表格重建**默认关**：`--tables` 对全部文档开；`table_manual.json` 的 `_tables_on`
    # 是**逐份放行**名单（核验过那一份的表再加进去）。理由见 `--tables` 的 help 与
    # `_work/ocr_tables_dryrun.md`（实测净收益 0、风险是毁 3 份文档）。

    if not args.src and not args.dir:
        p.error("需要给 src（单个 PDF）或 --dir（目录）")

    # --dir 模式下 src 用不上：只给一个位置参数时它其实是 dst。
    # （argparse 会优先填 src，所以这里显式搬一次，否则会静默不写盘）
    if args.dir and args.dst is None:
        args.dst, args.src = args.src, None

    targets: list[str] = []
    if args.dir:
        for dp, _, ns in os.walk(args.dir):
            for n in sorted(ns):
                if n.lower().endswith(_EXTS):
                    fp = os.path.join(dp, n)
                    layers = page_text_layer(fp)
                    if any(len(t.strip()) < TEXT_LAYER_MIN for t in layers):
                        targets.append(fp)
        print(f"扫描目录下共 {len(targets)} 个 PDF 存在「无文字层」的页")
    else:
        targets = [args.src]

    engine = None if args.check else _load_engine()
    manual = load_manual()
    allow = set(manual.get("_tables_on") or [])      # 逐份放行的表格重建名单
    if manual and not args.check:
        print(f"人工补值表：{len([k for k in manual if not k.startswith('_')])} 份文档有声明"
              f"；表格重建放行 {len(allow)} 份"
              + ("（--tables：对全部文档开）" if args.tables else ""))
    written = 0
    rows: list[dict] = []          # dry-run 报告用
    for fp in targets:
        if args.check and not args.dir and not args.src:
            continue
        tables = args.tables or doc_key(fp) in allow
        md, stat = convert_one(engine, fp, args.scale, args.check, not args.rgb, tables, manual)
        if md is None:
            continue
        ok = stat["chars"] >= 200
        tinfo = (f"　表 {stat['tables']}（救回 {stat['zoom_fixed']} 格 / 空格 {stat['empty']}）"
                 if stat.get("tables") else "")
        print(f"  {'✅' if ok else '⚠️ '} 输出 {stat['chars']} 字，耗时 {stat['elapsed']:.0f}s{tinfo}")
        out = _out_path(fp, args.dst, args.dir) if args.dst else None
        if args.dry_run:
            cur = open(out, encoding="utf-8").read() if (out and os.path.exists(out)) else None
            body, nblk = _diff_head(cur, md) if cur is not None else ([], -1)
            rows.append({"doc": doc_key(fp), "out": out, "cur": cur is not None,
                         "same": cur is not None and _norm_md(cur) == _norm_md(md),
                         "chars": stat["chars"], "cur_chars": len(cur) if cur else 0,
                         "tables": stat.get("tables", 0), "empty": stat.get("empty", 0),
                         "zoom": stat.get("zoom_fixed", 0),
                         "diff": body, "nblk": nblk, "ok": ok})
            continue
        if out and ok:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"  💾 {out}")
            written += 1

    if args.dry_run:
        _report(rows, args.report)
    elif written:
        print(f"\n已写入 {written} 个 md → {args.dst}")


def _report(rows: list[dict], path: str | None) -> None:
    """dry-run 汇总：**几份内容真变了** + 差异摘要（这就是人核验队列）。"""
    L: list[str] = []
    changed = [r for r in rows if not r["same"]]
    new = [r for r in rows if not r["cur"]]
    L.append("# 扫描页表格重建 —— dry-run 报告（**未写盘**）\n")
    L.append("目标 %d 份 PDF：内容**变了** %d 份 · 与现有**相同** %d 份 · **现有 md 不存在** %d 份\n"
             % (len(rows), len(changed) - len(new), len(rows) - len(changed), len(new)))
    L.append("| 文档 | 现有字数 | 重建字数 | 表 | 空格 | 变? | 差异块 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["same"], -r["tables"], r["doc"])):
        L.append("| %s | %d | %d | %d | %d | %s | %s |"
                 % (r["doc"][:38], r["cur_chars"], r["chars"], r["tables"], r["empty"],
                    "—" if r["same"] else ("**变了**" if r["cur"] else "无现有"),
                    r["nblk"] if r["nblk"] >= 0 else "—"))
    L.append("\n## 差异摘要（前 6 条/份）\n")
    for r in changed:
        L.append("### %s" % r["doc"])
        L.append("  现有 %d 字 → 重建 %d 字；表 %d · 空格 %d；差异块 %d"
                 % (r["cur_chars"], r["chars"], r["tables"], r["empty"], r["nblk"]))
        for ln in r["diff"]:
            L.append("    " + ln[:160])
        L.append("")
    txt = "\n".join(L)
    print("\n" + "=" * 88 + "\n" + txt)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"\n📄 报告已写 → {path}")
    print("\n[DRY-RUN] 未写任何 md")


if __name__ == "__main__":
    main()