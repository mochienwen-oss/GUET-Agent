# -*- coding: utf-8 -*-
"""索引完整性审计：**每份 md 的期望块数 vs Qdrant 里实际点数**，逐份对账。

为什么需要它 —— 入库是「先按 document_id 删旧向量、再写新向量」两步，中间被杀掉/
断掉，那份文档就会**少一批点且不报错**；而 `--sync` 的对账只查「source 在不在」，
查不出「点数对不对」。检索评测出问题时，得先排掉「索引本身是残的」这一层。

同时反向查**幽灵条目**：向量库里有、磁盘上没有的 source（改过文件名/删过文件会留下）。

用法：
    PYTHONIOENCODING=utf-8 python scripts/audit_index.py            # 全量对账
    PYTHONIOENCODING=utf-8 python scripts/audit_index.py --brief    # 只打汇总
退出码：不一致 → 1（与 audit_parse.py 同风格，可挂进流水线）
"""
import argparse
import collections
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.stdout.reconfigure(encoding="utf-8")

DIRS = [os.path.join(ROOT, "_md", "培养方案"), os.path.join(ROOT, "_md", "规章制度")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", action="store_true")
    args = ap.parse_args()

    from qdrant_client import QdrantClient
    from agent.tools.search_regulations import tool as vector
    from core.config import settings

    vector.set_client(QdrantClient(path=os.path.join(ROOT, "qdrant_local")))
    col = settings.qdrant_collection

    # ---- 磁盘侧：期望块数 ----
    want = {}
    for d in DIRS:
        for dp, _dd, ns in os.walk(d):
            for n in sorted(ns):
                if not n.lower().endswith(".md"):
                    continue
                f = os.path.join(dp, n)
                title = os.path.splitext(n)[0]
                text = open(f, encoding="utf-8").read()
                want[zlib.crc32(title.encode("utf-8")) & 0x7FFFFFFF] = (
                    title, len(vector.build_index_units(text, "")))

    # ---- 向量库侧：实际点数（scroll 全量，按 document_id 归并）----
    got = collections.Counter()
    src_of, offset = {}, None
    while True:
        pts, offset = vector.client().scroll(
            collection_name=col, limit=512, offset=offset,
            with_payload=["document_id", "source"], with_vectors=False)
        for p in pts:
            pl = p.payload or {}
            got[pl.get("document_id")] += 1
            src_of.setdefault(pl.get("document_id"), pl.get("source") or "")
        if offset is None:
            break

    n_pt = sum(got.values())
    print("磁盘 %d 份 md / 向量库 %d 个点（%d 个 document_id）" % (len(want), n_pt, len(got)))

    missing = [want[k][0] for k in want if k not in got]
    short = [(want[k][0], want[k][1], got[k]) for k in want if k in got and got[k] != want[k][1]]
    ghost = [(k, src_of.get(k, ""), got[k]) for k in got if k not in want]

    if not args.brief:
        for t in missing:
            print("  ❌ 整份缺失：%s" % t)
        for t, w, g in short:
            print("  ⚠️ 点数不符：%s 期望 %d 实际 %d（差 %+d）" % (t, w, g, g - w))
        for k, s, g in ghost:
            print("  👻 幽灵条目：document_id=%s source=%r 共 %d 点" % (k, s, g))

    ok = not (missing or short or ghost)
    print("%s 期望块数合计 %d / 实际点数 %d；缺失 %d 份 · 点数不符 %d 份 · 幽灵 %d 条"
          % ("✅ 一致" if ok else "❌ 不一致",
             sum(v[1] for v in want.values()), n_pt, len(missing), len(short), len(ghost)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
