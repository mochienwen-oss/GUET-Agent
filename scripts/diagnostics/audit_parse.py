# audit_parse.py —— 解析层质检：把「读乱」拆成可计数的缺陷
#
# 为什么必须有它：
#   audit_corpus.py 查的是「源文件有没有转成 md」（覆盖）和**切分**，它查不出
#   「转了、但内容是空的」，也查不出条结构丢失、表格被当正文吞、表头残缺。
#   实测：97 份 PDF 里 10 份（10.3%）解析后几乎为空——23 页的章程只出 481 字，
#   而它**通过了原有的全部检查**（md 文件确实存在，字数没人看）。
#
#   所以解析层需要一道自己的门，且这道门不能靠人眼抽查 127 份文档维持。
#
# 判据分两类：
#   ① 一致性断言（自动推导，零人工标注）★ 这是本脚本的核心
#      · 文字层里作为**定义**出现的「第X条」条数  vs  产物里处于标题位的条数
#        —— 不等 = 条结构在转换中丢失（§「每章第一条被吞进章标题」）
#      · 同一个标题行里同时出现「第X章」和「第X条」—— 条被吞进章标题的直接证据
#      · 文字层字数 vs md 字数 —— 比值过低 = 内容在转换中丢失
#   ② 形态断言（硬上限）
#      · 超长行（>1200 字）= 表格/正文被粘成一段（_join 失去边界）
#      · 段落里混入 `|` = 表格没被识别出来，被当正文吞掉
#      · 表格块缺表头 = 碎片变孤儿，_split_table 的表头锚点失效
#      · 疑似扫描页 = 必须走 scripts/documents/ocr2md.py，否则整份文档等于没进库
#   ③ 乱码指标（2026-09-28 加）—— 详见 garbled_stats / column_stats / fragment_stats
#      · **字符级乱码**（替换符/私有区/控制符）：本语料实测 0，留作回归守卫
#      · **结构级乱码**：表格被拍平成连排文字。分轻重——重（表值丢失，FAIL）/
#        轻（栏头残片，WARN）。实测重级只命中 1 份，就是《大学英语》的折算表
#      · **双栏检测指标**：naive 命中 vs strict 命中，差额 = 宽表格行造成的误判
#      · **<100 字碎片块定位**（`--fragments`）：跨页表格续接的输入清单
#
# 用法（在**项目根目录**执行）：
#   PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py
#   PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --src guet数据文件 --all
#   PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --json base_parse.json   # 存基线
#   PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --ocr                     # 打开插图 OCR（慢）
#   PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --md-dir _md --all        # 改判已生成的 md
#   PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --md-dir _md --fragments  # 加跑碎片块定位（慢）
#
# 退出码：存在 FAIL 文档时为 1（可直接当 CI/回归门用）。

import argparse
import collections
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 与 pdfextract._TEXT_LAYER_MIN / ocr2md.py 保持同一阈值（那边由常量收口）
TEXT_LAYER_MIN = 50

_RE_ART = re.compile(r"第[一二三四五六七八九十百零〇]+条")
_RE_CHAP = re.compile(r"第[一二三四五六七八九十百零〇]+[章篇]")
_RE_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_TABLE_SEP = re.compile(r"^\|[\s:\-|]+\|$")
# 单元格内为空：`| ` + 空白 + `|`。**空格个数不固定**（拼接方式决定），必须按模式判，
# 不能写字面量 —— 原先写 `"| |"` 导致这条检查全库恒为 0（见 shape_stats 注释）。
_RE_EMPTY_CELL = re.compile(r"\|\s*(?=\|)")

# 硬上限
MAX_LINE = 1200          # 超过即视为「被粘成一段」
MIN_MD_RATIO = 0.50      # md 字数 / 文字层字数 的下限
MIN_ARTICLE_KEEP = 0.90  # 条结构保留率下限

# ---- 乱码指标（2026-09-28 加）------------------------------------------------
# ★ 乱码必须分**字符级**与**结构级**两类数，不能只数字符级。
#   实测（2026-09-28，90 份）：替换字符 U+FFFD / 私有区 / 控制符 = **0 个**；
#   而《“大学英语”课程替换及成绩认定办法》的「认定标准」表被**拍平成一段连排文字**，
#   折算分（100 / A / 80 / 85 / 95）整批丢失。只数字符级的检测器对这份文档
#   报「零乱码」——把最严重的读乱漏掉了。所以结构级才是本语料的主判据。
_GARBLED_CATS = {"Cc", "Cf", "Co", "Cs"}    # 控制符 / 格式符 / 私有区 / 代理区
_RE_MATH_OP = re.compile(r"[≤≥]")           # 数学比较符：正文里几乎不出现
_RE_MUL_OP = re.compile(r"[×÷]")
# ⚠️ 故意**不用** `<` `>` 当判据。实测它们在本语料里主要是**书名号**用法
#   （`关于印发<广西壮族自治区…>的通知`），拿它当比较符会把 4 份职称评审文档
#   误判成拍平表。只有 ≤ ≥ 是干净的判据（真表 8 个命中、误报 0）。
FLAT_STRONG_OPS = 2      # 一行 ≥2 个 ≤/≥ → 拍平表（实测 1 命中 / 0 误报）
FLAT_MUL_NUMS = 6        # 乘除号连排 + 数字
FLAT_MIN_CHARS = 40      # 太短的行不判（避免标题/编号行误报）
# 轻级：表格栏头残片（表头被另写一行；真表还在，所以只 WARN）
RESIDUE_TERMS = 3        # 一行 ≥3 个「第N学年 / 第N学期」

