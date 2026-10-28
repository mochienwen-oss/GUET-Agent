import os
import re
from html.parser import HTMLParser

# --------------------------配置---------------------------
INPUT_DIR = r"D:\guet-agent-微调\_md\培养方案"
OUTPUT_DIR = r"D:\guet-agent-微调\_md_fixed\培养方案"
# ----------------------------------------------------------

# 【白名单：需要保留的一级章节标题（只匹配标题文本，与序号无关）】
# 保留顺序即最终输出顺序，序号重排时会依次改写为 一、二、三……
KEEP_TITLES = [
    "专业简介",
    "专业基本信息",
    "培养目标",
    "毕业要求",
    "核心课程与主要实践性教学环节",
    "学分修读要求",
    "教学进程计划表",
]

# 中文大写数字映射，用于重排序号
CN_NUM = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七",
    8: "八", 9: "九", 10: "十",
}

# 匹配一级章节标题行，兼容两种写法：
#   "## 一、专业简介"（带 markdown 井号前缀）
#   "一、培养计划学时、学分统计表"（不带前缀）
# 结尾用 [ \t]* 而非 \s*，避免吞掉标题后的换行（否则会吃掉标题与正文之间的空行）
RE_HEADING = re.compile(
    r"^(?P<mark>#{1,6}\s*)?(?P<num>[一二三四五六七八九十]+)、(?P<title>[^#\n]*?)[ \t]*$",
    re.MULTILINE,
)

RE_TABLE_BLOCK = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)


# ======================= HTML 表格解析 =======================
class _TableParser(HTMLParser):
    """把 <table> 解析成若干行 (text, rowspan, colspan) 的原始结构"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []          # list[list[(text, rowspan, colspan)]]
        self._cur_row = None
        self._cell_text = None
        self._rowspan = 1
        self._colspan = 1

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cell_text = []
            self._rowspan = max(1, int(a.get("rowspan") or 1))
            self._colspan = max(1, int(a.get("colspan") or 1))

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell_text is not None:
            text = "".join(self._cell_text).strip()
            self._cur_row.append((text, self._rowspan, self._colspan))
            self._cell_text = None
        elif tag == "tr" and self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None

    def handle_data(self, data):
        if self._cell_text is not None:
            self._cell_text.append(data)


def _expand_grid(rows):
    """
    把带 rowspan/colspan 的表格展开成规整二维网格：
      - rowspan：向下逐行复制单元格内容（让每行课程都带着所属模块）
      - colspan：只在跨列块的最左列填内容，其余列留空
    """
    if not rows:
        return []
    ncols = max((sum(cs for _, _, cs in r) for r in rows), default=0)
    grid = []   # list[list[str]]
    occ = []    # list[list[bool]]，标记已被占用的格子

    def ensure(i):
        while len(grid) <= i:
            grid.append([""] * ncols)
            occ.append([False] * ncols)

    r = 0  # 当前源行对应的“视觉行”下标
    for src_row in rows:
        ensure(r)
        c = 0
        for text, rs, cs in src_row:
            while c < ncols and occ[r][c]:
                c += 1
            for dr in range(rs):
                ensure(r + dr)
                for dc in range(cs):
                    if c + dc < ncols:
                        if dc == 0 and text:
                            grid[r + dr][c + dc] = text
                        occ[r + dr][c + dc] = True
            c += cs
        r += 1
    return grid


def _grid_to_md(grid):
    """二维网格 -> markdown 表格（首行作为表头）"""
    if not grid:
        return ""
    ncols = len(grid[0])

    def esc(cell):
        return re.sub(r"\s*\n\s*", " ", str(cell)).replace("|", "\\|")

    lines = []
    for i, row in enumerate(grid):
        cells = [esc(row[j]) if j < len(row) else "" for j in range(ncols)]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * ncols) + " |")
    return "\n".join(lines)


def html_table_to_md(html_block: str) -> str:
    """单个 <table>...</table> 块 -> markdown 表格"""
    try:
        p = _TableParser()
        p.feed(html_block)
        return _grid_to_md(_expand_grid(p.rows))
    except Exception as e:
        print(f"    [ERR]表格转换失败: {str(e)[:150]}")
        return html_block


# ======================= 章节过滤与序号重排 =======================
def filter_keep_sections(text: str) -> str:
    """只保留白名单章节，其余整块删除；文档开头（标题/元信息）一并保留"""
    matches = list(RE_HEADING.finditer(text))
    if not matches:
        return text

    # 第一个章节标题之前的文档头（如 "# 2025级XX专业培养方案" 及元信息行）
    head = text[:matches[0].start()].strip()

    kept = []
    for i, m in enumerate(matches):
        title = m.group("title").strip()
        if title in KEEP_TITLES:
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            kept.append(text[start:end])

    body = "\n\n".join(kept).strip()
    if head and body:
        return head + "\n\n" + body + "\n"
    return (body or head) + "\n"


def renumber_chapters(text: str) -> str:
    """按出现顺序，把保留章节的序号重写为 一、二、三……"""
    seq = 0

    def repl(m):
        nonlocal seq
        seq += 1
        mark = m.group("mark") or ""
        return f"{mark}{CN_NUM[seq]}、{m.group('title')}"

    return RE_HEADING.sub(repl, text)


# ======================= 主流程 =======================
def process_single_md(file_path: str, out_file_path: str):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    # step1：只保留白名单章节，其余模块整块删除
    text = filter_keep_sections(text)

    # step2：把保留章节里的 HTML 表格转成 markdown 表格（处理 rowspan/colspan）
    n_tables = len(RE_TABLE_BLOCK.findall(text))
    text = RE_TABLE_BLOCK.sub(lambda m: html_table_to_md(m.group(0)), text)

    # step3：重排章节序号
    text = renumber_chapters(text)

    os.makedirs(os.path.dirname(out_file_path), exist_ok=True)
    with open(out_file_path, "w", encoding="utf-8") as fw:
        fw.write(text)
    print(f"[OK] {os.path.basename(file_path)}：保留章节后转换 {n_tables} 个表格 -> {out_file_path}")


def walk_all_md(input_root: str, output_root: str):
    for root, _, files in os.walk(input_root):
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            src_full = os.path.join(root, fname)
            rel_path = os.path.relpath(src_full, input_root)
            dst_full = os.path.join(output_root, rel_path)
            process_single_md(src_full, dst_full)
    print("\n[完成] 全部文件处理完成！")


if __name__ == "__main__":
    walk_all_md(INPUT_DIR, OUTPUT_DIR)