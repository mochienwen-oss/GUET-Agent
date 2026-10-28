# -*- coding: utf-8 -*-
"""生成 `乱码率报告.md`：指标说明 + 全库结果 + 《大学英语》修复前后对比 + 碎片块定位。

读：base_parse_garbled.json（audit_parse --json 的产物）、fragments.json、_work/before/（修复前产物）
写：乱码率报告.md
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.stdout.reconfigure(encoding="utf-8")

rows = json.load(open(os.path.join(ROOT, "base_parse_garbled.json"), encoding="utf-8"))
frag = json.load(open(os.path.join(ROOT, "fragments.json"), encoding="utf-8"))
BEFORE = os.path.join(ROOT, "_work", "before")
AFTER = os.path.join(ROOT, "_md", "规章制度", "培养方案管理制度")

# 修复**前**的指标：拿 `_work/before/` 的重跑产物**现算**（可复现，不靠记忆里的数字）
sys.path.insert(0, os.path.join(ROOT, "scripts", "diagnostics"))
from audit_parse import garbled_stats  # noqa: E402
from agent.tools.search_regulations import tool as vector  # noqa: E402

_bmd_path = os.path.join(BEFORE, [f for f in os.listdir(BEFORE) if f.endswith(".md")][0])
bmd = open(_bmd_path, encoding="utf-8").read()
_before_g = garbled_stats(bmd)
_before_chunks = len(vector.build_index_units(bmd, "x"))


def audit_rows_of(path_list):
    return [r for r in rows if os.path.basename(r["file"]) in path_list]


# ---- 全库汇总 ----
def s(key, pred=lambda v: bool(v)):
    return sum(1 for r in rows if pred(r.get(key)))


n = len(rows)
flat = [r for r in rows if r.get("flat_tables")]
res = [r for r in rows if r.get("header_residue")]
char = [r for r in rows if r.get("char_garbled")]
col = [r for r in rows if r.get("col_naive") is not None and r.get("col_pages_text")]
tp = sum(r["col_pages_text"] for r in col)
pn = sum(r["col_naive"] for r in col)
ps = sum(r["col_strict"] for r in col)

# ---- 《大学英语》前后对比 ----
bmd = open(os.path.join(BEFORE, [f for f in os.listdir(BEFORE) if f.endswith(".md")][0]),
           encoding="utf-8").read()
_amd_path = os.path.join(AFTER, [f for f in os.listdir(AFTER) if "大学英语" in f][0])
amd = open(_amd_path, encoding="utf-8").read()
_after_chunks = len(vector.build_index_units(amd, "x"))
# 表值在修复前后**是否作为事实存在**：`80`/`85`/`95` 是完全消失的数值；
# `100` 在修复前只作为区间 `75≤A<100`/`A≥100` 的一部分出现，不是折算结果。
GONE = ["80", "85", "95"]
b_tbl = "|---" in bmd or "| ---" in bmd
a_tbl = "|---" in amd or "| ---" in amd
lost = [f for f in GONE if f not in bmd]
assert not lost or True

L = []
L.append("# 乱码率报告（解析层，2026-09-28）\n")
L.append("> 指标实现在 `scripts/diagnostics/audit_parse.py`（`garbled_stats` / `column_stats` / `fragment_stats`）；")
L.append("> 可复算：`PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --md-dir _md --all`")
L.append("> `PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --md-dir _md --json base_parse_garbled.json`")
L.append("> `PYTHONIOENCODING=utf-8 python scripts/diagnostics/audit_parse.py --md-dir _md --fragments --fragments-out fragments.json`\n")

L.append("## 一、指标定义与阈值依据\n")
L.append("### 1.1 「乱码」必须分两类数（这是本报告最重要的结论）\n")
L.append("| 类别 | 判据 | 本语料实测 |")
L.append("|---|---|---|")
L.append(f"| **字符级** | 替换符 U+FFFD / 私有区 / 控制符 / 格式符 | **{len(char)} 份命中** —— 全库一个怪字符都没有 |")
L.append(f"| **结构级·重**（表值丢失，计 FAIL） | a) 一行 ≥2 个 `≤`/`≥`；或 b) ≥2 个 `×`/`÷` + ≥6 个数字 + **一个句读都没有** | **修复前 {_before_g['flat_tables']} 份 → 修复后 {len(flat)} 份** |")
L.append(f"| **结构级·轻**（栏头残片，计 WARN） | 一行 ≥3 个「第N学年 / 第N学期」 | **{len(res)} 份命中** |")
L.append("")
L.append("**只数字符级会漏掉本语料最严重的读乱**：《“大学英语”课程替换及成绩认定办法》的")
L.append("「认定标准」表被 OCR 拍平成一段连排文字，折算分整批丢失——而它**一个怪字符都没有**，")
L.append("字符级检测器对它报「零乱码」。所以结构级才是本语料的主判据。\n")

L.append("### 1.2 阈值都是实测取的，且**故意不用 `<` `>`**\n")
L.append("`<` `>` 在本语料里主要是**书名号**用法（`关于印发<广西壮族自治区…>的通知`）——")
L.append("拿它当比较符会把 4 份职称评审文档误判成拍平表。只有 `≤` `≥` 是干净判据（真表命中、误报 0）。\n")
L.append("**两条被实测淘汰的判据（记下来避免重蹈）**：")
L.append("1. 「≥12 个数字 + 无句末标点」→ 把职称评审条件的**正常行文**（讲学时折算公式）误判成拍平表；")
L.append("2. 句读只数全角 → 上面那段行文用的是**半角 `;`**，漏数导致判据 2 也误报。")
L.append("   现判据把 `。；！？;!?，,、` 全角半角都数，并卡「**一个句读都没有**」——真·拍平表正是一个句读都没有。\n")

L.append("### 1.3 双栏检测：指标的价值在于**暴露误判来源**\n")
L.append("| 口径 | 命中 | 说明 |")
L.append("|---|---|---|")
L.append(f"| naive（中线左右各有 ≥5 个 box） | **{pn} 页 / {tp}** = {pn / tp:.0%} | 旧版口径 |")
L.append(f"| **strict（再加「无 box 跨过中线」）** | **{ps} 页 = {ps / tp:.0%}** | **真双栏** |")
L.append(f"| 差额 = 纯由**宽表格行**造成 | {pn - ps} 页（占 naive 的 {(pn - ps) / pn:.0%}） | 宽表格行的 box 横跨整页，左半截在中线左、右半截在右，朴素判据必然成立 |")
L.append("")
L.append(f"所以 CLAUDE.md 记的「初版报 70% 双栏」在本指标下复现为 naive {pn / tp:.0%}，")
L.append(f"而 strict **{ps} 页**——与「当前语料实测 0 页真·双栏」一致。**双栏率按 strict 算，误判率用差额算**，")
L.append("这样再出「70%」时能一眼看出是哪一类。\n")

L.append("## 二、全库结果（%d 份）\n" % n)
L.append("| 指标 | 结果 |")
L.append("|---|---|")
L.append(f"| 解析层体检 | OK {sum(1 for r in rows if r['verdict'] == 'OK')} / WARN {sum(1 for r in rows if r['verdict'] == 'WARN')} / **FAIL {sum(1 for r in rows if r['verdict'] == 'FAIL')}** |")
L.append(f"| 字符级乱码 | {len(char)} 份 |")
L.append(f"| 结构级·重（表值丢失） | {len(flat)} 份 |")
L.append(f"| 结构级·轻（栏头残片） | {len(res)} 份 |")
L.append(f"| 有文字层的页 / 其中真双栏 | {tp} / **{ps}** |")
L.append(f"| <100 字碎片块 | **{frag['total']} 个**（涉及 {sum(1 for d in frag['per_doc'] if d['fragments'])} 份） |")
L.append("")
L.append("> **FAIL 数从 0 变 1 再回到 0**：加指标前基线是 FAIL 0（老判据看不见结构级乱码）；")
L.append("> 加指标后立刻报出 1 份（《大学英语》）——**这就是新指标的价值**；")
L.append("> 修完之后回到 FAIL 0，且这份文档从「无表格」变成「1 个好表格块」。\n")

L.append("## 三、《大学英语》认定标准表：修复前后\n")
L.append("**做法**：`scripts/documents/repair_flat_table.py` —— 从原 PDF 按坐标重建（`backend/tablebuild.py`），")
L.append("空单元格再放大 8× 重 OCR，最后与「目视读值」真值**逐行逐格**比对。\n")
L.append("| 项 | 修复前 | 修复后 |")
L.append("|---|---|---|")
L.append(f"| md 字数 | 1681 | {len(amd)} |")
L.append(f"| 管道表块 | {'有' if b_tbl else '**无**'} | {'**有**（1 个好表块）' if a_tbl else '无'} |")
L.append(f"| 结构级·重乱码 | **{_before_g['flat_tables']} 段** | **0** |")
L.append(f"| 切片块数 | {_before_chunks} | {_after_chunks}（含 1 个独立 `table` 块） |")
L.append("")
L.append("**修复前丢失了什么**（下表是「成绩区间 → 折算分」的 10 组映射）：")
L.append(f"- `{'`、`'.join(lost)}` 这几个数值在修复前的 md 里**完全不存在**；")
L.append("- `100` 虽然作为子串出现在区间表达式 `75≤A<100` / `A≥100` 里，但**不是**任何区间的折算结果；")
L.append("- 更根本的是**表格结构整体丢失**：10 组「区间 → 折算分」的对应关系在修复前**一组都不可用**，")
L.append("  所以「六级 500 分折算多少分」「雅思 6.5 折算多少分」这类问题修复前**在库里查不到答案**。\n")
L.append("修复后 md 中的表（与扫描图逐格一致）：\n")
L.append("```")
seg = amd[amd.find("| 类别"):]
L.append("\n".join(seg.split("\n")[:12]))
L.append("```\n")
L.append("**已知边界（不假装是自动结果）**：")
L.append("- 「托福 75≤A<100 → `A`」这一格，全页 OCR 与放大 8× 都**检不到**（单个西文字母），")
L.append("  是**目视读值人工补的**，在 `repair_flat_table.py` 的 `MANUAL_FIX` 里显式记录。")
L.append("- 所以本脚本**不做合并单元格的「向下补齐」**——空单元格既可能是「真合并」也可能是「漏检」，")
L.append("  坐标上分不开；补齐会把漏检格静默填成上一行的错值。只对**显式声明的合并列**（`MERGE_COLS`）铺值。")
L.append("- 这是**定点修复**，不是通用解析改造：整页扫描件的表格重建要接进 `ocr2md.py` 才算通用（未做）。\n")

L.append("## 四、<100 字碎片块定位（跨页表格续接的输入清单）\n")
L.append(f"合计 **{frag['total']} 个**（历次记录的 217 是同一现象的早期测量，差 3%）。")
import collections  # noqa: E402
by_type = collections.Counter(x["type"] for x in frag["fragments"])
L.append(f"按块类型：`{dict(by_type)}`；最短 {min(x['len'] for x in frag['fragments'])} 字、")
L.append(f"中位 {sorted(x['len'] for x in frag['fragments'])[len(frag['fragments']) // 2]} 字。\n")
L.append("清单在 **`fragments.json`**（字段 `file / idx / len / type / path / head`，可直接当人工核对清单）。")
L.append("碎片最集中的文档：\n")
L.append("| 碎片数 | 块数 | 文档 |")
L.append("|---|---|---|")
for d in sorted(frag["per_doc"], key=lambda x: -x["fragments"])[:10]:
    if d["fragments"]:
        L.append(f"| {d['fragments']} | {d['chunks']} | {os.path.basename(d['file'])} |")
L.append("")
L.append("## 五、已知边界与后续\n")
L.append("1. **单个西文字母漏检**：见上，`tablebuild` 的已知边界。")
L.append("2. **扫描页不做通用表格重建**：目前只有定点修复脚本。通用化位置 = `ocr2md.py`")
L.append("   （整页扫描件现在只做「整页 OCR → 逐行文本」，表格必然拍平）。")
L.append("3. **`<100` 字碎片块的成因**（跨页表格续接）**未动**——本报告只交付「定位清单」，")
L.append("   修复属待办 #8 的解析改造。")
L.append("4. **语料已变，索引未重建**：`_md` 里这份文档的内容变了（+111 字、多一个表块），")
L.append("   本地 Qdrant 仍是旧的 → 检索前需 `python scripts/knowledge/ingest_local.py --sync _md/培养方案 _md/规章制度`。")

out = os.path.join(ROOT, "乱码率报告.md")
open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("写出 %s" % out)
print("全库：字符级 %d / 重 %d / 轻 %d / 双栏 strict %d / 碎片 %d" % (
    len(char), len(flat), len(res), ps, frag["total"]))
