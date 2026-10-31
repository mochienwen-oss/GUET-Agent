# mdify.py —— 把各种数据文件统一转成 Markdown 文本，供 rag 按 md 结构切分
#
# 非 md 文件（txt/pdf/docx/html）先规范化为 Markdown：标题层级（# / ## / ###）
# 会被切分器当作父块/小块的天然边界。
import io
import re
import unicodedata

# 中文章节 / 条款结构标记：识别成不同层级的 Markdown 标题
# ⚠️ 结尾**不能**要求空格或行尾：大量公文是「第一条本办法适用于…」紧贴正文的写法，
#    加了 (?:\s|$) 会让整篇一条「第X条」都匹配不到（实测博士后实施办法 30+ 条匹配到 0 条），
#    进而整篇建不出 ### 标题 → 父子分块退化。
_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零]+[章篇部分]")
_ARTICLE_RE = re.compile(r"^第[一二三四五六七八九十百零]+条")
# 只有序号、没有章名的孤标题（章名被排到了下一行）
_RE_CHAPTER_ONLY = re.compile(r"^#{1,6}\s*第[一二三四五六七八九十百零]+[章篇部分]\s*$")
# 「一、二、三、」式章节标题。
# 本科培养方案用的就是这个（data2.py 已产出「## 一、专业简介」），
# 研究生院那份「全校汇总」培养方案也用它——不认它的话 12.9 万字会被按
# chunk_parent_len 每 1000 字硬切，块边界全落在句子中间。
_SECTION_CN_RE = re.compile(r"^[一二三四五六七八九十]+、\S")

# 整行噪音（可安全删除；仅匹配「整行」避免误删正文）
_NOISE_RES = [
    re.compile(r"^https?://\S+$"),                              # URL 整行
    re.compile(r"^\d{1,3}/\d{1,3}$"),                           # 页码 1/9
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{1,2}$"),   # 抓取时间戳 2026/9/18 12:04
    # ↓ 政教文件多为「网页打印成 PDF」，页眉/页脚会把时间戳、URL、页码和标题挤在同一行
    re.compile(r"^https?://\S+\s+\d+/\d+$"),                    # https://... 1/9
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{1,2}\s+\S"),   # 2026/9/18 12:04 关于印发…
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日\s+星期[一二三四五六日]\s+.*请输入关键字$"),  # 网页日期栏
    re.compile(r"^首页\s*\|"),                                   # 网页导航「首页 | 信息公开年报」
    # ↓ 网页导航条 / 搜索框。网页把导航排成 <table>，抽成文字后可能带前导的 `|`，
    #   于是躲过了上一条规则（实测「|首页信息公开年报2026年9月18日 星期五…请输入关键字」）
    re.compile(r"^[\s\|·•]*首页[\s\|·•]*信息公开年报"),
    re.compile(r".*请输入关键字"),                                # 搜索框文字，公文里绝不会出现
    re.compile(r"^【?关闭】?$"),                                  # 网页按钮
    # ↓ 各种页码写法。穷举不完，但常见的这几种够用；真正可靠的剔除在 pdfextract 里按坐标做
    re.compile(r"^[\s\-—–·•]*\d{1,4}[\s\-—–·•]*$"),            # -2- / · 2 · / 2
    re.compile(r"^第\s*\d{1,4}\s*页(?:\s*[,，/]\s*共\s*\d{1,4}\s*页)?$"),   # 第2页 / 第2页,共23页
    re.compile(r"^\d{1,4}\s*/\s*\d{1,4}$"),                     # 2/23
    re.compile(r"^[-—–_=]{3,}$"),                                # 分隔线
]

# 段落起始标记：命中就强制另起一段，不和上一行合并
_BLOCK_START_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s|"                                      # markdown 标题
    r"\|"                                             # markdown 表格行
    r"第[一二三四五六七八九十百零]+[章节条款项]|"      # 第一章 / 第二条
    r"[一二三四五六七八九十]+、|"                      # 一、
    r"[（(][一二三四五六七八九十\d]+[）)]|"            # （一） / (1)
    r"\d+[.、）)]|"                                    # 1. / 1、 / 1)
    r"[•·▪◦]\s*|"                                      # 项目符号
    r"附\s*[件录则表]|目\s*录|注[:：]"
    r")"
)
_TERMINAL_CHARS = "。！？；：!?;:…"
_CLOSER_CHARS = "」』】》）)\"'”’"


