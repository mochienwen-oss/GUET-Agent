# pdfextract.py —— 基于 PyMuPDF 版面信息的 PDF → Markdown（取代 pypdf 的纯文本抽取）
#
# 为什么需要它：
#   pypdf.extract_text() 只给一串**按视觉行断开**的纯文本，于是必然出三种毛病——
#     · 页眉/页脚/页码混在正文里。正则清不干净，因为它们和正文长得一样：
#       你能穷举 -2-、· 2 ·、2/23、第2页、2019年08月23日… 到第几条？
#     · 一个段落被拆成 4~8 行，检索时切块会切在句子中间
#     · 表格被拍平成一行行文字，行列关系全部丢失
#
#   PyMuPDF 能给出**每行文字的字号和坐标**，于是可以做成真正的版面分析：
#     · 页眉页脚 —— 按坐标剔除（落在页面上下边缘的字块），不靠猜
#     · 段落重排 —— 上一行没以句末标点收尾，就和下一行是同一段
#     · 标题层级 —— 字号 ≥ 正文×1.12 的短行判为标题；再叠加「第X章/第X条/一、」结构
#     · 表格     —— page.find_tables() 还原成 markdown 表格，插回原来的纵向位置
#     · 图片     —— 显式标注占位，不静默丢弃（便于后续交 OCR 或人工）
#
# 这个模块只做「结构化」，归一化（NFKC 修康熙部首）和噪音清理仍由 mdify 收口。

import re
from collections import Counter

from documents import ocr_engine

# 句末标点：上一行以这些收尾 → 一段结束
_TERMINAL = "。！？；：!?;:…"
# 右括号类：先剥掉再判句末（「……规定。）」应当算句末）
_CLOSERS = "」』】》）)\"'”’"
# 段落起始标记：命中就强制另起一段（不并入上一行）
_BLOCK_START = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零]+[章节条款项]|"      # 第一章 / 第二条
    r"[一二三四五六七八九十]+、|"                      # 一、
    r"[（(][一二三四五六七八九十\d]+[）)]|"            # （一） / (1)
    r"\d+[.、]|"                                      # 1. / 1、
    r"[•·▪◦]\s*|"                                      # 项目符号
    r"附[件录表]|"
    r"注[:：]"
    r")"
)
# 纯页码行（文字层里偶尔混进来的）
_PAGE_NUM = re.compile(r"^[\s\-—–·•]*\d{1,4}[\s\-—–·•]*$")


def _is_terminal(line: str) -> bool:
    """这行是否以句末标点收尾（决定要不要和下一行合并）。"""
    s = line.rstrip()
    while s and s[-1] in _CLOSERS:
        s = s[:-1]
    return bool(s) and s[-1] in _TERMINAL




# 网页外壳的高频词。公文的封面页常把「网页导航 + 日期栏 + 搜索框」排成一张
# 看起来规整的表，形状校验（≥2 行 ≥2 列）拦不住它，只能按内容认。
_WEB_CHROME = ("信息公开年报", "请输入关键字", "【关闭】", "首页 |", "首页｜")


