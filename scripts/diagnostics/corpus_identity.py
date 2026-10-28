# -*- coding: utf-8 -*-
"""**语料身份 = 四个值**，一条命令算完，并把冻结命令行**直接打出来**。

为什么要有这个脚本：`index_payload_sha256` 只能靠查活索引得到，手抄 64 位哈希是
这套身份模型里唯一还会出错的地方。所以它不只是「打印」，还把**同源关系**当场证一遍：

```text
_md（91 份）                                    → source_tree_sha256
  └ 默认切分器（未过滤）                        → source_chunked_sha256
      └ plan_ingest R1–R6 过滤 → 786 块进索引   → index_payload_sha256
```

★ 最后那条断言（本地 `plan_ingest` 的保留块数 == 索引实际点数）就是这套身份的**中心主张**：
  「索引是默认切分的过滤子集，不是另一套切分」。它曾经被「两份有序列表等长比较」这种
  错误判据读成「文件与索引不同源」——所以这里把正确的判据固化成一行检查。

★ 只读：不建索引、不写文件、不动 collection / snapshot / BM25 / `_md`。

Run:
  QDRANT_URL=http://127.0.0.1:6333 QDRANT_COLLECTION=school_docs_phase5 \
  python scripts/diagnostics/corpus_identity.py            # 需要活索引
  python scripts/diagnostics/corpus_identity.py --no-index # 只算文件侧两个值
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _extra in (REPO / "backend", REPO / "训练集"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from agent.tools.search_regulations.scripts import build_collection as BC  # noqa: E402
from agent.tools.search_regulations.scripts import corpus_fingerprint as CF  # noqa: E402


def index_identity(collection: str | None = None) -> dict:
    """从**活索引**现算身份 —— 薄封装 `diagnostics._corpus_snapshot()`，**不另立口径**。

    ★ 刻意不在 `corpus_fingerprint` 里实现：那个模块的契约是「纯函数、不连 Qdrant」，
      加一个要起服务的函数会把它自己的说明变成假的。
    """
    from agent.tools.search_regulations.scripts import diagnostics

    _docs, _texts, _chunks, meta = diagnostics._corpus_snapshot()
    if collection is not None and meta.get("collection") != collection:
        raise SystemExit("活索引的 collection 是 %r，期望 %r —— 先查环境变量 QDRANT_COLLECTION"
                         % (meta.get("collection"), collection))
    return meta


def measure(md_root, *, collection=None, with_index=True) -> dict:
    fp = CF.fingerprint(md_root)
    out = {"collection": collection, "points": None,
           "source_tree_sha256": fp["tree_sha256"],
           "source_chunked_sha256": fp["chunked_sha256"],
           "source_files": fp["files"],
           "index_payload_sha256": None,
           "index_chunking_profile": BC.INDEX_CHUNKING_PROFILE,
           "lineage": None}
    if not with_index:
        return out
    meta = index_identity(collection=collection)
    kept = BC.plan_ingest(md_root)["kept"]
    out.update({"collection": meta["collection"], "points": meta["point_count"],
                "index_payload_sha256": meta["sha256"],
                "index_documents": meta["document_count"],
                "lineage": {"kept_blocks": len(kept),
                            "index_points": meta["point_count"],
                            "same_source": len(kept) == meta["point_count"]}})
    return out


def build_parser():
    parser = argparse.ArgumentParser(description="算语料身份四值并打印冻结参数")
    parser.add_argument("--md-root", default=str(REPO / "_md"),
                        help="语料根目录（默认 _md）")
    parser.add_argument("--collection", default="school_docs_phase5",
                        help="期望的集合名；与活索引不符即报错")
    parser.add_argument("--no-index", action="store_true",
                        help="只算文件侧两个值（不连 Qdrant）")
    return parser


def main(argv=None) -> dict:
    args = build_parser().parse_args(argv)
    got = measure(args.md_root, collection=args.collection,
                  with_index=not args.no_index)
    print(json.dumps(got, ensure_ascii=False, indent=1))
    if got["lineage"] is not None:
        mark = "✓" if got["lineage"]["same_source"] else "✗"
        print("\n[同源检查] 本地 plan_ingest 保留块 %d == 索引点数 %d  %s"
              % (got["lineage"]["kept_blocks"], got["lineage"]["index_points"], mark))
        if not got["lineage"]["same_source"]:
            # ★ 不同源是**停止条件**：不许把一份对不上的身份写进冻结产物。
            raise SystemExit(
                "语料与索引不同源 —— 拒绝出身份。可能：索引被重建过、语料被改过，"
                "或切分器/过滤规则变了。请先查清再冻结。")
    print("\n" + "-" * 68)
    print("可直接粘用的冻结参数：\n")
    for flag, key in (("--corpus-collection", "collection"),
                      ("--corpus-points", "points"),
                      ("--corpus-source-tree-sha256", "source_tree_sha256"),
                      ("--corpus-source-chunked-sha256", "source_chunked_sha256"),
                      ("--corpus-index-payload-sha256", "index_payload_sha256"),
                      ("--corpus-index-chunking-profile", "index_chunking_profile")):
        value = got.get(key)
        print("  %-34s %s" % (flag, "—" if value is None else value))
    return got


if __name__ == "__main__":
    main()