def _is_table_row(line: str) -> bool:
    """markdown 表格行。

    ★ 表格行**必须逐行保留**，绝不能参与「合并强制换行」：
      表格行以 `|` 结尾（不在句末标点里），下一行以 `|` 开头（不在段落起始标记里），
      原来的合并条件两条都满足 → 整张表被逐行吞成一个超长单行，分隔行 `| --- |`
      还会被塞进行中间，表格结构彻底销毁。实测某文档出现 2268 字符、492 个 `|` 的单行。
    """
    return line.lstrip().startswith("|")


def _ends_sentence(line: str) -> bool:
    """这行是否以句末标点收尾（决定要不要和下一行合并成一段）。"""
    s = line.rstrip()
    while s and s[-1] in _CLOSER_CHARS:
        s = s[:-1]
    return bool(s) and s[-1] in _TERMINAL_CHARS


# ---------------------------------------------------------------- 入库质量门
# ★ 判据的**单一来源**：scripts/diagnostics/audit_parse.py 与三个入库入口都 import 这几个常量，
#   避免「两处各写一个 200，改了一处」那种靠注释维持的一致。
#
# 为什么需要这道门：解析失败是**静默**的——实测 97 份 PDF 里 28 份（29%）解析后
# 几乎为空（23 页的章程只出 481 字），而它们全部通过了「源文件有没有对应 md」的
# 覆盖检查。空文档进库比不进库更糟：它让检索看起来正常但答不出来。
MIN_DOC_CHARS = 200        # md 绝对字数下限（与 scripts/documents/pdf2md.py 原 _MIN_OK 对齐）
MIN_CHARS_PER_PAGE = 150   # md 字数 / 源文件页数 的下限


def quality_reasons(md: str, pages: int | None = None, stats: dict | None = None) -> list[str]:
    """入库前的质量判据 → 拒绝理由列表（空列表 = 合格）。

    pages 未知时只做绝对字数判断（md-only 的入口拿不到页数）。
    stats 来自 pdfextract/mdify 的 to_markdown_report，有它才能看出「扫描页没处理」。
    """
    out: list[str] = []
    n = len((md or "").strip())
    if n < MIN_DOC_CHARS:
        out.append(f"md 产出几乎为空：{n} 字（< {MIN_DOC_CHARS}）")
        return out            # 字数为 0/极小，后面的比例判断没有意义
    if pages:
        per = n / pages
        if per < MIN_CHARS_PER_PAGE:
            out.append(f"md 产出过稀：{n} 字 / {pages} 页 = {per:.0f} 字/页"
                       f"（< {MIN_CHARS_PER_PAGE}）")
        if stats and stats.get("scan_pages") and per < MIN_CHARS_PER_PAGE:
            out.append(f"扫描页未处理：{len(stats['scan_pages'])}/{pages} 页无文字层"
                       f" → 先跑 scripts/documents/ocr2md.py")
    return out


def source_type_of(filename: str) -> str:
    """按扩展名判断来源类型。"""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith((".html", ".htm")):
        return "html"
    if name.endswith(".md"):
        return "md"
    return "txt"


def _normalize(text: str) -> str:
    """NFKC 归一化 —— 修「康熙部首」污染，这一步不能省。

    政教文件常是「网页打印成 PDF」，网页字体子集化会把一部分字形映射到
    Unicode 康熙部首区（U+2F00–U+2FDF）而不是正常 CJK 汉字，例如：
        博⼠后 → ⼠ 是 U+2F26，不是「士」U+58EB
        第⼀条 → ⼀ 是 U+2F00，不是「一」U+4E00
        电⼦科技⼤学 → ⼦ U+2F26 / ⼤ U+2F24

    后果是双重的：
      1. embedding 里它们是**完全不同的 token**，用户搜「博士后」永远匹配不到「博⼠后」；
      2. _ARTICLE_RE 用正常汉字写正则，匹配不到「第⼀条」→ 整篇建不出 ### 标题，
         父子分块全部退化（实测某文档 8 个「第X章」能匹配、0 个「第X条」能匹配）。

    NFKC 会把康熙部首映射回规范汉字，一行修好。
    """
    return unicodedata.normalize("NFKC", text or "")