# ---- 双栏检测指标 ----
# 实测教训：初版检测报「70% 双栏」，实际是把**宽表格行**当成了栏（见 CLAUDE.md）。
# 所以这里**必须分开报两个数**，把误判来源暴露出来。
GUTTER_MIN_PCT = 0.08    # 栏间空带 ≥ 页宽的 8%
COL_MIN_BOXES = 5        # 每侧 ≥5 个 box 才算「有内容」

# ---- 碎片块 ----
FRAG_MAX = 100           # 「碎片块」= 索引里 <100 字的块（跨页表格续接的输入清单）
# ★ 「这份文档到底进没进库」的判据：md 字数 / 页数。
#   它一条同时覆盖三种故障——扫描件（23 页 481 字 = 21 字/页）、网页外壳
#   （2 页 66 字 = 33 字/页）、以及 OCR 补完之后是否真的补上了（23 页 11497 字 = 500 字/页）。
#   原先用的是两条不完全的规则（「疑似扫描页」+「文字层极稀薄」），
#   它们只看**源文件**，所以 ocr2md 补完之后源文件仍然「没有文字层」，
#   审计会继续报 FAIL——把已经修好的文档一直标成坏的。
#
# ⚠️ 阈值不在这里定义：收口在 backend/mdify.py，与 pdf2md / seed_knowledge / admin
#    三个入库入口共用同一套（见 mdify.quality_reasons）。改阈值改那一处。
from documents.ingest.mdify import MIN_CHARS_PER_PAGE as MIN_MD_PER_PAGE, MIN_DOC_CHARS as MIN_MD_CHARS  # noqa: E402


def _lines(md: str) -> list[str]:
    return md.split("\n")


def heading_stats(md: str) -> dict:
    """标题层级分布 + 两类结构缺陷的直接证据。"""
    levels: collections.Counter = collections.Counter()
    swallowed: list[str] = []      # 一个标题行里同时有「章」和「条」
    art_heading = 0                # 处于标题位的条数
    art_wrong_level: list[str] = []  # 条出现在 <3 级标题里（条应自成一级）
    for ln in _lines(md):
        m = _RE_HEAD.match(ln)
        if not m:
            continue
        lvl, text = len(m.group(1)), m.group(2).strip()
        levels[lvl] += 1
        has_chap = bool(_RE_CHAP.match(text))
        has_art = bool(_RE_ART.match(text))
        if has_chap and has_art:
            swallowed.append(text[:70])
        if has_art:
            art_heading += 1
            if lvl < 3:
                art_wrong_level.append(f"h{lvl} {text[:60]}")
    return {"levels": {f"h{k}": v for k, v in levels.items()}, "swallowed": swallowed,
            "art_heading": art_heading, "art_wrong_level": art_wrong_level}


def table_stats(md: str) -> dict:
    """表格块统计。判据与 vector._split_table 完全一致：≥3 行且第 2 行是分隔行。"""
    blocks, cur = [], []
    for ln in _lines(md):
        if ln.lstrip().startswith("|"):
            cur.append(ln)
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    good = [b for b in blocks if len(b) >= 3 and _RE_TABLE_SEP.match(b[1].strip())]
    bad = [b for b in blocks if b not in good]
    return {"good": len(good), "bad": len(bad),
            "bad_samples": [" ".join(b[0][:60] for b in bad[:2])] if bad else []}


