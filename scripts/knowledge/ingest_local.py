# ingest_local.py —— 本地把 md 写入 Qdrant（绕开 MySQL）
#
# 用于本地快速验证检索效果：rag_test.py 检索时不读 MySQL，只需要 Qdrant 里有向量。
# 用 title 的 crc32 作为稳定 document_id，重复入库会覆盖同名文档。
#
# 前置：pip install sentence-transformers qdrant-client；本地已起 Qdrant：
#   docker run -d --name qdrant-local -p 6333:6333 -p 6334:6334 qdrant/qdrant
#
# 用法：
#   python scripts/knowledge/ingest_local.py 你的文件.md
#   python scripts/knowledge/ingest_local.py _md            # 或整个目录

import argparse
import os
import sys
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    p = argparse.ArgumentParser(description="把 md 写入本地 Qdrant（跳过 MySQL）")
    p.add_argument("path", nargs="?", help="md 文件或目录")
    p.add_argument("--sync", nargs="+", metavar="PATH",
                   help="对多个目录做整体对账：全部灌入 + 删掉向量库里已不存在的文档。"
                        "改过文件名/删过文件之后用它，否则旧向量会变成检索不到的幽灵条目")
    p.add_argument("--qdrant", default=os.environ.get("QDRANT_URL", "local"),
                   help="Qdrant 地址：local=内嵌(无需Docker)，或 http://ip:6333")
    p.add_argument("--vocab-from", nargs="+", metavar="PATH", default=None,
                   help="BM25 词表**取统计量**的范围，默认 = 本次入库的 roots。"
                        "★ 入库单个文件时**必须显式给全语料目录** —— 词表是语料级统计量"
                        "（df/idf/avgdl），只拿一份文件建词表，语料里绝大多数词都拿不到 id，"
                        "稀疏路（BM25）当场退化成废的，而且**不报错**。实测踩过："
                        "`ingest_local.py <单个md>` 把 12848 词元打成几百个。")
    args = p.parse_args()

    roots = args.sync or ([args.path] if args.path else [])
    if not roots:
        p.error("需要给 path，或用 --sync 指定一个或多个目录")
    vocab_roots = args.vocab_from or roots

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # ⚠️ 不要在这里 setdefault EMBED_MODEL：环境变量 > config.py 默认值，写死一个模型名
    #    会把 config.py 里换好的模型静默顶掉，于是「入库向量」与「query 向量」不同源，
    #    指标全废。模型由 backend/config.py 决定。
    if args.qdrant not in ("local", "memory", ":memory:"):
        os.environ["QDRANT_URL"] = args.qdrant

    BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend")
    sys.path.insert(0, BACKEND_DIR)
    from agent.tools.search_regulations import tool as vector  # noqa: E402

    # 内嵌 Qdrant（无需 Docker）：数据存到 <项目根>/qdrant_local
    if args.qdrant in ("local", "memory", ":memory:"):
        from qdrant_client import QdrantClient
        if args.qdrant in ("memory", ":memory:"):
            vector.set_client(QdrantClient(":memory:"))
        else:
            root = os.path.dirname(BACKEND_DIR)
            vector.set_client(QdrantClient(path=os.path.join(root, "qdrant_local")))

    files = []
    for r in roots:
        if os.path.isdir(r):
            # 递归收集：_md/规章制度 下面还有分类子目录
            for dp, _dirs, ns in os.walk(r):
                files.extend(os.path.join(dp, n) for n in sorted(ns) if n.lower().endswith(".md"))
        else:
            files.append(r)

    if not files:
        print("没有找到 .md 文件")
        return

    vector.ensure_collection()
    # ★ BM25 词表必须在入库**之前**建：idf/avgdl 是全语料统计量，
    #   单篇入库时现算不出来（查询侧也要用同一套，所以要落盘）。
    #   全量分词是秒级，可以每次重建——这样词表永远与本次入库的语料一致。
    n_terms = vector.build_bm25_vocab(vocab_roots)
    print(f"BM25 词表：{n_terms} 个词元 → model/bm25_vocab.json")
    if vocab_roots is roots and len(roots) == 1 and os.path.isfile(roots[0]):
        print("⚠️ 本次入库的是**单个文件**，词表也就只统计了这一份 —— "
              "除非你确定要这样，否则加 `--vocab-from _md/培养方案 _md/规章制度` 重来一次。")
    for f in files:
        title = os.path.splitext(os.path.basename(f))[0]
        with open(f, encoding="utf-8") as fp:
            text = fp.read()
        doc_id = zlib.crc32(title.encode("utf-8")) & 0x7FFFFFFF
        chunk_count = vector.index_document(doc_id, title, text)
        print(f"✅ {title} → document_id={doc_id}，小块数={chunk_count}")

    if args.sync:
        _prune_stale(vector, {os.path.splitext(os.path.basename(f))[0] for f in files})


def _prune_stale(vector, keep_titles: set) -> None:
    """删除向量库里 source 已不在 keep_titles 中的点。

    文件名一改，旧向量的 source 就永远对不上了——它检索不到、也删不掉，
    只会一直占着向量库名额并在统计里虚增文档数。所以改完名字要对一次账。
    """
    import collections

    from core.config import settings
    from qdrant_client import models as qm

    client = vector.client()
    stale: dict[str, int] = collections.defaultdict(int)
    offset = None
    while True:
        recs, offset = client.scroll(collection_name=settings.qdrant_collection,
                                     limit=2000, offset=offset, with_payload=True)
        for r in recs:
            s = (r.payload or {}).get("source") or ""
            if s not in keep_titles:
                stale[s] += 1
        if offset is None:
            break

    if not stale:
        print("🧹 对账完成：没有失效向量")
        return
    for s, n in sorted(stale.items(), key=lambda kv: -kv[1]):
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=qm.FilterSelector(filter=qm.Filter(must=[
                qm.FieldCondition(key="source", match=qm.MatchValue(value=s))])),
        )
        print(f"🧹 已删除失效文档「{s or '(无标题)'}」（{n} 个向量）")


if __name__ == "__main__":
    main()
