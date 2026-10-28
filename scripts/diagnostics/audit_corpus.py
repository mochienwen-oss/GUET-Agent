# audit_corpus.py —— 语料质检：覆盖 / 标题规范 / 重复 / 分块退化
#
# 在**入库之前**跑。数据问题在入库前发现，比入库后再翻便宜一个数量级——
# 而且一旦灌进向量库，切分和标题的毛病会伪装成「检索不准」或者「模型胡说」。
#
# 用法（在项目根目录执行）：
#   python scripts/diagnostics/audit_corpus.py                       # 默认：guet数据文件 → _md + _md_fixed
#   python scripts/diagnostics/audit_corpus.py --src guet数据文件 --md _md _md_fixed
#
# 四段体检：
#   ① 覆盖检查 —— 原始文件里哪些还没转成 md（= 内容根本没进知识库）
#   ② 标题规范 —— 康熙部首残留 / 多余空格 / 副本后缀 / 括号不一致
#   ③ 重复检测 —— 同一份文档的多个副本（会互相抢占检索槽位）
#   ④ 分块退化 —— 没有任何标题层级的文档（只能按字数硬切，块质量最差）

import argparse
import collections
import hashlib
import os
import re
import sys
import unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_EXTS = (".pdf", ".docx", ".html", ".htm", ".txt", ".md")
_RE_CHAP = re.compile(r"^\s*#{0,6}\s*第[一二三四五六七八九十百零]+[章篇部分]", re.M)
_RE_ART = re.compile(r"^\s*#{0,6}\s*第[一二三四五六七八九十百零]+条", re.M)
_RE_RADICAL = re.compile(r"[⺀-⿟]")   # 康熙部首 / CJK 部首补充
_RE_TAIL_JUNK = re.compile(r"(_\d{1,3}|[(（]\d+[)）])$")
_RE_SPACE = re.compile(r"\s")

MIN_CHARS = 200      # 少于这个字数视为转换失败
THIN_CHUNK_DOC = 0   # 章 0 且 条 0 → 无标题层级


def norm_title(t: str) -> str:
    """标题归一：NFKC + 去空格。用于比对，不用于显示。"""
    return _RE_SPACE.sub("", unicodedata.normalize("NFKC", t or ""))


def biennial_key(t: str) -> str:
    """去掉副本后缀后的 key，用来把「X.pdf」和「X(1).pdf」认成同一份。"""
    return _RE_TAIL_JUNK.sub("", norm_title(t))


def collect(root: str) -> dict[str, str]:
    """{归一化文件名stem: 相对 ROOT 的路径}

    ★ 必须返回「相对 ROOT」的路径而不是「相对 root」：
      否则后面按 stem 找文件时只能按 md_dirs 的先后顺序试探，
      会把 _md 和 _md_fixed 里同名的两个版本搞混（量到的和入库的不是同一份）。
    """
    out: dict[str, str] = {}
    for dp, _dirs, ns in os.walk(root):
        for n in ns:
            if n.lower().endswith(_EXTS):
                stem = os.path.splitext(n)[0]
                out[norm_title(stem)] = os.path.relpath(os.path.join(dp, n), ROOT)
    return out


def sec(title: str):
    print(f"\n{'=' * 88}\n{title}\n{'=' * 88}")