def shape_stats(md: str) -> dict:
    """形态缺陷：超长行、表格被当正文吞、连续空表头格。

    ★ 「段落里混入 |」要分两种，不能一锅算（第一版没分，63 份全报 FAIL，全是假警报）：
      · pipe_swallowed —— 单元格很长（>40 字）或整行很长（>200 字）
        → 真·表格被当正文吞，块边界和表头锚点都没了，**FAIL**
      · pipe_strip     —— 整行短、单元格也短
        → 封面「专业信息条」。文字层里分隔符是**独立的 `|` 字符**（每个 `|` 自己占一行、
          没有框线），find_tables() 找不到它，于是被 _join 粘成一行。
          内容本身完整且可检索（`主修|2025|本科|建筑与交通工程学院|交通工程|170学分`），
          属于观感问题，**WARN**。
    """
    long_lines = [f"{len(l)}字" for l in _lines(md) if len(l) > MAX_LINE]
    swallowed, strip = [], []
    for l in _lines(md):
        if l.lstrip().startswith("|") or l.count("|") < 3:
            continue
        cells = [c for c in l.split("|")]
        if len(l) > 200 or max((len(c) for c in cells), default=0) > 40:
            swallowed.append(l[:80])
        else:
            strip.append(l[:80])
    # 空单元格行 —— **合并单元格被 extract() 丢成空串**的证据。
    # ⚠️ 原判据写的是**字面量** `"| |"`（一个空格），而实际产物里空格的个数由
    #    `_table_to_md` 的拼接方式决定，实测是 `|  |`（**两个空格**）→
    #    这条检查在全库恒为 0，**看不见缺陷本身**（《创新创业拓展》技能证书类表的
    #    三个合并分值就是这样漏过去的）。改成按「管道符 + 空白 + 管道符」判。
    empty_cells = 0
    for l in _lines(md):
        if l.lstrip().startswith("|") and _RE_EMPTY_CELL.search(l):
            empty_cells += 1
    return {"long_lines": long_lines, "pipe_swallowed": swallowed,
            "pipe_strip": strip, "empty_cell_rows": empty_cells}


def garbled_stats(md: str) -> dict:
    """乱码两类分开数：① 字符级（替换符 / 私有区 / 控制符）② 结构级（表格被拍平）。

    结构级又分**轻重两级**，因为实测两者的后果差一个量级，混在一起报会让门失真：

    | 级别 | 判据 | 后果 | 实测（90 份） |
    |---|---|---|---|
    | **重** `flat_tables`（FAIL） | a) 一行 ≥2 个 `≤`/`≥`；或 b) ≥2 个 `×`/`÷` + ≥6 个数字 + 句末标点 ≤2 | **表值整批丢失** | **1 份**（《大学英语》折算表） |
    | 轻 `header_residue`（WARN） | 一行 ≥3 个「第N学年 / 第N学期」 | 表格**栏头残片**：表头被另写一行，真表还在 → 索引里多一条 junk 行 | 4 份（培养方案 教学进程计划表） |

    阈值都是实测取的，且**故意不用 `<` `>`**：它们在本语料里主要是**书名号**用法
    （`关于印发<广西壮族自治区…>的通知`），当比较符用会把 4 份职称评审文档误判成拍平表。
    另有一条更松的「纯数字流」判据（≥12 个数字 + 无句末标点）**实测误报**——
    职称评审条件的「课堂授课时数折算方式」是**正常行文**（含 `毕业设计(论文)学时数=` 这类公式），
    会被它误判成拍平表。所以那条判据**已删**：宁漏不误报。

    只判**非表格行**（以 `|` 开头的行是好表格，不算乱码）。
    """
    chars: list[str] = []
    for ch in md:
        if ch in "\n\t" or ch == "�":
            continue
        if 0xE000 <= ord(ch) <= 0xF8FF or unicodedata.category(ch) in _GARBLED_CATS:
            chars.append(ch)

    flat: list[str] = []
    residue: list[str] = []
    for ln in _lines(md):
        s = ln.strip()
        if len(s) < FLAT_MIN_CHARS or s.startswith("|"):
            continue
        strong = len(_RE_MATH_OP.findall(s))
        mul = len(_RE_MUL_OP.findall(s))
        nums = len(re.findall(r"\d", s))
        # ★ 句读必须**全角半角都数**：实测漏数半角 `;` 会把职称评审的
        #   「(1)毕业设计(论文)学时数=指导学生人数×计划周数×1学时/人·周;(2)…」
        #   这段**正常行文**（讲折算公式）误判成拍平表——它 3 个 ×、数字充足，
        #   但句子读得通。真·拍平表（《大学英语》那段）**一个句读都没有**。
        endp = len(re.findall(r"[。；！？;!?，,、]", s))
        if (strong >= FLAT_STRONG_OPS
                or (mul >= 2 and nums >= FLAT_MUL_NUMS and endp == 0)):
            flat.append(s)
        elif len(re.findall(r"第\d+学[年期间]", s)) >= RESIDUE_TERMS:
            residue.append(s)
    return {"char_garbled": len(chars), "char_kinds": sorted(set(chars))[:6],
            "flat_tables": len(flat), "flat_samples": [x[:100] for x in flat[:3]],
            "header_residue": len(residue), "residue_samples": [x[:100] for x in residue[:3]]}