def _table_to_md(rows: list, cells: list | None = None) -> str:
    """PDF grid -> HTML with explicit geometry spans and original line breaks."""
    import html
    if len(rows) < 2 or max(map(len, rows), default=0) < 2:
        return ""
    flat = " ".join(str(c or "") for row in rows for c in row)
    if sum(w in flat for w in _WEB_CHROME) >= 2:
        return ""
    if not cells:
        return "<table>" + "".join("<tr>" + "".join(
            "<td>" + html.escape(str(c or "")).replace("\n", "<br>") + "</td>"
            for c in row) + "</tr>" for row in rows) + "</table>"
    # Boundaries come from actual rectangles, never from guessed empty-cell values.
    rects = [tuple(round(float(v), 2) for v in box) for row in cells for box in row if box]
    xs = sorted({v for box in rects for v in (box[0], box[2])})
    ys = sorted({v for box in rects for v in (box[1], box[3])})
    origins, covered = {}, set()
    for r, row in enumerate(cells):
        for c, box in enumerate(row):
            if not box:
                continue
            x0, y0, x1, y1 = (round(float(v), 2) for v in box)
            rr, cc = ys.index(y0), xs.index(x0)
            rs, cs = ys.index(y1)-rr, xs.index(x1)-cc
            if (rr, cc) in origins:
                continue
            value = rows[r][c] if r < len(rows) and c < len(rows[r]) else ""
            origins[rr, cc] = (rs, cs, str(value or ""))
            covered.update((y, x) for y in range(rr, rr+rs) for x in range(cc, cc+cs))
    out = ["<table>"]
    for r in range(len(ys)-1):
        out.append("<tr>")
        for c in range(len(xs)-1):
            if (r, c) in origins:
                rs, cs, value = origins[r, c]
                value = html.escape(value).replace("\n", "<br>")
                out.append(f'<td rowspan="{rs}" colspan="{cs}">{value}</td>')
            elif (r, c) not in covered:
                raise ValueError("PDF 表格网格存在未覆盖位置，需要人工确认")
        out.append("</tr>")
    return "\n".join(out + ["</table>"])


def _join(a: str, b: str) -> str:
    """合并两行文字。

    中文直接连排（PDF 里「第 一 章」「总 则」的间隔是排版装饰，连起来反而更自然）；
    但**英文/数字被折行拆开时必须补空格**，否则 `develop` + `ment` 会黏成 `development` 之外
    的 `develop ment` 反例——总之拉丁字母边界要留空格。
    """
    if not a:
        return b
    if not b:
        return a
    if a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return a + " " + b
    return a + b


def _bbox_inside(inner, outer, tol: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - tol and inner[2] <= outer[2] + tol
            and inner[1] >= outer[1] - tol and inner[3] <= outer[3] + tol)


def _body_font_size(doc_blocks) -> float:
    """按字符数加权取众数字号，作为「正文基准」。"""
    sizes: Counter = Counter()
    for b in doc_blocks:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                t = (sp.get("text") or "").strip()
                if t:
                    sizes[round(float(sp.get("size", 0)), 1)] += len(t)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _fingerprint(text: str) -> str:
    """页眉页脚的指纹：抹掉数字和空白。

    页码每页都在变（1/9 → 2/9），但「同一句页眉」每页都一样——
    抹掉数字之后它们就同指纹了。
    """
    return re.sub(r"[0-9０-９]+", "#", re.sub(r"\s+", "", text))


def _collect_band_lines(pages, band: float = 0.12) -> dict:
    """收集落在页面上下边缘带里的文字，按指纹记录它出现在哪些页上。"""
    from collections import defaultdict

    seen: dict = defaultdict(set)
    for pi, page in enumerate(pages):
        h = page.rect.height
        lo, hi = h * band, h * (1 - band)
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                y0, y1 = ln["bbox"][1], ln["bbox"][3]
                if y1 > lo and y0 < hi:
                    continue                       # 在正文区，不参与判定
                t = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
                if t:
                    seen[_fingerprint(t)].add(pi)
    return seen


def _repeated_fingerprints(seen: dict, npages: int, ratio: float = 0.3) -> set:
    """出现在多页上的边缘文字 = 页眉/页脚。

    ★ 必须用「跨页重复」而不是「固定边距」来判：实测某文档第二页没有页眉、
       正文从 y=33 就开始（页高 792，6% 边距线在 47.5），按固定边距会把
       正文的「第三条」当页眉丢掉——**静默丢内容，比留点噪音危险得多**。
    """
    thresh = max(2, int(npages * ratio))
    return {k for k, v in seen.items() if len(v) >= thresh}


def _in_band(bbox, height: float, band: float) -> bool:
    return bbox[3] <= height * band or bbox[1] >= height * (1 - band)


# 页面文字层低于这个字数 → 视为扫描页，整页 OCR 交给 scripts/ocr2md.py。
# ★ 单一来源在 ocr_engine.TEXT_LAYER_MIN：两边一个负责扫描件、一个负责正文插图，
#   阈值错开就会出现真空或重复劳动。原先靠注释维持「两处都是 50」，现在靠 import。
_TEXT_LAYER_MIN = ocr_engine.TEXT_LAYER_MIN