def main():
    p = argparse.ArgumentParser(description="语料质检（入库前跑）")
    p.add_argument("--src", default=os.path.join(ROOT, "guet数据文件"), help="原始文件目录")
    p.add_argument("--md", nargs="+", default=["_md", "_md_fixed"], help="已转换的 md 目录")
    p.add_argument("--sim", type=float, default=0.90, help="标题相似度阈值（判重复用）")
    args = p.parse_args()

    src = collect(args.src)
    mds: dict[str, str] = {}
    md_dirs = []
    for d in args.md:
        full = os.path.join(ROOT, d)
        if os.path.isdir(full):
            md_dirs.append(d)
            mds.update(collect(full))

    print(f"原始文件 {len(src)} 个（{args.src}）")
    print(f"已转换 md {len(mds)} 个（{'、'.join(md_dirs) or '无'}）")

    # ---------------------------------------------------------------- ① 覆盖
    sec("① 覆盖检查：原始文件里哪些还没进知识库")
    missing = []
    for k, rel in sorted(src.items()):
        if any(k and (k in m or m in k) for m in mds):
            continue
        # 显示时相对 --src，读起来才是「规章制度/人事制度/xxx.pdf」
        missing.append(os.path.relpath(os.path.join(ROOT, rel), args.src))
    if not missing:
        print("✅ 全部已覆盖")
    else:
        by_dir = collections.Counter(os.path.dirname(r).split(os.sep)[0] or "(根)" for r in missing)
        print(f"❌ {len(missing)} 个原始文件还没有对应的 md：")
        for d, c in by_dir.most_common():
            print(f"     {d}: {c} 个")
        print()
        for r in missing[:60]:
            print(f"     · {r}")
        if len(missing) > 60:
            print(f"     … 还有 {len(missing) - 60} 个")

    # ---------------------------------------------------------------- ② 标题规范
    sec("② 标题规范")
    problems = collections.defaultdict(list)
    for k, rel in sorted(mds.items()):
        raw = os.path.basename(rel)
        stem = os.path.splitext(raw)[0]
        if _RE_RADICAL.search(stem):
            problems["康熙部首残留（NFKC 能修）"].append(stem)
        if _RE_SPACE.search(stem):
            problems["标题里有空格"].append(stem)
        if _RE_TAIL_JUNK.search(stem):
            problems["副本后缀（_01 / (1)）"].append(stem)
        if "（" in stem and "(" in stem:
            problems["全角半角括号混用"].append(stem)
    if not problems:
        print("✅ 标题都规范")
    else:
        for name, items in problems.items():
            print(f"\n⚠️  {name}：{len(items)} 个")
            for s in items[:12]:
                print(f"      {s[:76]}")
            if len(items) > 12:
                print(f"      … 还有 {len(items) - 12} 个")

    # ---------------------------------------------------------------- ③ 重复
    sec("③ 重复检测：内容重复的文档（会互相抢占检索槽位）")

    def md_path(rel: str) -> str | None:
        """rel 已经是相对 ROOT 的路径，直接用。"""
        p = os.path.join(ROOT, rel)
        return p if os.path.exists(p) else None

    # 同名副本（X.md 与 X(1).md 这种下载副本）
    groups = collections.defaultdict(list)
    for k, rel in mds.items():
        groups[biennial_key(k)].append(rel)
    name_dups = {k: v for k, v in groups.items() if len(v) > 1}

    # 内容指纹判重。
    # ⚠️ 不要按标题相似度判重：培养方案的标题都是「2025级XX专业培养方案(主修)」，
    #    两两标题相似度天然 >90%，按标题比全是误报。
    fingerprint = collections.defaultdict(list)
    for _k, rel in mds.items():
        p = md_path(rel)
        if not p:
            continue
        with open(p, encoding="utf-8", errors="ignore") as f:
            fingerprint[hashlib.sha1(f.read().strip().encode("utf-8")).hexdigest()].append(rel)
    content_dups = {h: v for h, v in fingerprint.items() if len(v) > 1}

    if not name_dups and not content_dups:
        print("✅ 没发现重复")
    for k, v in name_dups.items():
        same = "（正文完全相同，可直接删一份）" if any(
            len(set(content_dups.get(h, [])) & set(v)) > 1 for h in content_dups) else ""
        print(f"\n⚠️  同名副本 {len(v)} 份{same}：")
        for r in v:
            print(f"      {r}")
    for h, v in content_dups.items():
        if any(len(set(v) & set(g)) == len(v) for g in name_dups.values()):
            continue   # 已在上面报过
        print(f"\n⚠️  正文完全相同 {len(v)} 份（标题不同，建议只留一份）：")
        for r in v:
            print(f"      {r}")

    # ---------------------------------------------------------------- ④ 分块体检
    sec("④ 分块体检：直接调 vector._build_index_units（与入库时完全同一条路径）")
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    # 不设 EMBED_MODEL：本段只读配置、不加载模型，模型由 backend/config.py 决定。
    try:
        from agent.tools.search_regulations import tool as vector  # noqa: E402
    except Exception as e:
        print(f"⚠️  无法导入 backend.vector（{type(e).__name__}: {e}），跳过本段")
        return

    # 说明：不要自己写正则数「第X章」——培养方案用的是「## 一、专业简介」，
    # 规章制度用的是「## 第一章」，两套编号并存。只有走真实切分函数才数得准。
    from core.config import settings  # noqa: E402

    rows, empty = [], []
    for _k, rel in sorted(mds.items()):
        path = md_path(rel)
        if not path:
            continue
        t = open(path, encoding="utf-8", errors="ignore").read()
        if len(t.strip()) < MIN_CHARS:
            empty.append((len(t.strip()), rel))
        try:
            units = vector.build_index_units(t)
        except Exception as e:
            print(f"❌ 切分失败 {rel}: {type(e).__name__}: {e}")
            continue
        lens = [len(u["text"]) for u in units]
        rows.append({
            "rel": rel, "chars": len(t),
            "blocks": len(units),
            "avg": (sum(lens) // len(lens)) if lens else 0,
            "inband": sum(1 for n in lens
                          if settings.chunk_min_len <= n <= settings.chunk_max_len),
            "pathd": sum(1 for u in units if u["heading_path"]),
        })

    if not rows:
        print("没有可分析的 md")
        return

    tot_c = sum(r["chars"] for r in rows)
    tot_b = sum(r["blocks"] for r in rows)
    tot_i = sum(r["inband"] for r in rows)
    print(f"{len(rows)} 个 md　共 {tot_c:,} 字　→　{tot_b:,} 个块")
    print(f"  平均每文档：{tot_c // len(rows):,} 字、{tot_b / len(rows):.1f} 个块、"
          f"平均块长 {tot_c // max(tot_b, 1)} 字")
    print(f"  ★ 块长落在 [{settings.chunk_min_len}, {settings.chunk_max_len}] 的比例："
          f"{tot_i / max(tot_b, 1):.0%}（越低说明切分越退化）")
    # 块数 ≤ 2 = 整篇几乎没切开，检索粒度最粗
    flat = [r for r in rows if r["blocks"] <= 2]
    if flat:
        print(f"\n⚠️  块数 ≤ 2（整篇几乎没切开）的文档：{len(flat)} 个")
        for r in sorted(flat, key=lambda r: -r["chars"])[:25]:
            print(f"      {r['chars']:7,} 字　块{r['blocks']:>3}　{r['rel'][:56]}")
        if len(flat) > 25:
            print(f"      … 还有 {len(flat) - 25} 个")

    # 块长远低于下限 = 切得过碎，条款原子性多半没保住
    coarse = [r for r in rows if r["blocks"] > 2 and r["avg"] < settings.chunk_min_len]
    if coarse:
        print(f"\n⚠️  平均块长 < {settings.chunk_min_len} 字（切得过碎）：{len(coarse)} 个")
        for r in sorted(coarse, key=lambda r: r["avg"])[:12]:
            print(f"      平均{r['avg']:>4} 字　块{r['blocks']:>3}　{r['rel'][:56]}")

    if empty:
        print(f"\n⚠️  几乎为空的 md（<{MIN_CHARS} 字，转换失败或扫描件）：{len(empty)} 个")
        for n, rel in empty:
            print(f"      {n:5d} 字  {rel[:70]}")


if __name__ == "__main__":
    main()