def column_stats(path: str) -> dict:
    """双栏检测**指标**（只做指标，不做解析改造）。

    核心是**把误判来源暴露出来**——本语料实测「0 页真·双栏」，而朴素的
    「中线左右各有不少 box」判据会报出 70%，因为**宽表格行的 box 横跨整页**：
    表格一行的左半截落在中线左侧、右半截落在右侧，朴素判据必然成立。

    所以分开报三个数：
      · naive_pages    朴素判据命中页数（= 旧版口径）
      · strict_pages   加上「**没有任何 box 跨过中线**」后命中页数 ← **这才是真双栏**
      · wide_row_pages 两者之差 = 纯由宽表格行造成的误判页数
    双栏率按 strict 算；误判率 = wide_row_pages / naive_pages。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext != ".pdf":
        return {"applicable": False, "why": "非 PDF"}
    import pymupdf as fitz
    doc = fitz.open(path)
    try:
        pages = naive = strict = 0
        samples: list[str] = []
        for pno, page in enumerate(doc):
            words = page.get_text("words") or []
            if len(words) < 2 * COL_MIN_BOXES:
                continue                       # 无文字层（扫描页）或内容太少
            pages += 1
            mid = page.rect.width / 2.0
            left = sum(1 for w in words if w[2] <= mid)
            right = sum(1 for w in words if w[0] >= mid)
            cross = sum(1 for w in words if w[0] < mid < w[2])
            if left >= COL_MIN_BOXES and right >= COL_MIN_BOXES:
                naive += 1
                if cross == 0:
                    strict += 1
                    if len(samples) < 3:
                        samples.append(f"p{pno + 1} 左{left}/右{right}")
        return {"applicable": True, "pages_with_text": pages,
                "naive_pages": naive, "strict_pages": strict,
                "wide_row_pages": naive - strict, "strict_samples": samples}
    finally:
        doc.close()


def fragment_stats(md: str, category: str = "") -> dict:
    """`<100` 字碎片块定位 —— 跨页表格续接（待办 #8）的**输入清单**。

    这些块是「表格单元格尾巴溢出成正文行」的产物：短、无上下文、检索抢不到分
    也撑不起答案。所以不只报数，还给出**块首 40 字 + heading_path**，
    可直接当人工核对清单用。

    ⚠️ 需要 `import vector`，而 vector 顶层 `import sentence_transformers`
    （实测约 27 秒）—— 所以**默认不跑**，由 `--fragments` 打开。
    """
    from agent.tools.search_regulations import tool as vector
    units = vector.build_index_units(md, category)
    frags = []
    for u in units:
        t = re.sub(r"\s", "", u["text"])
        if len(t) < FRAG_MAX:
            frags.append({
                "idx": u["chunk_idx"], "len": len(t), "type": u["block_type"],
                "path": " / ".join(u["heading_path"] or [])[:60],
                "head": re.sub(r"\s+", " ", u["text"].strip())[:40],
            })
    return {"chunks": len(units), "fragments": len(frags), "frag_list": frags}


def source_stats(path: str) -> dict:
    """源文件侧的**真值**：页数、文字层字数、作为定义出现的条数、疑似扫描页。

    「作为定义」= 该行**以**第X条开头（正文里引用的「依据第五条规定」不算）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pymupdf as fitz
        doc = fitz.open(path)
        try:
            pages = doc.page_count
            per_page, arts = [], 0
            for page in doc:
                t = page.get_text() or ""
                per_page.append(len(t.strip()))
                for ln in t.split("\n"):
                    if _RE_ART.match(ln.strip()):
                        arts += 1
            return {"pages": pages, "text_chars": sum(per_page),
                    "articles_text": arts,
                    "scan_pages": [i + 1 for i, n in enumerate(per_page) if n < TEXT_LAYER_MIN],
                    "empty_pages": [i + 1 for i, n in enumerate(per_page) if n == 0]}
        finally:
            doc.close()
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        txt = "\n".join(p.text or "" for p in d.paragraphs)
        arts = sum(1 for ln in txt.split("\n") if _RE_ART.match(ln.strip()))
        return {"pages": 1, "text_chars": len(txt), "articles_text": arts,
                "scan_pages": [], "empty_pages": []}
    return {"pages": 1, "text_chars": 0, "articles_text": 0,
            "scan_pages": [], "empty_pages": []}