def _ocr_figure(page, bbox) -> str:
    """把插图区域渲染出来做 OCR，识别结果不够格就返回空串。

    实测资料里插图的两种命运：
      · 培养方案里**图形版的课表**（358~553 个汉字）—— 这是真内容，
        `find_tables()` 找不到它（没有框线），只有 OCR 能提取
      · 校徽 / 印章 / 装饰图 —— 0~几个汉字，被质检挡掉
    """
    try:
        import numpy as np
        import pymupdf as fitz

        from documents import mdify
        from documents.ocr_engine import is_content_text, ocr_image
    except ImportError:
        return ""
    try:
        pix = page.get_pixmap(clip=fitz.Rect(bbox), matrix=fitz.Matrix(3, 3),
                              colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        text = mdify.fix_ocr_text("\n".join(ocr_image(img)))
        return text.strip() if is_content_text(text) else ""
    except Exception:
        return ""      # OCR 不可用不能拖垮整篇转换


def _page_items(page, body_hint: float | None = None, repeated: set | None = None,
                band: float = 0.12, ocr_images: bool = False, stats: dict | None = None):
    """把一页拆成带纵坐标的条目：("table"|"image"|"line", y, text[, size])。

    stats 非 None 时，顺带把「丢掉了什么」记进去（供 audit_parse.py 与入库前置门用）。
    ★ 这个函数原来的问题是**丢弃是静默的**——图片被尺寸门槛挡掉、表格被判成网页导航，
      调用方一概不知。实测 327 个图片块里 200 个被丢，但没有任何地方能看出来。
    """
    height, width = page.rect.height, page.rect.width
    page_area = height * width
    repeated = repeated or set()

    items: list[tuple] = []

    # 表格：先取出来，稍后把它覆盖范围内的文字行丢掉，避免同一份内容出现两次
    table_boxes: list[list[float]] = []
    try:
        for tb in page.find_tables().tables:
            bbox = list(tb.bbox)
            _cells = [r.cells for r in tb.rows]
            md = _table_to_md(tb.extract(), _cells)
            if not md:
                if stats is not None:
                    stats["tables_rejected"] = stats.get("tables_rejected", 0) + 1
                continue
            # 边缘带里、且每页都出现的「表格」其实是网页导航条
            if _in_band(bbox, height, band) and _fingerprint(md) in repeated:
                if stats is not None:
                    stats["tables_chrome"] = stats.get("tables_chrome", 0) + 1
                continue
            table_boxes.append(bbox)
            items.append(("table", bbox[1], md))
            if stats is not None:
                stats["tables"] = stats.get("tables", 0) + 1
    except Exception as exc:
        raise ValueError("PDF 表格解析失败，未退回丢失结构的纯文本") from exc

    blocks = page.get_text("dict")["blocks"]
    body = body_hint or _body_font_size(blocks)
    # 这页文字层的字数（用来判断「是不是扫描页」）
    page_text_len = sum(len(s.get("text", "")) for b in blocks if b.get("type") == 0
                        for ln in b.get("lines", []) for s in ln.get("spans", []))

    for b in blocks:
        if b.get("type") == 1:                      # 图片块
            x0, y0, x1, y1 = b["bbox"]
            w, h = x1 - x0, y1 - y0
            # ⚠️ 只保留**有分量**的图。网页打印的 PDF 里塞满了导航图标、banner、
            #    装饰线，全标出来会刷屏（实测某封面刷出 12 个占位）。
            #    宽而扁的（横幅）几乎必然是装饰，一并排除。
            is_banner = w >= width * 0.75 and h <= height * 0.18
            if w * h < page_area * 0.10 or h < height * 0.12 or is_banner:
                # 丢弃本身多半是对的（实测丢掉的最大图只占页面积 2.8%，都是印章/装饰），
                # 但**必须记数**——否则「内容是不是被丢了」永远无从判断。
                if stats is not None:
                    stats["images_dropped"] = stats.get("images_dropped", 0) + 1
                    stats["images_dropped_area"] = round(
                        stats.get("images_dropped_area", 0.0) + w * h / page_area, 4)
                continue
            if _in_band((x0, y0, x1, y1), height, band):
                if stats is not None:
                    stats["images_band"] = stats.get("images_band", 0) + 1
                continue
            marker = f"<!-- 图片 {int(w)}x{int(h)} -->"
            # ★ 插图 OCR 的三重把关：
            #   ① 开关打开  ② 这一页**已有文字层**  ③ 这张图**不是整页**
            #   ②③ 是为了不和 scripts/ocr2md.py 抢活：整页图 + 无文字层 = 扫描件，
            #   整页 OCR 是它的职责。两边都做会让同一份正文被灌两遍。
            if ocr_images and page_text_len >= _TEXT_LAYER_MIN and w * h < page_area * 0.85:
                text = _ocr_figure(page, (x0, y0, x1, y1))
                if text:
                    marker = f"<!-- 图片OCR:\n{text}\n-->"
                    if stats is not None:
                        stats["images_ocr"] = stats.get("images_ocr", 0) + 1
                elif stats is not None:
                    stats["images_ocr_rejected"] = stats.get("images_ocr_rejected", 0) + 1
            items.append(("image", y0, marker))
            if stats is not None:
                stats["images_kept"] = stats.get("images_kept", 0) + 1
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            text = "".join(sp.get("text", "") for sp in spans).strip()
            if not text:
                continue
            # ★ 只在**页面上下边缘带**里才把纯数字行当页码丢弃。
            #   原实现无条件丢弃「孤零零的数字行」，实测把培养方案封面专业信息条里的
            #   年级「2025」当页码删掉了——**静默丢内容**，比留个页码危险得多。
            #   真正的页码不靠这条规则：它们每页都出现、抹掉数字后同指纹，
            #   已被上面的 _repeated_fingerprints 收走。这条只兜「没重复的零星页码」，
            #   那就必须限定在页边，正文里的独立数字是数据，不是页码。
            if _in_band(ln["bbox"], height, band) and _PAGE_NUM.match(text):
                continue
            if _in_band(ln["bbox"], height, band) and _fingerprint(text) in repeated:
                continue                            # ★ 跨页重复的页眉/页脚 → 丢弃
            if any(_bbox_inside(ln["bbox"], tb) for tb in table_boxes):
                continue                            # 已在表格里出现过了
            size = max((float(sp.get("size", body)) for sp in spans), default=body)
            items.append(("line", ln["bbox"][1], text, size))

    return items, body


def _emit(items, body: float) -> str:
    """把条目按纵坐标排好，合并被强制换行拆散的段落和标题，输出 markdown。"""
    out: list[str] = []
    buf = ""          # 正在累积的段落

    def flush():
        nonlocal buf
        if buf:
            out.append(buf)
            buf = ""

    for it in sorted(items, key=lambda x: x[1]):
        kind, text = it[0], it[2]

        if kind in ("table", "image"):
            flush()
            out.append(text)
            continue

        size = it[3] if len(it) > 3 else body
        is_heading = (size >= body * 1.12 and len(text) <= 40) or _is_heading_text(text)

        if is_heading:
            flush()
            # 大标题会跨行：「关于印发《…》的通知(桂」+「电人〔2022〕2号)」。
            # 不并回去的话，一个标题会变成两个「只有几个字」的空父块。
            # 长度上限 52 是防过度合并——连续两个独立标题不该被粘成一个。
            #
            # ★ 再加一条「当前行不是新的结构单元」：光靠长度拦不住
            #   「章标题 + 紧随其后的第一款」，章标题通常不以句末标点收尾，
            #   两者一粘，第一款就失去了自己的标题位（它会被并进章标题字符串，
            #   而下游 _plain_to_md 的 _ARTICLE_RE 锚在 `^第`，匹配不上带 `## ` 前缀的行）。
            #   当前语料实测未触发，但这是纯靠运气——加判据把它堵死。
            if (out and out[-1].startswith("## ")
                    and not _is_terminal(out[-1][3:])
                    and not _STRUCT_START.match(text)
                    and len(out[-1]) + len(text) <= 52):
                out[-1] = _join(out[-1], text)
            else:
                out.append(f"## {text}")
            continue

        # 正文：上一行没以句末标点收尾，且这一行不是新段落开头 → 合并
        if buf and not _is_terminal(buf) and not _BLOCK_START.match(text):
            buf = _join(buf, text)
        else:
            flush()
            buf = text

    flush()
    return "\n\n".join(out)


_HEADING_TEXT = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零]+[章篇部分]\b|"
    r"[一二三四五六七八九十]+、|"
    r"附\s*[则件录]|目\s*录"
    r")"
)

