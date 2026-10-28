# -*- coding: utf-8 -*-
"""build_plan_db.py —— 从 `final-md/培养方案/*.md` 抽结构化培养方案底座。

产出：
    _work/plan_db/plan_db.candidate.json  待验收的候选库
    _work/plan_db/reconcile.json     三数对账数字 + warnings
    _work/plan_db/*.json             分步中间产物（便于分段调试，不必每次全跑）

四步（对应 spec §4.2–4.5）：
    ① 封面 + 专业基本信息（一 / 二节）
    ② 教学进程计划表（十节）：表头可能竖排爆开 → 按列拼回；模块列 forward-fill；值级清洗
    ③ 毕业要求对应矩阵（六节）：倒排 课程名 → [指标点]，支撑课程贪心回并
    ④ 核心课程 / 实践环节（七节）+ 学分修读要求（九节）

**候选先行**：只写 `_work` 候选库；来源审计缺失时阻断，人工复核项显式入队。
**不碰解析层、不碰线上向量库、不碰服务器。**
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[5])
SRC_DIR = os.path.join(ROOT, "final-md", "培养方案")
OUT_DIR = os.path.join(ROOT, "backend", "data")
WORK_DIR = os.path.join(ROOT, "_work", "plan_db")
DEFAULT_OUTPUT_PATH = os.path.join(WORK_DIR, "plan_db.candidate.json")

# 归一化是**单一来源**，放在本工具的 `scripts/normalize.py`，抽取与运行时共用一份。
# 各写一份必然漂移，而那种漂移**不报错、只让匹配率悄悄掉下来**。
sys.path.insert(0, os.path.join(ROOT, "backend"))
from agent.tools.query_course.scripts.normalize import (  # noqa: E402
    CJK, cell_num, join_key, norm_cjk, to_float,
)
from agent.tools.query_course.scripts import plan_source as ps  # noqa: E402

#: 抽取器版本。**任何解析规则变更必须 bump**，并在 `_work/plan_db/extractor_changelog.md` 留一行。
EXTRACTOR_VERSION = "build_plan_db/2.2.2"
SCHEMA_VERSION = "plan_db/v2"


# ── 分节 ──────────────────────────────────────────────────────────────────

ORD = "一二三四五六七八九十"
SEC_RE = re.compile(r"^##\s*([" + ORD + r"]+)、")


def split_sections(lines):
    """按真正的节标题 `## X、` 分节。节内的 `##` 折行是缺陷产物，归入所在节。

    ⚠️ 标题行**本身也要收进该节**：本语料里正文紧跟在标题后面
    （`## 九、学分修读要求毕业总学分不低于170 学分。其中,通识必修课程46 学分…`），
    丢掉标题行会整段吞掉通识必修/通识选修的学分明细。
    """
    out, cur = {}, None
    for i, ln in enumerate(lines, 1):
        m = SEC_RE.match(ln)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        if cur:
            out[cur].append((i, ln))
    return out


def join_lines(rows):
    """把一节的折行拼回连续文本：剥 `##` 前缀后**直接相接**（不插分隔符）。

    `…计算机系` + `统基础…` → `…计算机系统基础…`
    """
    return "".join(re.sub(r"^#{1,6}\s*", "", ln).strip() for _, ln in rows)


# ── ① 封面 + 基本信息 ─────────────────────────────────────────────────────

COVER_RE = re.compile(r"^([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
                      r"\s*(?:([\d.]+)\s*学分)?\s*$")
BASIC_RE = re.compile(r"^\(([" + ORD + r"]+)\)\s*([^:：]+)[:：]\s*(.+?)\s*$")


def parse_cover(lines, warn):
    for ln in lines:
        m = COVER_RE.match(ln.strip())
        if m:
            return {
                "level": m.group(1).strip(), "grade": m.group(2).strip(),
                "degree_level": m.group(3).strip(), "college": norm_cjk(m.group(4)),
                "major": norm_cjk(m.group(5)), "total_credits": to_float(m.group(6)),
            }
    warn("cover_missing", "未找到封面行")
    return {}


def parse_basic(sec2, warn):
    """二节按 `(一)字段名:值` 抽。原样存中文键，另给工具层用的英文键。"""
    raw = {}
    for _, ln in sec2:
        m = BASIC_RE.match(ln.strip())
        if m:
            raw[norm_cjk(m.group(2))] = norm_cjk(m.group(3))
    if not raw:
        warn("basic_missing", "二节未抽到任何字段")
    return {"basic": raw, "code": raw.get("专业代码"), "degree": raw.get("授予学位"),
            "duration": raw.get("学制"), "major_field": raw.get("所属专业类")}


# ── ② 教学进程计划表（十节）───────────────────────────────────────────────

EXPECT_COLS = ["课程模块", "", "", "课程代码", "课程名称", "课程类别", "课程性质",
               "学分", "总学时", "理论学时", "实验学时", "实践学时",
               "开课学期", "是否必修", "开课部门"]
WANT_COLS = [c for c in EXPECT_COLS if c]

IS_ROW = re.compile(r"^\|.*\|\s*$")
IS_SEP = re.compile(r"^\|[\s\-:|]+\|\s*$")
#: 模块汇总行的标记：**整格**形如 `要求学分:21` / `要求子模块数:1`。
#  实测这类行是**合并单元格的副产物**：它与前一行是同一门课，只是课程代码那一格
#  被写成了汇总标记（法学 305 行 = 304 行的副本）。所以判据要按**整格**匹配，
#  且要扫**所有列** —— 标记出现在课程代码列，不在课程名列。
_SUMMARY_CELL = re.compile(r"^(?:要求学分|要求子模块数)\s*[:：]")
MAX_HDR_ROWS = 12          # 表头最多拼多少行，超了就退化（防止吞掉数据行）


def cells_of(ln):
    return [c.strip() for c in ln.strip().strip("|").split("|")]


def merge_cols(buf, n):
    """把竖排爆开的表头按列拼回：col5 = `课程类` + `别` → `课程类别`。"""
    out = [""] * n
    for cells in buf:
        for i, c in enumerate(cells[:n]):
            if c:
                out[i] += c
    return out


def _is_hdr_fragment(cells):
    """竖排表头的残片判据：所有非空单元格都**短**、且**不含数字**。

    课程代码（`000042`）带数字、课程名多半超过 6 字 —— 真数据行天然被排除。
    这条判据是防**级联污染**的：索引一旦错，真数据行会通不过 `_is_data_row`，
    若不排除就会被当成表头残片塞进缓冲，把后面真正的表头淹掉、索引再也修不回来。
    """
    ne = [c for c in cells if c]
    return bool(ne) and all(len(c) <= 6 and not any(ch.isdigit() for ch in c) for c in ne)


def _is_summary_name(name):
    """**课程名那一格**本身是模块汇总标记（`要求学分:21` / `要求子模块数:1`）。

    ★ 只判课程名列，**不扫所有列**。实测（高分子材料 293 行）：
      模块列里带 `要求学分:4` 的行本身是一门**真课程**
      （`000230 计算思维与人工智能导论(理工版)`）——
      扫全列会把它一起误杀（实测过一次：课程数 7149 → 6731，unmatched 152 → 2345）。
    ★ 也**不是**「课程名里含冒号」：那会误杀
      `模拟法学实践:模拟仲裁`(090052) 这类真课程（阶段5B 审计 F1，11 份 / 21 行）。
    """
    return bool(_SUMMARY_CELL.match(name or ""))


def _is_data_row(cells, idx):
    """数据行判据：课程名列非空 **且** 学分行可解析。

    排除 `要求学分:21 要求子模块数:1` 这类模块汇总行、表头行、页脚行。
    """
    ni, ci = idx.get("课程名称"), idx.get("学分")
    if ni is None or ci is None or max(ni, ci) >= len(cells):
        return False
    if _is_summary_name(cells[ni]):
        return False
    code_i = idx.get("课程代码")
    if code_i is not None and code_i < len(cells) and _SUMMARY_CELL.match(cells[code_i] or ""):
        # 合并单元格展开的副本行：课名/学分等与前一门课完全相同，
        # 只是课号格被「要求学分:*」覆盖。它不是新课程。
        return False
    name = cells[ni]
    if not name:
        return False
    return to_float(cells[ci]) is not None


def parse_plan_table(rows, warn):
    """返回 (courses, course_names)。

    表被 PDF 分页切成多段、每段重复表头。两个坑：
      · **列数逐片段变化**（15 列 / 14 列，封面段少一个空的合并列）
        → 表头必须**每个片段重建**，绝不能整节建一次（否则 14 列片段按 15 列索引读，
          课程名会落到「专业任选」上、学分落到学时上 —— 造出假课）
      · **表头可能竖排爆开**（本语料 2/63 份）：一格一行，需按列拼接才还原
    """
    idx, courses, names, buf = None, [], [], []
    fill = {}                                # 模块列（`课程代码` 之前的所有列）的合并单元格
    frag = 0                                 # 表片段序号（分页会重复表头，每重建一次 +1）
    for lineno, ln in rows:
        s = ln.strip()
        if not IS_ROW.match(s) or IS_SEP.match(s):
            continue
        cells = [norm_cjk(c) for c in cells_of(s)]

        if idx is not None and _is_data_row(cells, idx):
            buf = []
            # 模块列 = `课程代码` 之前的所有列。**必须由表头推出的列号算**，
            # 不能写死 0/1/2 —— 14 列片段里下标 2 其实是 `课程代码`，会把课号填进模块名。
            mod_cols = list(range(idx.get("课程代码", 3)))
            for k in mod_cols:
                if k < len(cells) and cells[k] and ":" not in cells[k] and "：" not in cells[k]:
                    fill[k] = cells[k]

            def g(key):
                i = idx.get(key)
                return cells[i] if i is not None and i < len(cells) else ""

            raw_name = cells[idx["课程名称"]]
            # 表内自带标记，文档末尾写明「★表示核心课程,▲表示主要实践环节」。
            # 直接读它比拿七节名单回配可靠；不剥掉还会挡住第六节矩阵的匹配。
            marked_core = "★" in raw_name
            marked_prac = "▲" in raw_name
            cname = raw_name.replace("★", "").replace("▲", "").strip()

            courses.append({
                "module": g("课程类别"),                           # 通识必修 / 学科基础 ...
                "module_path": [fill[k] for k in mod_cols if fill.get(k)],   # 课程模块 → 子模块 → …
                "code": g("课程代码"),
                "name": cname,
                "_marked_core": marked_core,
                "_marked_prac": marked_prac,
                "nature": g("课程性质"),
                "credits": to_float(g("学分")),
                "hours": to_float(g("总学时")),
                "theory": to_float(g("理论学时")),
                "lab": to_float(g("实验学时")),
                "practice": to_float(g("实践学时")),
                "semester": cell_num(g("开课学期")),
                "required": g("是否必修") == "必修",
                "dept": g("开课部门"),
                "src_line": lineno,
                "_raw_line": s,                    # 原始行：拒收时要能贴回报告
                "_fragment": frag,
            })
            names.append(courses[-1]["name"])
            continue

        # 非数据行 → 累积表头（正常 1 行；竖排爆开 N 行）
        if not _is_hdr_fragment(cells):
            buf = []                         # 明显不是表头残片 → 清缓冲，防止毒化
            continue
        buf.append(cells)
        if len(buf) > MAX_HDR_ROWS:
            buf = buf[-(MAX_HDR_ROWS // 2):]
        merged = merge_cols(buf, max(len(c) for c in buf))
        if all(w in merged for w in WANT_COLS):
            idx = {w: merged.index(w) for w in WANT_COLS}
            frag += 1
            if len(buf) > 1:
                warn("header_exploded", "第 %d 行：表头由 %d 行竖排拼成" % (lineno, len(buf)))
            buf = []
    return courses, names


# ── 三份「低置信」文档的针对性抽取 ─────────────────────────────────────────
# 培养方案更新不频繁，逐份特判的代价可接受（这三份是全域唯一需要特判的）。
# **判据与改动理由都写在这里，不静默**。
#
# ① 测控技术与仪器 / 自动化：表头竖排爆开**且与数据行交错**，表头索引无法稳定重建
#    → 改用「课程代码锚定」：6 位课程代码列右侧的偏移是固定的（实测 116/111 行全部满足）。
# ② 密码科学与技术：源文件两行学分漏了小数点（同行邻居都是 2，总学时也都对得上 32）
#    → 按**课号**显式修正。
#
# ★★ 2026-10-14 阶段5B 审计 F2：原来这两个键是 **(文件名, 行号)**。
#    `_md` 在 2026-10-12/13 被重洗过，行号整体漂移 → 修正落到了**别的课**上
#    （第 337 行当时已是「军事技能」，被从 0.0 改成 2.0），而真正的目标
#    「软件安全」「侧信道分析技术」学分仍是 32、被 row_rejected 丢掉。
#    **行号不是身份，内容才是** —— 改按课号索引，重洗后仍然指向同一门课。
CODE_ANCHORED = {
    "2025级测控技术与仪器专业培养方案(主修).md",
    "2025级自动化专业培养方案(主修).md",
}
CREDIT_FIXES = {
    ("2025级密码科学与技术专业培养方案(主修).md", "030246"): 2.0,   # 软件安全（源写 32）
    ("2025级密码科学与技术专业培养方案(主修).md", "030245"): 2.0,   # 侧信道分析技术（源写 32）
}
CODE_RE = re.compile(r"^\d{6}$")
VALID_COURSE_CODE_RE = re.compile(r"^(?:\d{6}|\d{7}X\d)$")
# 课程代码列往右的偏移。实测与 15 列表头一致。
CODE_OFFSETS = (("name", 1), ("module", 2), ("nature", 3), ("credits", 4),
                ("hours", 5), ("theory", 6), ("lab", 7), ("practice", 8), ("semester", 9))


def parse_plan_table_code_anchored(rows, warn):
    """课程代码锚定解析（只用于 `CODE_ANCHORED` 里的文件）。

    表格被竖排爆开的合并单元格打散时，表头不可靠，但**数据行本身是完整的**，
    且 6 位课程代码右侧的偏移恒定。开课部门在爆开的碎片里，取不到就留空。
    """
    courses, names = [], []
    for lineno, ln in rows:
        s = ln.strip()
        if not IS_ROW.match(s) or IS_SEP.match(s):
            continue
        cells = [norm_cjk(c) for c in cells_of(s)]
        i = [k for k, c in enumerate(cells) if CODE_RE.match(c)]
        if len(i) != 1 or i[0] + len(CODE_OFFSETS) >= len(cells) + 1:
            continue
        i = i[0]
        if i + max(off for _, off in CODE_OFFSETS) >= len(cells):
            continue
        f = {k: cells[i + off] for k, off in CODE_OFFSETS}
        cred = to_float(f["credits"])
        if not f["name"] or cred is None or "：" in f["name"] or ":" in f["name"]:
            continue
        course = {
            "module": f["module"], "module_path": [], "code": cells[i],
            "name": f["name"].replace("★", "").replace("▲", "").strip(),
            "_marked_core": "★" in f["name"], "_marked_prac": "▲" in f["name"],
            "nature": f["nature"], "credits": cred,
            "hours": to_float(f["hours"]), "theory": to_float(f["theory"]),
            "lab": to_float(f["lab"]), "practice": to_float(f["practice"]),
            "semester": cell_num(f["semester"]), "required": f["nature"] == "必修",
            "dept": "", "src_line": lineno,
            "_raw_line": s,
            "_fragment": 0,
        }
        if courses and courses[-1]["code"] == course["code"]:
            # 竖排爆开时，同一课号可能连续占两行，课名被拆成
            # 「写作与沟通1(结构性思」+「维)」。同课号只能产生一门课。
            previous = courses[-1]
            fragment = course["name"]
            if fragment and fragment != previous["name"] and not previous["name"].endswith(fragment):
                previous["name"] += fragment
            previous["_raw_line"] += "\n" + s
            previous["_src_end_line"] = lineno
            names[-1] = previous["name"]
            continue
        courses.append(course)
        names.append(course["name"])
    if not courses:
        warn("anchored_empty", "课程代码锚定未取到任何课程")
    return courses, names


def _apply_credit_fixes(fname, courses, warn):
    """按**课号**修正源里的学分笔误。改了必须留痕。

    ★ 键是 `(文件名, 课号)`，**不是行号** —— 见 `CREDIT_FIXES` 上方的 F2 说明。
    ★ 每次修正都写进该行的 `_corrections`（阶段5B Gate-1 第 4 条：数值异常不得被静默修复）。
    """
    for c in courses:
        fix = CREDIT_FIXES.get((fname, c["code"]))
        if fix is not None and c["credits"] != fix:
            warn("credit_corrected", "课号 %s %s 学分 %s → %s（源笔误）"
                 % (c["code"], c["name"], c["credits"], fix))
            c.setdefault("_corrections", []).append({
                "field": "credits", "from": c["credits"], "to": fix,
                "reason": "源文件漏小数点（同行邻居为 2，总学时 32 与之相符）",
                "authority": "human_review", "by": "阶段5B 审计", "date": "2026-10-14"})
            c["credits"] = fix
    return courses


# ── ③ 毕业要求对应矩阵（六节）─────────────────────────────────────────────

IND_RE = re.compile(r"^(\d+[.\-－]\d+)\s*(.*)$")     # 编号有点号 (`1.1`) 也有连字符 (`1-1`)，本语料两种都有


def _resolve_shortest(tok, name_by_key):
    """给「核心课程 / 实践环节」标位用：歧义时取**后缀最短**的候选。

    与 `_resolve` 的政策不同是故意的 —— 矩阵倒排宁可漏配也不瞎配；
    而七节列的是基名（`数据库系统原理`），表里是 `数据库系统原理A` / `数据库系统原理实验`，
    基名就该落到后缀最短的那个（`A`）上。
    """
    k = join_key(tok)
    if k in name_by_key:
        return name_by_key[k]
    cands = [v for kk, v in name_by_key.items() if kk.startswith(k)]
    return min(cands, key=len) if cands else None


def rejoin_tokens(tokens, name_by_key):
    """贪心回并：把被折行插了空格的课程名拼回来，再按 `join_key` 对齐到规范名。

    ['计算思维与人工智能导论(理工', '版)'] → ['计算思维与人工智能导论(理工版)']
    拼不回来的 token 原样留在结果里 → 进 unmatched。
    """
    out, i = [], 0
    while i < len(tokens):
        for j in range(len(tokens), i, -1):
            cand = "".join(tokens[i:j])
            hit = _resolve(cand, name_by_key)
            if hit:
                out.append(hit)
                i = j
                break
        else:
            out.append(tokens[i])
            i += 1
    return out


def split_compound_token(tok, name_by_key):
    """用同专业课程字典拆分一个粘连 token。

    只在存在**唯一的、完整覆盖的、至少两门课**切分时返回结果。
    多解、只能覆盖一部分或无解都返回 None，宁可留在 unmatched
    也不根据课名前缀猜分法。
    """
    target = join_key(tok)
    if not target or target in name_by_key:
        return None
    # 矩阵会把「写作与沟通1(结构性思维)」简写为「写作与沟通1」。
    # 与 `_resolve` 保持同一政策：只接受唯一指向某门课的前缀；为避免
    # 「工程」这类过短片段也成为别名，前缀别名至少 4 个归一化字符。
    prefix_targets = {}
    for key, name in name_by_key.items():
        for end in range(4, len(key)):
            prefix_targets.setdefault(key[:end], set()).add(name)
    aliases = dict(name_by_key)
    for prefix, names in prefix_targets.items():
        if prefix not in aliases and len(names) == 1:
            aliases[prefix] = next(iter(names))
    options = [(key, name) for key, name in aliases.items()
               if key and len(key) < len(target)]
    memo = {}

    def solve(pos):
        if pos == len(target):
            return [()]
        if pos in memo:
            return memo[pos]
        found = []
        for key, name in options:
            if not target.startswith(key, pos):
                continue
            for tail in solve(pos + len(key)):
                candidate = (name,) + tail
                if candidate not in found:
                    found.append(candidate)
                if len(found) > 1:
                    memo[pos] = found[:2]
                    return memo[pos]
        memo[pos] = found
        return found

    solutions = solve(0)
    if len(solutions) == 1 and len(solutions[0]) >= 2:
        return list(solutions[0])
    return None


def _resolve(tok, name_by_key):
    """把 token 对齐到规范课程名。

    先精确（join_key），再**前缀回退**：矩阵常写基名而表里带后缀
    （`毕业设计` vs `毕业设计(论文)`、`写作与沟通1` vs `写作与沟通1(批判性思维)`）。
    回退**只在候选唯一时生效** —— `数据库系统原理` 同时命中 `数据库系统原理A`
    和 `数据库系统原理实验` 就判为歧义、保持 unmatched，绝不瞎猜。
    """
    k = join_key(tok)
    if k in name_by_key:
        return name_by_key[k]
    cands = [v for kk, v in name_by_key.items() if kk.startswith(k) and kk != k]
    return cands[0] if len(cands) == 1 else None


def parse_matrix(rows, name_by_key, warn, split_log=None):
    """返回 (indicator_text{id: 文字}, course_indicators{课程: [id]}, unmatched[])"""
    texts, inv, unmatched = {}, {}, []
    cur_req = ""
    for lineno, ln in rows:
        s = ln.strip()
        if not IS_ROW.match(s) or IS_SEP.match(s):
            continue
        cells = cells_of(s)
        if norm_cjk(cells[0]) == "毕业要求":
            continue                                   # 表头行
        if len(cells) < 3:
            continue
        req, ind, sup = (norm_cjk(cells[0]), norm_cjk(cells[1]), cells[2])
        if req:
            cur_req = req
        m = IND_RE.match(ind)
        if not m:
            continue
        iid, itext = m.group(1), norm_cjk(m.group(2))
        texts.setdefault(iid, itext)
        for tok in rejoin_tokens([t for t in re.split(r"\s+", sup.strip()) if t], name_by_key):
            disp = _resolve(tok, name_by_key)
            if disp:
                inv.setdefault(disp, [])
                if iid not in inv[disp]:
                    inv[disp].append(iid)
            elif tok:
                parts = split_compound_token(tok, name_by_key)
                if parts:
                    for part in parts:
                        inv.setdefault(part, [])
                        if iid not in inv[part]:
                            inv[part].append(iid)
                    if split_log is not None:
                        split_log.append({
                            "line": lineno, "token": tok, "courses": parts,
                            "indicator": iid, "requirement": cur_req,
                            "reason": "unique_full_course_name_segmentation",
                        })
                else:
                    unmatched.append({"line": lineno, "token": tok, "indicator": iid, "requirement": cur_req})
    return texts, inv, unmatched


# ── ④ 核心课程 / 实践环节（七节）+ 学分明细（九节）──────────────────────────

def _strip_labels(block):
    """剥掉七节的段落标签，只留课程清单。

    实测三种写法（都在本语料里）：
        `(一)核心课程:程序设计与问题求解、…`          ← 带冒号
        `(一)核心课程学科基础课:数学分析、…`           ← 多一层标签
        `(一)核心课程机械制造基础B、机械设计基础、…`   ← **完全没有分隔符**
    所以不能靠「冒号」定位，得按标签词剥。标签**按长度降序**替换，避免
    `实践教学环节` 先吃掉 `主要实践性教学环节` 的一部分。
    """
    t = block
    for w in sorted(LABELS, key=len, reverse=True):
        t = t.replace(w, "、")
    out = []
    for x in re.split(r"[、,，;；]", t):
        x = re.sub(r"^[^:：]*[:：]", "", norm_cjk(x).strip("。")).strip()
        if x and len(x) <= 60:                    # 超长的多半是标签残渣
            out.append(x)
    return out


LABELS = ("核心课程", "主要实践性教学环节", "实践教学环节", "专业主干课程",
          "学科基础课", "实验课程", "集中实践教学环节", "专业核心课程")


def parse_core(s7):
    blob = join_lines(s7)
    core, practice = [], []
    m1 = re.search(r"\(一\)(.*?)(?=\(二\)|$)", blob, re.S)
    m2 = re.search(r"\(二\)(.*)$", blob, re.S)
    if m1:
        core = _strip_labels(m1.group(1))
    if m2:
        # The course enumeration ends before the following prose about bases,
        # enterprises and URP. Commas inside that prose are not course separators.
        listing = re.split(r"[。；;]", m2.group(1), maxsplit=1)[0]
        practice = _strip_labels(listing)
    return core, practice


MODULE_NAMES = (
    "通识必修课程|通识选修课程|数学与自然科学基础课程|"
    "专业类基础课程|专业核心课程|学科基础课程|科学基础课程|"
    "专业教育课程?|实践教学(?:课程)?|多元化教育课程"
)
MODULE_CREDIT_RE = re.compile(
    r"(%s)\s*([\d.]+)\s*(?:个学分|学分|分)" % MODULE_NAMES
)
TOTAL_CREDIT_RE = re.compile(r"毕业总学分不低于\s*([\d.]+)\s*(?:学分)?")


def parse_module_credits(s9, warn):
    blob = join_lines(s9)
    mods = {m.group(1): to_float(m.group(2)) for m in MODULE_CREDIT_RE.finditer(blob)}
    mt = TOTAL_CREDIT_RE.search(blob)
    if not mods:
        warn("module_credits_missing", "九节未抽到模块学分明细")
    return mods, (to_float(mt.group(1)) if mt else None)


# ── 主流程 ────────────────────────────────────────────────────────────────

#: 进「人工复核」队列的 warning（判据拿不准 / 修了会牵动多门课）。
#: 其余 warning 只降置信、进 warnings，不进复核队列。
REVIEW_CODES = ("anchored_empty", "module_credits_missing", "basic_missing",
                "course_code_invalid", "cover_credits_missing")

#: §8 先修图的处置状态表（由 Task 0 的渲染侦察产出）。**取值只有三个，不许留空。**
SEC8_STATUSES = ("extracted", "unavailable_by_decision", "unavailable_raster")
SEC8_TABLE = os.path.join(WORK_DIR, "sec8_recoverability.json")

def _file_sha256(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _credits_meet_requirement(modules_sum, required):
    """封面总学分是要求值；模块合计可高于它，但不能低于它。"""
    if required is None:
        return None
    return modules_sum + 1e-9 >= required


def _atomic_write(path, payload):
    """原子写：临时文件 + `os.replace`。半截文件比没有文件更坏。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(temp, target)


