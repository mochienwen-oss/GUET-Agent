# -*- coding: utf-8 -*-
"""路由模板登记的**候选生成器**（灌库 SOP 的一环）。

新文档入库后，不能靠人凭空想「这文档该配哪族、用什么词」——那正是本仓库
反复踩的坑（关键字定位 9/14 找错款）。所以流程固定为：

    文档入库 → 本脚本按**标题**生成候选 → **人核**（读原文/读制度）→ 落盘
    `backend/data/route_templates.json`

本脚本**只建候选队列，不写结论**（铁律 8）：
  · 它给不出「这文档属于哪族」的正确答案 —— 族是人按业务划的；
  · 它给的是**词面线索**：标题里的候选专名、与既有族的词面重合度、建议的模板写法。

用法（项目根目录）：
  python scripts/route_registry.py --suggest              # 扫语料标题，出候选
  python scripts/route_registry.py --check                # 自检：既有登记表里
                                                          # 有没有指向不存在的文档/空模板
"""
import argparse
import collections
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

REG = os.path.join(ROOT, "backend", "data", "route_templates.json")

# 公文名里可剥掉的通用词（剥完剩下的就是**特征名**，专名的候选）
_GENERIC = ["桂林电子科技大学", "本科生", "普通高等教育", "全日制", "关于", "印发",
            "的通知", "办法", "规定", "细则", "条例", "章程", "方案", "规范",
            "（试行）", "(试行)", "（修订）", "(修订)", "暂行", "实施"]


def titles_from_corpus():
    """语料文档标题：优先从 `_md/` 文件名取（离线可跑），不依赖 Qdrant。"""
    md = os.path.join(ROOT, "_md")
    out = []
    for dp, _, names in os.walk(md):
        for n in sorted(names):
            if n.endswith(".md"):
                out.append(n[:-3])
    return out


def feature_name(t: str) -> str:
    s = t
    for g in _GENERIC:
        s = s.replace(g, "")
    return re.sub(r"[\s《》〔〕\[\]()（）\d号]+", "", s).strip()


def suggest():
    reg = json.load(open(REG, encoding="utf-8")) if os.path.isfile(REG) else {}
    fams = reg.get("families") or {}
    known = [(n, spec) for n, spec in fams.items()]
    titles = titles_from_corpus()
    print("语料文档 %d 份；既有登记族 %d 个：%s\n"
          % (len(titles), len(known), "、".join(n for n, _ in known)))
    print("=== 候选队列（**人核后**才能落盘）===")
    print("说明：'词面重合' 只提示「这标题里出现了既有族的专名」，**不等于它属于该族**；")
    print("      族归属要人按业务判；新族要人另起。\n")
    rows = []
    for t in titles:
        f = feature_name(t)
        hits = []
        for n, spec in known:
            ks = list(spec.get("强专名", [])) + list(spec.get("弱专名", []))
            h = [k for k in ks if k in t]
            if h:
                hits.append("%s:%s" % (n, "、".join(h[:3])))
        rows.append((f, t, " | ".join(hits) or "—"))
    # 有词面重合的排前面（最可能是既有族的补充），其余按标题序
    rows.sort(key=lambda r: (r[2] == "—", r[1]))
    for f, t, h in rows:
        print("  特征名 %-24s | 词面重合 %s" % (f[:24] or "(剥空了)", h))
        print("      原标题: %s" % t[:60])
    print("\n落盘格式见 backend/data/route_templates.json 的 _口径；"
          "新族请同时写 强专名 / 弱专名 / templates。")


def _corpus_body() -> str:
    """语料**正文**。★ 必须扫正文不是文件名 ——
    「四级」「试读期」「拟接收名单」是**条内专名**（只在正文里），
    拿文件名查会把它们全判成「不是语料措辞」（实测踩过）。
    """
    md = os.path.join(ROOT, "_md")
    parts = []
    for dp, _, names in os.walk(md):
        for n in names:
            if n.endswith(".md"):
                try:
                    parts.append(open(os.path.join(dp, n), encoding="utf-8",
                                      errors="ignore").read())
                except Exception:
                    pass
    return "\n".join(parts)


def check():
    if not os.path.isfile(REG):
        sys.exit("❌ 找不到 %s" % REG)
    reg = json.load(open(REG, encoding="utf-8"))
    fams = reg.get("families") or {}
    body = _corpus_body()
    print("登记族 %d 个；语料正文 %d 字符\n" % (len(fams), len(body)))
    hard = 0
    for n, spec in fams.items():
        strong, weak = spec.get("强专名") or [], spec.get("弱专名") or []
        tpls = spec.get("templates") or []
        issues = []
        if not strong:
            issues.append("**没有强专名** → 该族永远定不到（弱专名必须依附强专名）")
        if not tpls:
            issues.append("**没有 templates** → 路由到该族也召不到东西")
        in_body = [k for k in strong if k in body]
        not_in = [k for k in strong if k not in body]
        notes = spec.get("专名标注") or {}
        if issues:
            hard += 1
            print("  ✗ %s" % n)
            for i in issues:
                print("       - %s" % i)
            continue
        print("  %s %s（强 %d / 弱 %d / 模板 %d）" % ("✓" if in_body else "⚠", n,
                                                    len(strong), len(weak), len(tpls)))
        print("       语料正文命中(%d): %s" % (len(in_body), "、".join(in_body) or "—"))
        if not_in:
            print("       ⚠ 未在正文出现(%d): %s" % (len(not_in), "、".join(not_in)))
            for k in not_in:
                print("           · %s → %s" % (k, notes.get(k, "**无标注**（请补：口语 or 错词）")))
    print("\n结论：%d 个族有**结构性**问题（缺强专名/缺模板）；"
          "「未在正文出现」只是提示，需人判断是口语还是错词。" % hard)
    return 1 if hard else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(check())
    suggest()