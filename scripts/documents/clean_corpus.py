# -*- coding: utf-8 -*-
"""clean_corpus.py —— 语料清洗：把「只稀释检索、答不出题」的噪音去掉，不改写正文。

## 为什么要按目录分派适配器

培养方案和规章制度的噪音**不是一种东西**：

- 培养方案：PDF 里有**图**（八、课程先修后修关系图）。解析器把图内文字 OCR 成一行
  连排文字 —— 有的包进 `<!-- 图片OCR: … -->`（48/63 份），有的裸放（导航工程那种）。
  同一学期的课在那行里交错（`大学英语1大学英语3大学英语2大学英语4`），
  **先修→后修的边不可恢复**；而开课学期在「十、教学进程计划表」里本来就有。
  实测（2026-10-11）：63/63 份都有这一节，0 份是表格，它会**被检索到并排第 1**，
  稀释真正有用的块。
- 规章制度：**红头文件格式** —— 版头「XX文件」、主送机关「各单位、各部门:」、
  版记「X办公室 …印发」，外加网页打印残留「【关闭】…信息公开网」。

把两套规则堆在一个正则表里会互相打架（「各单位、各部门:」只在公文里有意义），
所以按目录分派。

## 分寸：删噪音，不删信息

- **删**：版头、主送机关（只删「各单位、各部门:」这几个字，**留下文号和日期**）、
  版记、网页残留、HTML 注释块、竖排表头拍平行、整个先修后修关系图节。
- **留**：通知正文、落款发文日期、文号、所有条文体。

## 门

    python scripts/diagnostics/audit_parse.py --md-dir _md_clean

FAIL/WARN 不得劣于 `_md` 的基线（`_work/audit/parse_baseline.json`）。
另有一条本模块自己的断言：`_md_clean` 里「课程先修后修关系图」残留必须为 0。

用法（项目根目录）：
    PYTHONIOENCODING=utf-8 python scripts/documents/clean_corpus.py            # 只看报告，不落盘
    PYTHONIOENCODING=utf-8 python scripts/documents/clean_corpus.py --apply    # 落盘到 _md_clean/
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------- 通用

# HTML 注释块。★ 图片OCR 那一类整块是**图的 OCR 文本**（连排、无结构），
# 留在库里只会被检索到并占位。连注释标记一起删。
RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# 网页打印残留。mdify._NOISE_RES 里有 `^【?关闭】?$`，但**带后缀的那种躲过去了**
# （实测「【关闭】桂林电子科技大学信息公开网」），所以这里放宽到「以【关闭】开头」。
RE_WEB_RESIDUE = [
    re.compile(r"^\s*【?关闭】?.*$"),
    re.compile(r"^\s*桂林电子科技大学信息公开网\s*$"),
]

RE_BLANK_RUN = re.compile(r"\n{3,}")


def _drop_lines(text: str, pats: list) -> tuple[str, int]:
    """整行命中任一正则就删。返回 (新文本, 删掉行数)。"""
    out, n = [], 0
    for line in text.split("\n"):
        if any(p.search(line) for p in pats):
            n += 1
            continue
        out.append(line)
    return "\n".join(out), n


def clean_generic(text: str) -> tuple[str, dict]:
    hits = {}
    text, n = RE_HTML_COMMENT.subn("", text)
    hits["html_comment"] = n
    text, n = _drop_lines(text, RE_WEB_RESIDUE)
    hits["web_residue"] = n
    text = RE_BLANK_RUN.sub("\n\n", text).strip() + "\n"
    return text, hits


# ---------------------------------------------------------------- 培养方案

# 整节删除的目标：八、课程先修后修关系图。
# 删到**下一个一级章节标题**为止（`## 九、…` 那种）。
RE_PLAN_SECTION = re.compile(
    r"^#{0,6}[ \t]*[一二三四五六七八九十]+、[ \t]*课程先修后修关系图[^\n]*\n"
    r"(?:(?!#{0,6}[ \t]*[一二三四五六七八九十]+、)[\s\S])*",
    re.M,
)

# 竖排表头被拍平成一行：把「课程模块/代码/名称/类别/性质/学分/总学时/理论/实验/实践/
# 开课学期/是否必修/开课部门」这些**栏头词**按阅读顺序粘成的一行，紧挨着真正的
# markdown 表头（`| 课程模块 | … |`）。判据：无 `|`、长度 ≥ 12、且 90% 以上的字
# 落在栏头字的集合里。
_HEADER_CHARS = set("课程模块代码名称类别性质学分总学时理论实验实践开课期是否必修部门"
                    "序号课组要求模块数备注安排")


def _is_stacked_header(line: str) -> bool:
    s = line.strip()
    if len(s) < 12 or "|" in s:
        return False
    hit = sum(1 for c in s if c in _HEADER_CHARS)
    return hit / len(s) >= 0.9


def clean_plan(text: str) -> tuple[str, dict]:
    hits = {}
    text, n = RE_PLAN_SECTION.subn("", text)
    hits["prereq_graph_section"] = n
    out, n = [], 0
    for line in text.split("\n"):
        if _is_stacked_header(line):
            n += 1
            continue
        out.append(line)
    hits["stacked_header"] = n
    return "\n".join(out), hits


# ---------------------------------------------------------------- 规章制度

# 版头（红头）。整行是「X文件」这种字头，正文里绝不会独立成行。
RE_LETTERHEAD = re.compile(r"^#{1,6}[ \t]*桂林电子科技大学文件[ \t]*$")

# 主送机关。**只删「各单位、各部门:」这几个字**，留下同一行的文号/日期 ——
# 实测两种写法：
#   桂电教〔2021〕32 号各单位、各部门:      → 桂电教〔2021〕32 号
#   2023年05月25日各单位、各部门:           → 2023年05月25日
RE_ADDRESSEE = re.compile(r"各单位、各部门[:：]|[各全]单位[:：]$")

# 版记：X办公室  + 日期 + 印发。纯印刷信息。
# 实测「桂林电子科技大学校长办公室                2021 年9 月22 日印发」
RE_IMPRINT = re.compile(
    r"^\s*\S{0,12}办公室\s+\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*印发\s*$"
)


def clean_reg(text: str) -> tuple[str, dict]:
    hits = {"letterhead": 0, "addressee": 0, "imprint": 0}
    out = []
    for line in text.split("\n"):
        if RE_LETTERHEAD.search(line):
            hits["letterhead"] += 1
            continue
        if RE_IMPRINT.search(line):
            hits["imprint"] += 1
            continue
        if RE_ADDRESSEE.search(line):
            new = RE_ADDRESSEE.sub("", line).rstrip()
            hits["addressee"] += 1
            if not new.strip():
                continue
            out.append(new)
            continue
        out.append(line)
    return "\n".join(out), hits


# ---------------------------------------------------------------- 主流程

# 目录名 → 适配器。不在表里的目录只过通用清洗。
ADAPTERS = {
    "培养方案": clean_plan,
    "规章制度": clean_reg,
}


def clean_one(text: str, top_dir: str) -> tuple[str, dict]:
    text, g = clean_generic(text)
    fn = ADAPTERS.get(top_dir)
    s = {}
    if fn:
        text, s = fn(text)
    text = RE_BLANK_RUN.sub("\n\n", text).strip() + "\n"
    return text, {**g, **s}


def main() -> int:
    p = argparse.ArgumentParser(description="语料清洗（不改写正文，只去噪音）")
    p.add_argument("--src", default=os.path.join(ROOT, "_md"))
    p.add_argument("--out", default=os.path.join(ROOT, "_md_clean"))
    p.add_argument("--apply", action="store_true", help="落盘（默认只出报告）")
    p.add_argument("--report", default=os.path.join(ROOT, "_work", "audit", "clean_report.json"))
    args = p.parse_args()

    files = []
    for dirpath, _, names in os.walk(args.src):
        for n in sorted(names):
            if n.lower().endswith(".md"):
                files.append(os.path.join(dirpath, n))

    per_doc, totals = [], {}
    left_graph = []
    for f in files:
        rel = os.path.relpath(f, args.src)
        top = rel.split(os.sep)[0]
        raw = open(f, encoding="utf-8", errors="ignore").read()
        new, hits = clean_one(raw, top)
        per_doc.append({"file": rel, "before": len(raw), "after": len(new), "hits": hits})
        for k, v in hits.items():
            totals[k] = totals.get(k, 0) + v
        if "课程先修后修关系图" in new:
            left_graph.append(rel)
        if args.apply:
            dst = os.path.join(args.out, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "w", encoding="utf-8").write(new)

    print("扫描 %d 份 → %s" % (len(files), os.path.relpath(args.out, ROOT)))
    print("按类型命中：")
    for k, v in sorted(totals.items(), key=lambda x: -x[1]):
        print("  %-24s %d" % (k, v))
    cut = sum(d["before"] - d["after"] for d in per_doc)
    print("净减 %d 字（%.2f%%）" % (cut, 100.0 * cut / max(1, sum(d["before"] for d in per_doc))))
    print("★ 先修后修关系图残留：%d 份%s" % (
        len(left_graph), "（应为 0）" if left_graph else " ✅"))
    for r in left_graph[:10]:
        print("    ! %s" % r)
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump({"files": per_doc, "totals": totals, "prereq_graph_left": left_graph},
              open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("报告 → %s" % os.path.relpath(args.report, ROOT))
    return 1 if left_graph else 0


if __name__ == "__main__":
    sys.exit(main())