def audit_one(path: str, ocr: bool, md_override: str | None = None) -> dict:
    """体检一份文档。

    md_override 非 None 时用这份 md（= 已生成的 `_md/` 产物），不再重新转换。
    ★ 这是判断「处置有没有生效」的正确来源：ocr2md 是**独立于 pdf2md 的一遍**，
      重新从源文件转换只会得到 ocr 之前的结果，永远看不到处置的效果。
    """
    from documents.ingest import mdify

    src = source_stats(path)
    stats: dict = {}
    try:
        if md_override is not None:
            md = md_override
        else:
            report = getattr(mdify, "to_markdown_report", None)
            if report is not None:
                md, stats = report(os.path.basename(path), open(path, "rb").read(),
                                   ocr_images=ocr)
            else:
                md = mdify.to_markdown(os.path.basename(path), open(path, "rb").read(),
                                       ocr_images=ocr)
    except Exception as e:
        return {"file": path, "error": f"{type(e).__name__}: {e}", "verdict": "FAIL",
                "reasons": ["解析抛异常"]}

    h = heading_stats(md)
    t = table_stats(md)
    s = shape_stats(md)
    g = garbled_stats(md)
    col = column_stats(path)
    tc = src["text_chars"]
    ratio = (len(md) / tc) if tc else 1.0
    keep = (h["art_heading"] / src["articles_text"]) if src["articles_text"] else None

    reasons = []
    warns = []
    pages = src["pages"] or 1
    md_per_page = len(md) / pages
    # ★ 主判据：这份文档到底有没有内容进库（一条覆盖扫描件/网页外壳/处置是否生效）
    if len(md.strip()) < MIN_MD_CHARS:
        reasons.append(f"md 产出几乎为空：{len(md)} 字（< {MIN_MD_CHARS}）")
    elif md_per_page < MIN_MD_PER_PAGE:
        reasons.append(f"md 产出过稀：{len(md)} 字 / {pages} 页 = {md_per_page:.0f} 字/页"
                       f"（< {MIN_MD_PER_PAGE}）")
    if src["scan_pages"]:
        if md_per_page < MIN_MD_PER_PAGE:
            reasons.append(f"扫描页未处理：{len(src['scan_pages'])}/{pages} 页无文字层"
                           f" → 需 scripts/documents/ocr2md.py")
        else:
            warns.append(f"源含 {len(src['scan_pages'])}/{pages} 页扫描页，已由 ocr2md 补入"
                         f"（md {md_per_page:.0f} 字/页）")
    if h["swallowed"]:
        reasons.append(f"条被吞进章标题 {len(h['swallowed'])} 处")
    if h["art_wrong_level"]:
        reasons.append(f"条不在独立层级 {len(h['art_wrong_level'])} 处")
    if keep is not None and keep < MIN_ARTICLE_KEEP:
        reasons.append(f"条结构保留率 {keep:.0%}（文字层 {src['articles_text']} 条 → 标题位 {h['art_heading']} 条）")
    if tc >= 1000 and ratio < MIN_MD_RATIO:
        reasons.append(f"内容缩水：文字层 {tc} 字 → md {len(md)} 字（{ratio:.0%}）")
    if s["long_lines"]:
        reasons.append(f"超长行 {len(s['long_lines'])} 条（最长 {max(int(x[:-1]) for x in s['long_lines'])} 字）→ 被粘成一段")
    if s["pipe_swallowed"]:
        reasons.append(f"表格被当正文吞 {len(s['pipe_swallowed'])} 行（单元格 >40 字）")
    if t["bad"]:
        reasons.append(f"缺表头/碎片表格块 {t['bad']} 个 → 表头锚点失效")
    if s["empty_cell_rows"]:
        warns.append(f"表头含空单元格 {s['empty_cell_rows']} 行 → 合并单元格信息不全（尚未修）")
    # ★ 结构级乱码：表格被拍平 = 表值整批丢失（《大学英语》的折算分丢在这）
    if g["flat_tables"]:
        reasons.append(f"表格被拍平成连排文字 {g['flat_tables']} 段 → 表值可能整批丢失")
    if g["header_residue"]:
        warns.append(f"表格栏头残片 {g['header_residue']} 段（真表仍在，索引里多 junk 行）")
    if g["char_garbled"]:
        reasons.append(f"字符级乱码 {g['char_garbled']} 个：{''.join(g['char_kinds'])}")
    if s["pipe_strip"]:
        warns.append(f"信息条被粘成一行 {len(s['pipe_strip'])} 处（封面无框线信息条，观感问题）")
    if stats.get("images_dropped"):
        warns.append(f"被尺寸门槛丢弃的图片 {stats['images_dropped']} 个（多为印章/装饰，可接受）")

    return {
        "file": os.path.relpath(path, ROOT), "error": None,
        "pages": src["pages"], "text_chars": tc, "md_chars": len(md),
        "ratio": round(ratio, 3), "articles_text": src["articles_text"],
        "articles_heading": h["art_heading"], "article_keep": None if keep is None else round(keep, 3),
        "levels": h["levels"], "tables_good": t["good"], "tables_bad": t["bad"],
        "long_lines": len(s["long_lines"]), "pipe_swallowed": len(s["pipe_swallowed"]),
        "pipe_strip": len(s["pipe_strip"]),
        "empty_cell_rows": s["empty_cell_rows"],
        "char_garbled": g["char_garbled"], "char_kinds": g["char_kinds"],
        "flat_tables": g["flat_tables"], "flat_samples": g["flat_samples"],
        "header_residue": g["header_residue"], "residue_samples": g["residue_samples"],
        "col_naive": col.get("naive_pages"), "col_strict": col.get("strict_pages"),
        "col_wide_row": col.get("wide_row_pages"),
        "col_pages_text": col.get("pages_with_text"),
        "scan_pages": len(src["scan_pages"]),
        "images_dropped": stats.get("images_dropped"),
        "swallowed": h["swallowed"][:2], "art_wrong_level": h["art_wrong_level"][:2],
        "verdict": "FAIL" if reasons else ("WARN" if warns else "OK"),
        "reasons": reasons, "warns": warns,
    }