# 「结构单元」的起点：章 / 节 / 条 / 一、 / （一） / 附则 / 目录。
# 用途只有一个——`_emit` 判断「这一行能不能并进上一个标题」时，
# 结构单元一律不许并（并了就丢掉它自己的位置）。
#
# ⚠️ 这里比 _HEADING_TEXT 多了「节」「条」「（一）」，是**刻意**的：
#    · 条：它由下游 mdify._plain_to_md 的 _ARTICLE_RE 补成 ### （实测保留率 100%），
#      _emit 不重复处理，但绝不能把它并进章标题；
#    · （一）/1.：它是**条内的款项**，属于条款原子单元的一部分，不是标题
#      （提成标题会把每条规定切成碎片，毁掉「一条 = 一个小块」的检索粒度）。
#      但它同样不能被并进标题——实测桂电教〔2026〕13号 的「第四条…」标题把
#      紧随的「(一)科研成果类」粘了进去，变成「第四条…(一)科研成果类」。
#    · 节：当前语料里没有「第X节」，先不把它提成标题——
#      `_split_headings` 会在**每一行**同层标题处切分，所以一个紧跟着条标题的
#      节标题会变成「只含标题一行」的孤儿小块。要把它提成标题，
#      必须先让切分器能把「只有标题的节」并入紧随其后的那段正文。
#      那属于切片层改造，和「递归切片」一起做。
_STRUCT_START = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零]+[章篇部分节条款项]|"
    r"[一二三四五六七八九十]+、|"
    r"[（(][一二三四五六七八九十\d]+[）)]|"
    r"附\s*[则件录]|目\s*录"
    r")"
)


