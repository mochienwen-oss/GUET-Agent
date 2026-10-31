"""Structure-only indexing: articles/paragraph groups and explicit table rows.

No character window, overlap, or model-based rewriting. Source offsets refer to
the stored document; table evidence is a reversible cell projection, not HTML.
"""
from __future__ import annotations

import hashlib
import html
import re
from bs4 import BeautifulSoup

VERSION = "structure-evidence/1"
HEADING = re.compile(r"^(#{1,6})[ \t]+([^\r\n]+)", re.M)
HTML_TABLE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)
MD_TABLE = re.compile(r"^\|[^\r\n]*\|[ \t]*\r?\n\|[ :|\-]+\|[ \t]*(?:\r?\n\|[^\r\n]*\|[ \t]*)+", re.M)
HEADER_WORDS = {"课程名称", "课程代码", "学分", "学时", "总学时", "序号", "课程性质",
                "开课学期", "毕业要求", "分解指标", "支撑课程", "项目", "类别",
                "课程类别", "课程模块", "成绩", "备注", "认定学分", "姓名", "考核方式"}


def _id(*parts):
    return hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:24]


def _sections(text):
    # HTML tables may contain line-initial # characters: never treat these as headings.
    tables = [m.span() for m in HTML_TABLE.finditer(text)]
    matches = [m for m in HEADING.finditer(text)
               if not any(a <= m.start() < b for a, b in tables)]
    starts = sorted({0, *(m.start() for m in matches)})
    lookup, stack = {m.start(): m for m in matches}, []
    for i, a in enumerate(starts):
        if a in lookup:
            m = lookup[a]
            level = len(m[1])
            stack = [(n, t) for n, t in stack if n < level] + [(level, m[2].strip())]
        yield a, starts[i+1] if i+1 < len(starts) else len(text), [t for _, t in stack]


def table_grid(raw):
    """Expand explicit spans only. Blank cells remain blank, holes remain holes."""
    if raw.startswith("|"):
        rows = [re.split(r"(?<!\\)\|", s.strip()[1:-1]) for s in raw.splitlines()]
        raw = "<table>" + "".join("<tr>" + "".join(
            f"<{tag}>" + value.strip().replace(r"\|", "|") + f"</{tag}>"
            for value in row) + "</tr>" for i, row in enumerate(rows) if i != 1
            for tag in ["th" if i == 0 else "td"]) + "</table>"
    table = BeautifulSoup(raw, "html.parser").find("table")
    if table is None or table.find("table"):
        raise ValueError("缺少表格或包含嵌套表格，需要人工处理")
    trs = table.find_all("tr")
    grid, cells = {}, []
    for r, tr in enumerate(trs):
        c = 0
        for tag in tr.find_all(["td", "th"], recursive=False):
            while (r, c) in grid:
                c += 1
            rs, cs = int(tag.get("rowspan", 1)), int(tag.get("colspan", 1))
            if not (1 <= rs <= len(trs)-r and 1 <= cs <= 256):
                raise ValueError("非法表格合并范围")
            cell = {"cell_id": f"r{r+1}c{c+1}", "row": r+1, "column": c+1,
                    "rowspan": rs, "colspan": cs, "text": tag.get_text("\n", strip=True),
                    "explicit_th": tag.name == "th"}
            cells.append(cell)
            for rr in range(r, r+rs):
                for cc in range(c, c+cs):
                    if (rr, cc) in grid:
                        raise ValueError("表格合并区域重叠")
                    grid[rr, cc] = cell
            c += cs
    first = [x for x in cells if x["row"] == 1]
    explicit = bool(first) and all(x["explicit_th"] for x in first)
    inferred = sum(x["text"].replace("\n", "") in HEADER_WORDS for x in first) >= 2
    headers = max((x["rowspan"] for x in first), default=0) if explicit or inferred else 0
    flags = [] if explicit else ["表头为规则候选，需确认" if inferred else "表头未确认，不猜测字段"]
    width = max((c+1 for _, c in grid), default=0)
    if any((r, c) not in grid for r in range(len(trs)) for c in range(width)):
        flags.append("源表网格存在空缺，未推断或补值")
    return grid, cells, len(trs), width, headers, flags