# ---------------------------------------------------------------- OCR 错字修正
# ⚠️ 只该用在 **OCR 结果** 上（调用方见 scripts/documents/ocr2md.py）。原生文字层不会有这类
#    形近误识，拿它去改原生文本反而有改错的风险。
#
# 这是「降低人工校验成本」最划算的一招：你只需要通读一遍，把**规律性**的错字登记到
# 这里，之后所有 OCR 文档自动受益。零星的一次性错字就靠人工顺手改，不必强求脚本完美。
_OCR_FIXES = [
    # 风 / 凤 —— 公文里「凤」几乎必然是「风」的误识（凤凰等专名除外，故只做双字词）
    ("师凤", "师风"), ("凤清", "风清"), ("凤气", "风气"), ("凤险", "风险"),
    ("凤格", "风格"), ("凤尚", "风尚"), ("作凤", "作风"), ("凤雨", "风雨"),
    # 诣 / 谐
    ("造谐", "造诣"),
    # 其他常见形近误识（按需往下加）
    ("党凤", "党风"), ("校凤", "校风"), ("学凤", "学风"),
]
_OCR_FIX_RES = [(re.compile(a), b) for a, b in _OCR_FIXES]


def fix_ocr_text(text: str) -> str:
    """OCR 后处理：修已知的形近/同音误识 + 数字间被误识成「一」的破折号。"""
    if not text:
        return text
    for pat, rep in _OCR_FIX_RES:
        text = pat.sub(rep, text)
    # 「3一8人」→「3—8人」：数字之间的「一」几乎必然是破折号
    return re.sub(r"(?<=\d)一(?=\d)", "—", text)


def _convert(filename: str, content: bytes, ocr_images: bool = False) -> tuple[str, dict]:
    """按扩展名分派 → (markdown, stats)。to_markdown / to_markdown_report 共用。

    stats 只有 PDF 路径能给出完整信号（页级文字层、扫描页、被丢图片）；
    其余格式返回一个最小 stats，键存在但为 0 —— 调用方不必分格式判断。
    """
    name = (filename or "").lower().strip()
    empty = {"pages": 0, "text_chars": 0, "scan_pages": [], "empty_pages": [],
             "tables": 0, "images_kept": 0, "images_dropped": 0, "images_ocr": 0,
             "headings": {}, "source_type": source_type_of(filename)}
    if name.endswith(".md"):
        md = content.decode("utf-8", "ignore")
        empty["headings"] = _heading_counts(md)
        return md, empty
    if name.endswith(".txt"):
        md = _plain_to_md(content.decode("utf-8", "ignore"))
        empty["headings"] = _heading_counts(md)
        return md, empty
    if name.endswith(".pdf"):
        # Layout/table extraction is mandatory: never silently replace a failed
        # structured parse with plain text and pretend tables were preserved.
        from documents.ingest import pdfextract
        raw, stats = pdfextract.to_markdown_report(content, ocr_images=ocr_images)
        if stats.get("unprocessed_scan_pages"):
            pages = "、".join(map(str, stats["unprocessed_scan_pages"]))
            raise ValueError(f"第 {pages} 页为未完成OCR的扫描内容；请先审校OCR结果后上传Markdown，本次未入库")
        md = _plain_to_md(raw)
        # ★ headings 必须在**收口之后**重算：_plain_to_md 会把「第X条」补成 ###，
        #   在 pdfextract 那一层数出来的层级是不全的（实测公文 472 个 h3 全在这一步产生）。
        stats["headings"] = _heading_counts(md)
        stats["source_type"] = "pdf"
        return md, stats
    if name.endswith(".docx"):
        # 收口：NFKC 归一化 + 噪音清理 + 按文字特征（第X章 / 一、）补出标题层级。
        # 公文 docx 常「加粗+手动放大字号」而不用 Word 的 Heading 样式，
        # 只靠样式名会得到零个标题 → 父子分块退化成按字数硬切。
        md = _plain_to_md(_docx_to_md(content))
        empty["headings"] = _heading_counts(md)
        empty["source_type"] = "docx"
        return md, empty
    if name.endswith((".html", ".htm")):
        md = _html_to_md(content.decode("utf-8", "ignore"))
        empty["headings"] = _heading_counts(md)
        return md, empty
    raise ValueError("仅支持 .txt / .md / .pdf / .docx / .html 文件")