def _estate8(fname):
    """§8「课程先修后修关系图」的处置状态（Gate-1 第 5 条：不可恢复要**明确标记**）。

    该节在 `_md` 里被用户主动删除，所以状态只能由**源 PDF 的图形形态**决定；
    结论表由 Task 0 的渲染侦察产出。缺表、缺文件或状态非法都必须
    中止构建；不得把“没有审计结果”静默伪装成 `unavailable_raster`。
    """
    try:
        with open(SEC8_TABLE, encoding="utf-8") as fh:
            table = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取先修图审计表 %s: %s" % (SEC8_TABLE, exc)) from exc
    if not isinstance(table, dict) or fname not in table:
        raise RuntimeError("先修图审计表缺少文件: %s" % fname)
    entry = table[fname]
    value = entry.get("estate8") if isinstance(entry, dict) else None
    if value not in SEC8_STATUSES:
        raise RuntimeError("非法 estate8 状态 %r: %s" % (value, fname))
    return value


def build_one(path):
    warns = []

    def warn(code, msg):
        warns.append({"code": code, "msg": msg})

    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    sec = split_sections(lines)
    rec = {"file": os.path.basename(path)}
    rec.update(parse_cover(lines, warn))
    if not rec.get("major"):                  # 封面缺失（2/63）→ 用文件名兜底
        mm = re.match(r"^(\d{4})级(.+?)(?:专业)?培养方案", os.path.basename(path))
        if mm:
            rec.setdefault("grade", mm.group(1))
            rec["major"] = norm_cjk(mm.group(2))
    rec.update(parse_basic(sec.get("二", []), warn))

    fname = os.path.basename(path)
    if fname in CODE_ANCHORED:
        courses, names = parse_plan_table_code_anchored(sec.get("十", []), warn)
    else:
        courses, names = parse_plan_table(sec.get("十", []), warn)
    courses = _apply_credit_fixes(fname, courses, warn)
    for course in courses:
        code = course.get("code") or ""
        if code and not VALID_COURSE_CODE_RE.fullmatch(code):
            course["_invalid_code"] = True
            warn("course_code_invalid", "第 %d 行：课程「%s」课号 %r 不符合已知课号格式"
                 % (course["src_line"], course["name"], code))
    rec["core_courses"], rec["practice_courses"] = parse_core(sec.get("七", []))
    rec["module_credits"], total9 = parse_module_credits(sec.get("九", []), warn)

    rec["total_credits_source"] = "cover"
    # 封面学分缺失时可用九节值保持查询可用，但必须显式标记为回退并进人工复核。
    if rec.get("total_credits") is None:
        warn("cover_credits_missing", "封面行缺学分段")
        if total9:
            rec["total_credits"] = total9
            rec["total_credits_source"] = "section9_fallback"

    # 拒收明显读错的行 —— **宁可少一门，也不造假课**。
    # 竖排爆开的那 2 份文档（测控/自动化）会有 14/15 列混用，导致课程名落到「课程类别」上。
    # ── 硬失败：拒收明显读错的行。宁可少一门，也不造假课。
    # ★ 拒收必须是**显式动作**：每条都带原始行与理由（Gate-1 第 2 条）。
    #   阶段5B 审计 F1 的教训：旧版把「课程名列含冒号」当汇总行静默丢掉 21 行，
    #   且**不产生任何 warning** —— 最大的缺陷恰好最安静。
    strategy = "code_anchor" if fname in CODE_ANCHORED else "header"
    hard_failures, kept = [], []
    for c in courses:
        credits = c["credits"] if c["credits"] is not None else -1
        reason = None
        if not (0 <= credits <= 30):
            reason = "学分越界 %s（多半被读成了学时）" % c["credits"]
        elif c["name"] == c["module"]:
            reason = "课程名与课程类别同值「%s」—— 列错位" % c["name"]
        elif c["name"] == c["nature"]:
            reason = "课程名与课程性质同值「%s」—— 列错位" % c["name"]
        if reason:
            raw = c.get("_raw_line", "")
            hard_failures.append({
                "file": fname, "line": c["src_line"], "raw_line": raw,
                "row_hash": ps.row_hash(raw), "course_code": c.get("code"),
                "course_name": c.get("name"), "reason": reason})
        else:
            kept.append(c)
    if hard_failures:
        warn("row_rejected", "硬失败拒收 %d 行（原文与理由见 _work/plan_db/rejected.json）"
             % len(hard_failures))
    rec["courses"] = kept
    names = [c["name"] for c in kept]

    # 六节矩阵的「支撑课程」取自**两个来源**：十节教学计划表 + 七节核心课程/实践环节。
    # 生产实习、毕业设计 只出现在七节，不在十节表里 —— 只用十节会误报成「不一致」。
    name_by_key = {}
    for n in names + rec["core_courses"] + rec["practice_courses"]:
        name_by_key.setdefault(join_key(n), n)

    # ⚠️ 核心/实践标位只能对**教学计划表**解析。若拿上面那份含七节名字的 `name_by_key` 去解析，
    #    `离散数学` 会精确命中「七节里的它自己」而不是课表里的 `离散数学A` —— 标位就全落空。
    plan_by_key = {join_key(n): n for n in names}
    core_set = {_resolve_shortest(x, plan_by_key) for x in rec["core_courses"]}
    prac_set = {_resolve_shortest(x, plan_by_key) for x in rec["practice_courses"]}
    unres = [x for x in rec["core_courses"] + rec["practice_courses"]
             if _resolve_shortest(x, plan_by_key) is None]
    if unres:
        warn("syllabus_name_unresolved", "七节 %d 个名字对不上教学计划表：%s"
             % (len(unres), "、".join(unres[:5])))
    for c in rec["courses"]:
        c["is_core"] = bool(c.pop("_marked_core", False)) or c["name"] in core_set
        c["is_practice"] = bool(c.pop("_marked_prac", False)) or c["name"] in prac_set

    matrix_splits = []
    rec["indicator_text"], inv, rec["unmatched_courses"] = parse_matrix(
        sec.get("六", []), name_by_key, warn, split_log=matrix_splits)
    rec["matrix_splits"] = matrix_splits
    if matrix_splits:
        warn("matrix_compound_split", "唯一完整切分 %d 个粘连课程 token" % len(matrix_splits))
    for c in rec["courses"]:
        c["indicators"] = inv.get(c["name"], [])

    # ── 行级 provenance + 置信状态（Task 2）──
    header_exploded = any(w["code"] == "header_exploded" for w in warns)
    for c in rec["courses"]:
        raw = c.pop("_raw_line", "")
        c["src"] = {"file": fname, "line": c["src_line"],
                    "row_hash": ps.row_hash(raw), "fragment": c.pop("_fragment", 0)}
        end_line = c.pop("_src_end_line", None)
        if end_line is not None:
            c["src"]["end_line"] = end_line
        c["extractor"] = {"name": "build_plan_db", "version": EXTRACTOR_VERSION,
                          "strategy": strategy}
        corrections = c.pop("_corrections", [])
        c["corrections"] = corrections
        reasons, level = [], "high"
        if corrections:
            level = "medium"; reasons.append("该行的值被修正过（见 corrections）")
        if header_exploded:
            level = "medium" if level == "high" else level
            reasons.append("本文件的表头由多行竖排拼成")
        if strategy == "code_anchor":
            level = "low"; reasons.append("该行来自课程代码锚定（表头不可靠）")
        if not c.get("dept"):
            reasons.append("开课部门取不到")
            level = "medium" if level == "high" else level
        if c.pop("_invalid_code", False):
            reasons.append("课程代码不符合已知格式，需人工回源复核")
            level = "low"
        c["confidence"] = {"level": level, "reasons": reasons}

    # ── 质量状态与来源（Task 3 / Task 8）──
    review_items = [w for w in warns if w["code"] in REVIEW_CODES]
    if hard_failures or review_items:
        status = "needs_review"
    elif warns:
        status = "warn"
    else:
        status = "ok"
    modules_sum = round(sum(v for v in rec["module_credits"].values() if v), 2)
    declared = rec.get("total_credits")
    reconcile_target = declared
    modules_consistent = _credits_meet_requirement(modules_sum, reconcile_target)
    if modules_consistent is False:
        warn("module_credits_below_cover",
             "模块学分合计 %s 低于封面要求值 %s"
             % (modules_sum, reconcile_target))
    rec["credits"] = {
        "total_declared": declared,
        "section9_total": total9,
        "modules_sum": modules_sum,
        "reconcile_target": reconcile_target,
        "consistent": modules_consistent,
        "rule": "modules_sum_gte_cover_total",
    }
    review_items = [w for w in warns if w["code"] in REVIEW_CODES]
    if hard_failures or review_items:
        status = "needs_review"
    elif warns:
        status = "warn"
    else:
        status = "ok"
    rec["quality"] = {"status": status, "hard_failures": hard_failures,
                      "warnings": warns, "review_items": review_items}
    rec["provenance"] = {"file": fname, "sha256": _file_sha256(path),
                         "estate8": _estate8(fname), "strategy": strategy}

    rec["_metrics"] = {
        "n_courses": len(courses),
        "low_confidence": status != "ok",
        "credits_sum": round(sum(c["credits"] for c in courses), 2),
        "core_listed": len(rec["core_courses"]),
        "core_matched": sum(1 for x in rec["core_courses"] if join_key(x) in name_by_key),
        "module_credits_sum": modules_sum,
        "total9": total9,
        "unmatched": len(rec["unmatched_courses"]),
        "kept_courses": len(kept),
        "hard_failures": len(hard_failures),
        "quality_status": status,
        "estate8": rec["provenance"]["estate8"],
    }
    rec["_warnings"] = warns
    return rec


def main(argv=None):
    parser = argparse.ArgumentParser(description="从当前 final-md 构建结构化 plan_db")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH,
                        help="候选库输出路径（默认不覆盖运行时 plan_db.json）")
    args = parser.parse_args(argv)
    if not os.path.isdir(SRC_DIR):
        sys.exit("❌ 找不到语料目录 %s" % SRC_DIR)
    from agent.tools.query_course.scripts.final_md_plan import build
    active = json.loads(Path(ROOT, "backend/data/documents.active.json").read_text("utf-8"))
    result = build(SRC_DIR, active)
    unknown = sum(course["credits"] is None for major in result["majors"] for course in major["courses"])
    if Path(args.output).resolve() == Path(OUT_DIR, "plan_db.json").resolve() and unknown:
        raise ValueError("存在未确认学分，不能覆盖正式 plan_db；请先裁决来源异常")
    _atomic_write(args.output, result)
    print(json.dumps({"docs": len(result["majors"]),
                      "courses": sum(len(m["courses"]) for m in result["majors"]),
                      "unverified_credits": unknown, "source": "final-md/培养方案",
                      "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