def _is_heading_text(text: str) -> bool:
    """按文字特征判标题（字号不可靠时的兜底，比如全篇同字号的公文）。

    输出的层级固定为 `##`（父块）。层级体系现状：
      · 章 / 一、 / 附则 / 目录 → `##`（父块，`_split_headings(levels=(1,2))`）
      · 第X条                  → `###`（小块，由 mdify._plain_to_md 补出）
    这个「章=2 / 条=3」的划分是被 318 题基线验证过的，不要随手改。
    """
    return bool(_HEADING_TEXT.match(text)) and len(text) <= 40


def _heading_counts(md: str) -> dict:
    """产物的标题层级分布（字符串键，方便直接进 JSON）。"""
    c: Counter = Counter()
    for ln in md.split("\n"):
        m = re.match(r"^(#{1,6})\s", ln)
        if m:
            c[f"h{len(m.group(1))}"] += 1
    return dict(c)


def to_markdown_pages(content: bytes, ocr_images: bool = False) -> tuple[list[str], dict]:
    """逐页产出 markdown（与 PDF 页码一一对应，纯扫描页为 ""）+ stats。

    ★ 为什么需要「逐页」而不是整篇：混合文档（正文有文字层 + 个别页是扫描件）
      必须**只把扫描那几页换成 OCR 结果**，其余页保持版面分析。整篇 API 的页边界
      在 "\n\n".join 时就丢了，拼接方无法定位插入点，只能整篇改用 OCR ——
      那会把培养方案里 15~21 张识别得好好的表格全部拍平（实测 18 份会中招）。
    """
    import pymupdf as fitz
    stats: dict = {"pages": 0, "text_chars": 0, "scan_pages": [], "empty_pages": [],
                   "tables": 0, "images_kept": 0, "images_dropped": 0, "images_ocr": 0}
    page_mds: list[str] = []
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        pages = list(doc)
        stats["pages"] = len(pages)
        repeated = _repeated_fingerprints(_collect_band_lines(pages), len(pages))
        # ⚠️ 这里**刻意不传 body_hint**，让 _page_items 逐页各算众数字号。
        #   试过改成「全篇统一算一次」，结果是 Cookie 式回归：全篇字符数被教学进程表的
        #   小字号单元格主导（众数 9.9），于是封面信息条、正文（11~12pt）统统被判成
        #   「大于正文×1.12」的标题——培养方案标题数从 18 涨到 124，还把封面信息条
        #   粘进了文档标题。逐页自算反而自校准：散文页的众数就是散文字号。
        for pi, page in enumerate(pages):
            n = len((page.get_text() or "").strip())
            stats["text_chars"] += n
            if n < _TEXT_LAYER_MIN:
                stats["scan_pages"].append(pi + 1)
            if n == 0:
                stats["empty_pages"].append(pi + 1)
            items, body = _page_items(page, repeated=repeated,
                                      ocr_images=ocr_images, stats=stats)
            if n < _TEXT_LAYER_MIN and any(item[0] == "image" for item in items):
                stats.setdefault("unprocessed_scan_pages", []).append(pi+1)
            text = _emit(items, body)
            page_mds.append(text)
    finally:
        doc.close()
    return page_mds, stats