def _heading_counts(md: str) -> dict:
    """标题层级分布（字符串键，可直接进 JSON）。"""
    c: dict = {}
    for ln in (md or "").split("\n"):
        m = re.match(r"^(#{1,6})\s", ln)
        if m:
            k = f"h{len(m.group(1))}"
            c[k] = c.get(k, 0) + 1
    return c


def to_markdown(filename: str, content: bytes, ocr_images: bool = False) -> str:
    """按扩展名分派，把文件原始字节转成 Markdown 文本。

    ocr_images=True 时对**正文插图**做 OCR（把图里的文字抠出来）。默认关闭：
    它要加载 OCR 模型、每张图几秒，不适合后端上传接口这种交互场景；
    批量转换脚本（pdf2md / fetch_doc）才该打开。

    ⚠️ 线上（routers/admin.py，走默认 ocr_images=False）与批量脚本（pdf2md.py，
    传 ocr_images=True）目前**开关不一致**，等于同一份文件线上线下解析结果不同。
    统一开关是待办项，见 CLAUDE.md「硬约束」。
    """
    return _convert(filename, content, ocr_images=ocr_images)[0]


def to_markdown_report(filename: str, content: bytes,
                       ocr_images: bool = False) -> tuple[str, dict]:
    """同 to_markdown，额外返回解析质量信号 stats。

    用途：入库**前置门**。解析失败是静默的——一份 23 页的扫描件会返回一小段文本，
    调用方当成功，于是「文档进库了但里面几乎没内容」这件事没人知道。
    先看 stats 再决定入不入库，比入库后从检索指标倒查便宜得多。
    """
    return _convert(filename, content, ocr_images=ocr_images)


def iter_with_heading_path(md: str):
    """逐行产出 (line, heading_path)，heading_path 是这一行所处的标题栈。

    标题栈规则：遇到层级 n 的标题，先弹出栈里层级 >= n 的，再压入 n。
    于是「第二条」的 heading_path 是 ["第二章 组织管理", "第二条 …"]。

    为什么必须在解析层确立：下游拿到的只是一串 markdown，章/条的归属只能靠
    重扫标题栈还原；而溯源引用（答案末尾标注「《X》第二章 第二条」）和
    结构感知切片都要用这个归属。让每个消费方各自正则猜一遍，迟早猜岔。
    """
    stack: list[tuple[int, str]] = []
    for ln in (md or "").split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lvl, text = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, text))
        yield ln, [t for _, t in stack]


def _extract_pdf_pypdf(content: bytes) -> str:
    """pypdf 纯文本抽取 —— 只作兜底。

    ⚠️ 它的输出会带页眉/页脚/页码，且一个段落被拆成多行（按视觉行断开）。
    正常情况下请走 backend/pdfextract.py 的结构化抽取。
    """
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    raw = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return _normalize(raw)   # ★ 见 _normalize：不归一化会让「第⼀条」层级的标题全部丢失


def _is_noise(line: str) -> bool:
    return any(r.match(line) for r in _NOISE_RES)


def _plain_to_md(text: str) -> str:
    """纯文本 → md：识别中文「章 / 条」结构为标题层级，并清理整行噪音。

    非标题行与空行原样保留，不破坏原有段落结构。
    """
    # Treat complete tables as atomic source, outside normalization, heading
    # recognition and wrapped-line merging. Do not flatten cell line breaks.
    tables = []
    def hold_table(match):
        tables.append(match[0])
        return f"\n\nGUETTABLEPLACEHOLDER{len(tables)-1}END\n\n"
    text = re.sub(r"<table\b[^>]*>.*?</table\s*>", hold_table, text, flags=re.I | re.S)
    out: list[str] = []
    for ln in _normalize(text).split("\n"):
        s = ln.strip()
        if _is_noise(s):
            continue
        if not s:
            out.append("")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            cur, body = len(m.group(1)), m.group(2).strip()
            # ★ pdfextract 只能按**字号**判「是不是标题」，判不出「是章还是条」：
            #   一份公文里字号偏大的条会被它写成 `## 第四条…`（实测桂电教〔2026〕13号）。
            #   而下面几条规则都锚在 `^第`，匹配不到带 `## ` 前缀的行——纠正不了。
            #   条必须落在 ###：否则它在 vector._build_index_units 里会被当成**父块**
            #   （父块按 h1/h2 切、小块按 h3+ 切），「一条 = 一个小块」的检索粒度就没了。
            if cur == 2 and _ARTICLE_RE.match(body):
                out.append(f"### {body}")
                continue
            out.append(s)
            continue
        if _CHAPTER_RE.match(s):
            out.append(f"## {s}")
        elif _SECTION_CN_RE.match(s):
            out.append(f"## {s}")
        elif _ARTICLE_RE.match(s):
            out.append(f"### {s}")
        else:
            out.append(s)
    result = "\n\n".join(_merge_wrapped(_merge_orphan_heading(out)))
    for i, raw in enumerate(tables):
        result = result.replace(f"GUETTABLEPLACEHOLDER{i}END", raw)
    return result