def _row(grid, r, width, headers):
    fields, lines, seen = [], [], set()
    for c in range(width):
        cell = grid.get((r, c))
        if cell is None or cell["cell_id"] in seen:
            continue
        seen.add(cell["cell_id"])
        columns = [cc+1 for cc in range(width) if grid.get((r, cc)) is cell]
        labels, header_ids = [], []
        for hr in range(headers):
            for cc in columns:
                h = grid.get((hr, cc-1))
                if h and h["cell_id"] not in header_ids:
                    header_ids.append(h["cell_id"])
                    if h["text"]:
                        labels.append(h["text"].replace("\n", " / "))
        label = " / ".join(dict.fromkeys(labels)) or "列" + "、".join(map(str, columns))
        field = {**cell, "columns": columns, "header_cell_ids": header_ids,
                 "header": label, "inherited_from_rowspan": cell["row"] != r+1}
        fields.append(field)
        # Never deduplicate by value: two distinct cells may legitimately be equal.
        lines.append(f"{label}：{cell['text'] or '〔空白，原表未填写〕'}")
    return "\n".join(lines), fields


def build_chunks(text: str, category: str = "") -> list[dict]:
    source_sha = hashlib.sha256(text.encode()).hexdigest()
    units = []
    # A regulation mentioning 毕业要求 is not a curriculum plan. Explicit source
    # category wins; infer only from actual plan headings when uncategorized.
    if "规章制度" in category:
        is_plan = False
    elif "培养方案" in category:
        is_plan = True
    else:
        is_plan = bool(re.search(r"^#{1,6}\s*(?:[一二三四五六七八九十]+、)?(?:专业简介|教学进程计划表)", text, re.M))
    for a, b, path in _sections(text):
        sid = _id(source_sha, "section", a, b)
        tables = sorted([*HTML_TABLE.finditer(text, a, b), *MD_TABLE.finditer(text, a, b)],
                        key=lambda m: m.start())
        cursor, prose = a, []
        for m in tables:
            if m.start() < cursor:
                continue
            prose.append((cursor, m.start()))
            cursor = m.end()
        prose.append((cursor, b))
        context = "\n\n".join(HEADING.sub("", text[x:y]).strip() for x, y in prose
                                if HEADING.sub("", text[x:y]).strip())
        base = {"heading_path": path, "category": category, "chunking_version": VERSION,
                "source_sha256": source_sha, "section_parent_id": sid, "section_span": [a, b]}
        for m in tables:
            grid, cells, nrows, width, nh, flags = table_grid(m[0])
            tid = _id(source_sha, "table", m.start(), m.end())
            represented = set()
            for r in (range(nh, nrows) if nh < nrows else range(nrows)):
                body, fields = _row(grid, r, width, nh)
                if not fields:
                    continue
                represented.update(x["cell_id"] for x in fields)
                represented.update(cid for x in fields for cid in x["header_cell_ids"])
                evidence = body + ("\n\n同节正文／表外说明：\n" + context if context else "")
                row_html = "<table><tr>" + "".join(
                    "<td>" + html.escape(f["text"]).replace("\n", "<br>") + "</td>"
                    for f in fields) + "</tr></table>"
                units.append({**base, "chunk_id": _id(tid, r), "parent_id": tid,
                              "source_span": [m.start(), m.end()], "row_number": r+1,
                              "block_type": "table", "text": evidence, "display_html": row_html,
                              "table_fields": fields, "review_flags": flags})
            if represented != {x["cell_id"] for x in cells}:
                raise ValueError("表格单元格未被完整表示")
        for x, y in prose:
            spans = [(x, y)]
            # Regulations keep the complete article, including conditions and consequences.
            if is_plan and not (path and re.match(r"第.+条", path[-1])):
                spans = [(x+m.start(), x+m.end()) for m in re.finditer(
                    r"\S[\s\S]*?(?=\r?\n[ \t]*\r?\n|\Z)", text[x:y])]
                grouped = []
                for start, end in spans:
                    sub = re.match(r"(\d+)\.\d+", text[start:end])
                    if sub and grouped and re.match(re.escape(sub[1])+r"\.", text[grouped[-1][0]:]):
                        grouped[-1] = (grouped[-1][0], end)
                    else:
                        grouped.append((start, end))
                spans = grouped
            for start, end in spans:
                body = text[start:end]
                if not HEADING.sub("", body).strip():
                    continue
                units.append({**base, "chunk_id": _id(sid, start, end), "parent_id": sid,
                              "source_span": [start, end], "block_type": "paragraph",
                              "text": body, "raw_text": body, "review_flags": []})
    units.sort(key=lambda u: (u["source_span"][0], u.get("row_number", 0)))
    for i, unit in enumerate(units):
        unit.update(chunk_idx=i, previous_id=units[i-1]["chunk_id"] if i else None,
                    next_id=units[i+1]["chunk_id"] if i+1 < len(units) else None)
    return units