def _clean_stem(stem: str) -> str:
    """文件名归一（NFKC 修康熙部首 + 去空格）——与 pdf2md._clean_stem / ocr2md 的写法一致。

    必须一致才能把源文件和 `_md/` 产物对上：文件名本身会带污染
    （「桂林电⼦科技⼤学⼯程类硕⼠ 专业学位论⽂基本要求」里的康熙部首与空格），
    pdf2md 落盘时会把它们归一化，这里不归一化就找不到对应产物。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", stem)) or stem


def _kind(reason: str) -> str:
    """把一条 reason 归一成「缺陷类型」——必须去掉具体数字，否则每份文档都自成一类
    （第一版没归一，汇总里出现「疑似扫描页 1/19」「疑似扫描页 1/20」各算一类）。"""
    head = re.split(r"[\s（(→：:]", (reason or "").strip())[0]
    return head[:30] or (reason or "")[:30]


def main() -> int:
    p = argparse.ArgumentParser(description="解析层质检：把「读乱」拆成可计数的缺陷")
    p.add_argument("--src", default=os.path.join(ROOT, "guet数据文件"),
                   help="源文件目录（默认 guet数据文件）")
    p.add_argument("--ext", nargs="+", default=[".pdf", ".docx"],
                   help="纳入检查的扩展名")
    p.add_argument("--all", action="store_true", help="打印每份文档一行（默认只列 FAIL）")
    p.add_argument("--ocr", action="store_true",
                   help="打开插图 OCR（慢；默认关，与 seed_knowledge 的上传路径一致）")
    p.add_argument("--md-dir", default=None,
                   help="改判**已生成的 md 产物**（如 _md）而不是重新转换源文件。"
                        "判断「处置有没有生效」必须用这个——ocr2md 是独立于 pdf2md 的一遍，"
                        "重转源文件只会得到 ocr 之前的结果")
    p.add_argument("--json", default=None, help="把结果存成 JSON 基线")
    p.add_argument("--fragments", action="store_true",
                   help="额外跑 <100 字碎片块定位（需 import vector，慢约 27 秒；"
                        "结果写 --fragments-out，供待办 #8 跨页表格续接当输入清单）")
    p.add_argument("--fragments-out", default="fragments.json",
                   help="碎片块清单输出路径（默认 fragments.json）")
    args = p.parse_args()

    files = []
    for dirpath, _, names in os.walk(args.src):
        for n in sorted(names):
            if os.path.splitext(n)[1].lower() in args.ext:
                files.append(os.path.join(dirpath, n))
    if not files:
        print(f"❌ {args.src} 下没有 {args.ext} 文件")
        return 1

    # 把 _md 产物按 (相对目录, 归一化文件名) 建索引，供逐份取用
    md_map: dict = {}
    if args.md_dir:
        for dirpath, _, names in os.walk(args.md_dir):
            for n in sorted(names):
                if n.lower().endswith(".md"):
                    rel = os.path.relpath(dirpath, args.md_dir)
                    rel = "" if rel == "." else rel
                    md_map[(rel, _clean_stem(os.path.splitext(n)[0]))] = os.path.join(dirpath, n)
        print(f"产物索引：{args.md_dir} 下 {len(md_map)} 份 md")

    def _md_for(path: str) -> str | None:
        rel = os.path.relpath(os.path.dirname(path), args.src)
        rel = "" if rel == "." else rel
        hit = md_map.get((rel, _clean_stem(os.path.splitext(os.path.basename(path))[0])))
        if hit is None:
            return None
        try:
            return open(hit, encoding="utf-8", errors="ignore").read()
        except Exception:
            return None

    print(f"扫描 {len(files)} 份（扩展名 {'/'.join(args.ext)}，插图 OCR {'开' if args.ocr else '关'}"
          f"{'，md 来源 ' + args.md_dir if args.md_dir else ''}）…\n")
    rows = [audit_one(f, args.ocr, md_override=_md_for(f) if args.md_dir else None)
            for f in files]

    ok = [r for r in rows if r.get("verdict") == "OK"]
    warn = [r for r in rows if r.get("verdict") == "WARN"]
    bad = [r for r in rows if r.get("verdict") == "FAIL"]
    n = len(rows)

    print(f"{'=' * 104}")
    print(f"解析层体检：{n} 份　│　OK {len(ok)}　│　WARN {len(warn)}（观感问题，不影响入库）"
          f"　│　⚠️ FAIL {len(bad)}")
    print(f"{'=' * 104}")

    # 汇总：每一类缺陷各命中多少份（比逐份列表更能看出主要矛盾）
    kinds: collections.Counter = collections.Counter()
    wkinds: collections.Counter = collections.Counter()
    for r in bad:
        for rs in r.get("reasons", []):
            kinds[_kind(rs)] += 1
    for r in warn + bad:
        for ws in r.get("warns", []):
            wkinds[_kind(ws)] += 1
    if kinds:
        print("\n按 FAIL 类型（命中份数）：")
        for k, c in kinds.most_common():
            print(f"   {c:4d} 份  {k}")
    if wkinds:
        print("\n按 WARN 类型（命中份数）：")
        for k, c in wkinds.most_common():
            print(f"   {c:4d} 份  {k}")

    if bad:
        print(f"\n{'=' * 104}\nFAIL 明细\n{'=' * 104}")
        for r in bad:
            if r.get("error"):
                print(f"\n❌ {r['file']}\n     解析异常：{r['error']}")
                continue
            print(f"\n❌ {r['file']}")
            print(f"     {r['pages']} 页　文字层 {r['text_chars']} 字 → md {r['md_chars']} 字"
                  f"（{r['ratio']:.0%}）　条 {r['articles_text']}→{r['articles_heading']}"
                  f"　层级 {r['levels'] or '无'}　表格 {r['tables_good']}好/{r['tables_bad']}坏")
            for rs in r["reasons"]:
                print(f"     · {rs}")
            for ws in r.get("warns", []):
                print(f"     ~ {ws}")
            for x in r.get("swallowed", []):
                print(f"       吞并样例: {x}")
            for x in r.get("art_wrong_level", []):
                print(f"       层级样例: {x}")
            for x in r.get("flat_samples", []):
                print(f"       拍平表样例: {x}")

    if args.all:
        print(f"\n{'=' * 104}\n全部文档\n{'=' * 104}")
        print(f"{'页':>4} {'文字层':>8} {'md':>8} {'比':>5} {'条:文字→标题':>14} "
              f"{'表(好/坏)':>10} {'乱码段':>6} {'双栏(naive/strict)':>18} {'层级':>16}  文件")
        for r in sorted(rows, key=lambda x: (x.get("verdict") == "OK", x.get("file", ""))):
            if r.get("error"):
                print(f"{'--':>4} {'--':>8} {'--':>8} {'--':>5} {'--':>14} {'--':>10} "
                      f"{'--':>6} {'--':>18} {'--':>16}  {r['file']}")
                continue
            mark = {"OK": "  ", "WARN": "~ ", "FAIL": "⚠️"}.get(r["verdict"], "  ")
            lv = ",".join(f"h{k}:{v}" for k, v in sorted(r["levels"].items())) or "无"
            garb = (r.get("flat_tables") or 0) + (r.get("char_garbled") or 0)
            if r.get("header_residue"):
                garb = f"{garb}+{r['header_residue']}轻" if garb else f"{r['header_residue']}轻"
            if r.get("col_naive") is None or not r.get("col_pages_text"):
                coltxt = "—"
            else:
                coltxt = f"{r['col_naive']}/{r['col_strict']}"
            print(f"{mark}{r['pages']:>4} {r['text_chars']:>8} {r['md_chars']:>8}"
                  f" {r['ratio']:>5.0%} {r['articles_text']:>6}→{r['articles_heading']:<7}"
                  f" {r['tables_good']:>4}/{r['tables_bad']:<5} {garb:>6} {coltxt:>18}"
                  f" {lv:>16}  {os.path.basename(r['file'])[:44]}")

    # 全局：层级分布是「结构感知切片能不能做」的总信号
    agg: collections.Counter = collections.Counter()
    for r in rows:
        for k, v in (r.get("levels") or {}).items():
            agg[k] += v
    print(f"\n全库标题层级合计：" + ("  ".join(f"{k}={v}" for k, v in sorted(agg.items())) or "无"))
    no_h3 = sum(1 for r in rows if r.get("levels") and "h3" not in r["levels"] and "h4" not in r["levels"])
    has_h3 = sum(1 for r in rows if r.get("levels") and ("h3" in r["levels"] or "h4" in r["levels"]))
    print(f"  有 h3/h4（条级小块可用）：{has_h3} 份　│　"
          f"★ 无 h3/h4（小块只能按字数硬切）：{no_h3} 份")

    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n✅ 基线已存 → {args.json}")

    # ---- 乱码率（字符级 + 结构级）----------------------------------------
    print(f"\n{'=' * 104}\n乱码率（字符级：替换符/私有区/控制符 · 结构级：表格被拍平）\n{'=' * 104}")
    n_char = sum(1 for r in rows if r.get("char_garbled"))
    n_flat = sum(1 for r in rows if r.get("flat_tables"))
    n_res = sum(1 for r in rows if r.get("header_residue"))
    print(f"字符级：{n_char} 份命中（全库 {sum(r.get('char_garbled') or 0 for r in rows)} 个字符）"
          f"　← 本语料实测为 0，保留作回归守卫")
    print(f"结构级·重（表值丢失，计入 FAIL）：{n_flat} 份")
    print(f"结构级·轻（栏头残片，仅 WARN）：{n_res} 份")
    if n_flat or n_res:
        print(f"\n{'级别':<6}{'段数':>4}  {'文档':<50} 样例")
        for r in sorted([x for x in rows if x.get("flat_tables") or x.get("header_residue")],
                        key=lambda x: (-(x.get("flat_tables") or 0), -(x.get("header_residue") or 0))):
            lvl = "重" if r.get("flat_tables") else "轻"
            n = (r.get("flat_tables") or 0) + (r.get("header_residue") or 0)
            smp = ((r.get("flat_samples") or []) + (r.get("residue_samples") or [""]))[0]
            print(f"{lvl:<6}{n:>4}  {os.path.basename(r['file'])[:50]:<50} {smp[:62]}")

    # ---- 双栏检测指标 ----------------------------------------------------
    col = [r for r in rows if r.get("col_naive") is not None and r.get("col_pages_text")]
    if col:
        pn = sum(r["col_naive"] for r in col)
        ps = sum(r["col_strict"] for r in col)
        pw = sum(r["col_wide_row"] for r in col)
        tp = sum(r["col_pages_text"] for r in col)
        print(f"\n{'=' * 104}\n双栏检测指标（有文字层的页 {tp}，覆盖 {len(col)} 份）\n{'=' * 104}")
        print(f"naive 判据命中（中线左右各有 ≥{COL_MIN_BOXES} 个 box）：{pn} 页 = {pn / tp:.0%}")
        print(f"★ strict 判据命中（再加「无 box 跨过中线」）：{ps} 页 = {ps / tp:.0%}  ← 真双栏")
        print(f"其中 **纯由宽表格行造成**的误判：{pw} 页"
              f"（占 naive 的 {pw / pn:.0%}）")
        print("   → 这就是「初版报 70% 双栏」的来源：宽表格行的 box 横跨整页，"
              "左半截落在中线左侧、右半截落在右侧，朴素判据必然成立。")
        if ps:
            print("   strict 命中样例：" + "; ".join(
                f"{os.path.basename(r['file'])[:24]}[{' '.join(r.get('col_strict_samples') or [])}]"
                for r in col if r["col_strict"]))

    # ---- 碎片块定位（可选，慢）-------------------------------------------
    if args.fragments:
        print(f"\n{'=' * 104}\n<{FRAG_MAX} 字碎片块定位（跨页表格续接的输入清单）\n{'=' * 104}")
        all_frag, per_doc = [], []
        for f in files:
            md = _md_for(f) if args.md_dir else None
            if md is None:
                continue
            fr = fragment_stats(md, os.path.relpath(os.path.dirname(f), args.src))
            per_doc.append({"file": os.path.relpath(f, ROOT),
                            "chunks": fr["chunks"], "fragments": fr["fragments"]})
            for x in fr["frag_list"]:
                x["file"] = os.path.relpath(f, ROOT)
                all_frag.append(x)
        print(f"合计 {len(all_frag)} 个碎片块（<{FRAG_MAX} 字），涉及 "
              f"{sum(1 for d in per_doc if d['fragments'])} 份文档")
        by_type = collections.Counter(x["type"] for x in all_frag)
        print(f"  按块类型：{dict(by_type)}")
        print(f"  最短 {min((x['len'] for x in all_frag), default=0)} 字"
              f" / 中位 {sorted((x['len'] for x in all_frag))[len(all_frag) // 2] if all_frag else 0} 字")
        json.dump({"total": len(all_frag), "per_doc": per_doc, "fragments": all_frag},
                  open(args.fragments_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  ✅ 清单已存 → {args.fragments_out}（字段：file/idx/len/type/path/head）")

    print(f"\n退出码：{'1（有 FAIL）' if bad else '0'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