def _join_lines(a: str, b: str) -> str:
    """合并两行文字：中文直接连排，拉丁字母/数字边界必须补空格。

    PDF 里「第 一 章」「总 则」的间隔是排版装饰，连起来反而更自然；
    但英文单词被折行拆开时不留空格就会黏成一个假词。
    """
    if not a:
        return b
    if not b:
        return a
    if a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return a + " " + b
    return a + b


def _merge_wrapped(lines: list[str]) -> list[str]:
    """把被「强制换行」拆散的段落合并回去。

    PDF 文字层和 OCR 都是「一个视觉行一条」，一个段落常被拆成 4~8 行：

        第一条  为规范学校学生管理行为,维护学校正常的教育教
        学秩序和生活秩序,保障学生合法权益,培养德、智、体、美、
        劳等方面全面发展的社会主义建设者和接班人,……

    不合并的话，检索切块会切在句子中间（chunk_small_len 一卡就断），
    喂给模型的上下文也是碎的。

    规则：上一行没以句末标点收尾、且这一行也不是新段落开头 → 并成一段。
    中文连排**不加空格**（加了空格反而会在中文字之间造出假的分词边界）。
    """
    out: list[str] = []
    buf = ""
    for ln in lines:
        s = ln.strip()
        if not s:                        # 空行 = 段落边界
            if buf:
                out.append(buf)
                buf = ""
            continue
        # ★ 表格行：连续的行必须**并入同一个块**（用单换行连接），绝不能各成一块。
        #   各成一块的话，外层 "\n\n".join 会在每行之间插空行，切分器按 \n\n 分段时
        #   会把每个表格行当成独立段落 —— 表格识别（要求 ≥3 行且第 2 行是分隔行）
        #   就永远不成立，表头重复逻辑全部失效（实测 209 个表格块只有 1 个带表头）。
        if _is_table_row(s):
            if buf and not _is_table_row(buf):
                out.append(buf)
                buf = ""
            buf = f"{buf}\n{s}" if buf else s
            continue
        if _is_table_row(buf):           # 表格到此结束，先收尾，别把正文吞进去
            out.append(buf)
            buf = ""
        # 段落合并：上一行没以句末标点收尾、且这一行不是新段落开头 → 并成一段。
        # 但不把正文并进**标题行**（`## 一、评选对象` 后面跟正文，合并会让标题吞掉整段）。
        if buf and not buf.lstrip().startswith("#") \
                and not _ends_sentence(buf) and not _BLOCK_START_RE.match(s):
            buf = _join_lines(buf, s)
            continue
        if buf:
            out.append(buf)
        buf = s
    if buf:
        out.append(buf)
    return out


