# preview_chunks.py —— 打印文档的分块结构，供人工校验（**不碰向量库、不加载模型**）
#
# `vector._build_index_units` 是纯函数：只做文本切分，不连 Qdrant、不加载 embedding。
# 所以这个工具可以直接跑，不需要起任何服务——正好用来验「切分效果」。
#
# 用法（在项目根目录执行）：
#   python scripts/diagnostics/preview_chunks.py <md 文件>                 # 概览：父子块数量 + 每块长度
#   python scripts/diagnostics/preview_chunks.py <md 文件> --tables        # 只看表格块，检查是否都带表头
#   python scripts/diagnostics/preview_chunks.py <md 文件> --full          # 打印每块内容（会很长）
#   python scripts/diagnostics/preview_chunks.py <目录>                    # 批量概览
#
# 人工校验时重点看：
#   ① 表格块是否**都带表头**（第一行是列名、第二行是 | --- |）
#   ② 块边界是否落在**句子/行边界**上，而不是被拦腰切断
#   ③ 有没有「零信息」块（单个 | 、或只剩标点）

import argparse
import collections
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
# 不设 EMBED_MODEL：本工具只读配置、不加载 embedding，模型由 backend/config.py 决定。
# （原先这里 setdefault 成 bge-large-zh-v1.5，换模型后会**覆盖** config 的新默认值——

from agent.tools.search_regulations import tool as vector  # noqa: E402


def is_table_block(s: str) -> bool:
    lines = s.lstrip().split("\n")
    return len(lines) >= 2 and lines[0].startswith("|") and vector.is_table_sep(lines[1])


def headless_table(s: str) -> bool:
    """看着像表格块，但第二行不是分隔行 → 缺表头（孤儿碎片）。"""
    return s.lstrip().startswith("|") and not is_table_block(s)


def report(path: str, args):
    text = open(path, encoding="utf-8", errors="ignore").read()
    units = vector.build_index_units(text)
    chunks = [u["text"] for u in units]
    from core.config import settings

    print(f"\n{'=' * 92}")
    print(f"{os.path.relpath(path, ROOT)}")
    print(f"  {len(text):,} 字　→　{len(chunks)} 个块")

    tbl = [s for s in chunks if s.lstrip().startswith("|")]
    ok_tbl = [s for s in tbl if is_table_block(s)]
    print(f"  表格块 {len(tbl)}，其中带表头 {len(ok_tbl)}"
          + (f"　⚠️ 缺表头 {len(tbl) - len(ok_tbl)}" if len(tbl) - len(ok_tbl) else "　✅"))

    junk = [s for s in chunks if vector.is_meaningless(s)]
    print(f"  零信息块 {len(junk)}" + ("　✅" if not junk else "　⚠️"))

    lens = sorted(len(s) for s in chunks)
    if lens:
        lo, hi = settings.chunk_min_len, settings.chunk_max_len
        inband = sum(1 for n in lens if lo <= n <= hi)
        # ⚠️ 只看整体带内比例会**误判**：完整条款块（一条规定只有 100 多字）和表格碎片
        #   是**刻意**留在带外的，它们豁免尺寸规则。所以同时报一个「排除豁免后」的比例——
        #   那个才反映切分本身有没有退化。
        exempt = {id(s) for s in chunks
                  if vector.is_complete_article(s) or s.lstrip().startswith("|")}
        rest = [n for s, n in zip(chunks, (len(x) for x in chunks)) if id(s) not in exempt]
        inband2 = sum(1 for n in rest if lo <= n <= hi)
        print(f"  块长 min/中位/max = {lens[0]}/{lens[len(lens) // 2]}/{lens[-1]}"
              f"　│　带内 {inband}/{len(lens)} = {inband / len(lens):.0%}"
              + (f"　│　**排除条款/表格豁免后** {inband2}/{len(rest)} = {inband2 / len(rest):.0%}"
                 if rest else "　│　（全部是豁免块）"))

    # ★ 结构感知切片的三项验收（都是「不达标就说明切分退化」的信号）
    art = sum(1 for s in chunks if vector.is_complete_article(s))
    pathd = sum(1 for u in units if u["heading_path"])
    types = collections.Counter(u["block_type"] for u in units)
    print(f"  完整条款块 {art}　│　带 heading_path {pathd}/{len(units)}"
          f"　│　block_type {dict(types)}")

    shown = ok_tbl if args.tables else chunks
    if args.tables and not tbl:
        print("  （这份文档没有表格块）")
        return
    limit = len(shown) if args.full else min(12, len(shown))
    for i, s in enumerate(shown[:limit], 1):
        tag = "表格" if is_table_block(s) else ("孤儿" if headless_table(s) else "正文")
        head = s.replace("\n", " ⏎ ")
        print(f"    [{i:>3}] {tag} {len(s):>5} 字  {head[:88]}")
    if len(shown) > limit:
        print(f"    … 还有 {len(shown) - limit} 块（--full 看全部）")


def main():
    p = argparse.ArgumentParser(description="打印分块结构供人工校验（不碰向量库）")
    p.add_argument("path", help="md 文件或目录")
    p.add_argument("--full", action="store_true", help="打印全部块（默认每份只列 12 块）")
    p.add_argument("--tables", action="store_true", help="只看表格块")
    args = p.parse_args()

    target = args.path if os.path.isabs(args.path) else os.path.join(ROOT, args.path)
    if os.path.isfile(target):
        report(target, args)
        return

    files = []
    for dp, _d, ns in os.walk(target):
        files.extend(os.path.join(dp, n) for n in sorted(ns) if n.lower().endswith(".md"))
    if not files:
        print(f"❌ {target} 下没有 md 文件")
        return
    print(f"共 {len(files)} 个 md")
    for f in files:
        report(f, args)


if __name__ == "__main__":
    main()