def to_markdown_report(content: bytes, ocr_images: bool = False) -> tuple[str, dict]:
    """同 to_markdown，但额外返回**解析质量信号** stats。

    为什么需要它：解析失败是**静默**的——23 页的扫描件会返回一小段文本，
    调用方一律当成功。实测 97 份 PDF 里 10 份解析后几乎为空，且全部通过了原有的
    「源文件有没有对应 md」覆盖检查。stats 把「哪些页需要 OCR」「丢了几张图」
    「表格认出来几张」变成可判定的数，供 scripts/audit_parse.py 和入库前置门使用。

    关键字段：
      scan_pages      —— 文字层 < _TEXT_LAYER_MIN 的页号（= 该走 scripts/ocr2md.py）
      text_chars      —— 文字层总字数（判断「是不是网页外壳」用）
      images_dropped  —— 被尺寸/banner 门槛丢掉的图片块数（原来完全静默）
      tables          —— 认出来的表格数；tables_rejected = 形状/内容不过关被丢的

    需要按页处理（混合文档的 OCR 拼接）时用 to_markdown_pages()。
    """
    page_mds, stats = to_markdown_pages(content, ocr_images=ocr_images)
    md = "\n\n".join(t for t in page_mds if t.strip())
    stats["headings"] = _heading_counts(md)
    return md, stats


def to_markdown(content: bytes, ocr_images: bool = False) -> str:
    """PDF 字节 → Markdown（结构化：去页眉页脚、合段落、还原表格）。

    两步走：先扫一遍全篇，找出「每页都在重复」的边缘文字（= 真正的页眉页脚），
    再逐页抽取并剔除它们。这样就不会误伤「正文恰好从页顶开始」的那种页。

    ocr_images=True 时，对「有文字层的页面里的插图」做 OCR，把图里的文字抠出来
    （实测培养方案的图形版课表就是这么拿到 358~553 个汉字的）。
    默认关闭：它要加载 OCR 模型、每张图几秒，不适合后端上传接口这种交互场景。

    需要质量信号（扫描页 / 被丢图片 / 表格计数）时用 to_markdown_report()。
    """
    return to_markdown_report(content, ocr_images=ocr_images)[0]


def to_markdown_with_fallback(content: bytes, ocr_images: bool = False) -> str:
    """PyMuPDF 不可用时退回 mdify 的 pypdf 路径，保证链路不断。"""
    try:
        return to_markdown(content, ocr_images=ocr_images)
    except Exception:
        from documents import mdify
        return mdify._extract_pdf_pypdf(content)