def _merge_orphan_heading(lines: list[str]) -> list[str]:
    """把「## 第二章」这种只有序号、章名被排到下一行的孤标题合并回去。

    公文里的章标题常是居中两行排版：第一行「第二章」、第二行「学校的举办者」。
    OCR 和网页打印成 PDF 都会保留成两行，不合并的话父块标题只剩「第二章」三个字，
    检索和 📌 资料来源展示都很难看。
    """
    merged: list[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if _RE_CHAPTER_ONLY.match(cur.strip()):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = lines[j].strip() if j < len(lines) else ""
            # 下一行得像个「章名」：短、不是标题、不以句末标点收尾
            if (nxt and len(nxt) <= 20 and not nxt.startswith("#")
                    and not re.search(r"[。；;！？]$", nxt)):
                merged.append(f"{cur.strip()} {nxt}")
                i = j + 1
                continue
        merged.append(cur)
        i += 1
    return merged


def _docx_tc_key(tc) -> str:
    """单元格 XML 节点的稳定标识。

    ⚠️ 不能用 `id(tc)`：lxml 每次访问返回的是**代理对象**，同一个节点两次取到的
       Python 对象可能不同，`id()` 也就不同 —— 去重会静默失效。用节点在树里的
       路径字符串才是稳定的。
    """
    try:
        return tc.getroottree().getpath(tc)
    except Exception:
        return ""


def _docx_table_to_md(table) -> str:
    """DOCX XML identity -> explicit HTML spans; keep source cell line breaks."""
    from documents.ingest.pdfextract import _table_to_md

    rows, keys, locations = [], [], {}
    for r, row in enumerate(table.rows):
        rows.append([c.text or "" for c in row.cells])
        keys.append([_docx_tc_key(c._tc) for c in row.cells])
        if any(not key for key in keys[-1]):
            raise ValueError("无法定位 DOCX 单元格，未猜测合并范围")
        for c, key in enumerate(keys[-1]):
            locations.setdefault(key, []).append((r, c))
    boxes = {key: (min(c for r, c in positions), min(r for r, c in positions),
                   max(c for r, c in positions)+1, max(r for r, c in positions)+1)
             for key, positions in locations.items()}
    return _table_to_md(rows, [[boxes[key] for key in row] for row in keys])


def _docx_iter_blocks(document):
    """按**文档真实顺序**产出 ("p", 段落) / ("tbl", 表格)。

    ⚠️ 不能像原来那样分别遍历 `document.paragraphs` 和 `document.tables`：
       两个属性各自独立，交错顺序无从得知，结果是**所有表格被甩到全文末尾**，
       和它上方的正文彻底分离（一张「学分要求表」跑到文末，父块上下文全对不上）。
       python-docx 提供了 `element.body`，按 XML 子元素顺序迭代就能还原位置。
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield "p", Paragraph(child, document)
        elif tag == "tbl":
            yield "tbl", Table(child, document)


def _docx_para_images(para) -> int:
    """段落里内嵌了几张图（`w:drawing` / 老式 `w:pict`）。"""
    from docx.oxml.ns import qn
    return len(para._p.findall(".//" + qn("w:drawing"))) + len(para._p.findall(".//" + qn("w:pict")))


def _docx_to_md(content: bytes) -> str:
    """Word → Markdown（保留图表在原文中的位置）。

    输出会被 `to_markdown` 再包一层 `_plain_to_md` 收口，所以这里只管结构，
    归一化和噪音清理不在这里做。

    标题识别有两条路：优先用 Word 内置样式名；样式缺失时（公文常直接「加粗+放大字号」，
    根本不用 Heading 样式）靠 `_plain_to_md` 的文字特征规则补出层级。
    """
    import docx
    document = docx.Document(io.BytesIO(content))
    parts: list[str] = []

    for kind, block in _docx_iter_blocks(document):
        if kind == "tbl":
            md = _docx_table_to_md(block)
            if md:
                parts.append(md)
            continue

        # 段落：先处理内嵌图片
        if _docx_para_images(block):
            parts.append("<!-- 图片 -->")

        text = (block.text or "").strip()
        if not text:
            continue
        style = (block.style.name or "").lower()
        if "title" in style or "heading 1" in style or "标题 1" in style:
            parts.append(f"# {text}")
        elif "heading 2" in style or "标题 2" in style:
            parts.append(f"## {text}")
        elif "heading 3" in style or "标题 3" in style:
            parts.append(f"### {text}")
        else:
            parts.append(text)

    return "\n\n".join(parts)


def _html_to_md(html_text: str) -> str:
    """Keep explicit table structure; normalize only surrounding prose."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        tag.replace_with("\n\n" + "#" * int(tag.name[1]) + " " + tag.get_text(" ", strip=True) + "\n\n")
    tables = []
    for tag in list(soup.find_all("table")):
        if tag.find_parent("table"):
            continue
        tables.append(str(tag))
        tag.replace_with(f"\n\nGUETHTMLTABLE{len(tables)-1}END\n\n")
    result = _plain_to_md(soup.get_text("\n"))
    for i, raw in enumerate(tables):
        result = result.replace(f"GUETHTMLTABLE{i}END", raw)
    return result
