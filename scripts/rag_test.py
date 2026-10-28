# rag_test.py —— 本地查看 RAG 检索效果
#
# 本地加载 bge embedding，连 Qdrant，输入问题查看召回的分块/来源/分数。
# 直接复用 backend/rag.py 的 retrieve()（small-to-big + cRAG），与后端线上一致。
#
# 前置：
#   pip install sentence-transformers qdrant-client
#   确保远程 Qdrant 可访问：服务器安全组放行 6333，或用 SSH 隧道（更安全）：
#       ssh -L 6333:localhost:6333 root@<服务器IP>
#   首次会下载 bge 模型（约 1.3GB，走 hf-mirror）；--rerank 还需下载 bge-reranker-base。
#
# 用法（在项目根目录执行）：
#   python scripts/rag_test.py "转专业需要哪些条件"
#   python scripts/rag_test.py --rerank "转专业需要哪些条件"
#   python scripts/rag_test.py                      # 交互模式

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    p = argparse.ArgumentParser(description="本地查看 RAG 检索效果（small-to-big + cRAG）")
    p.add_argument("query", nargs="?", default=None, help="问题（不填进入交互模式）")
    p.add_argument("--qdrant", default=os.environ.get("QDRANT_URL", "local"),
                   help="Qdrant 地址：local=内嵌(无需Docker)，或 http://ip:6333")
    p.add_argument("--collection", default=None, help="Qdrant collection（默认 school_docs）")
    p.add_argument("--top-k", type=int, default=None, help="召回数（默认取后端配置 3）")
    p.add_argument("--rerank", action="store_true", help="叠加 reranker 精排")
    p.add_argument("--full", action="store_true", help="完整打印召回内容（不截断、保留换行，同时显示命中小块与完整父块）")
    p.add_argument("--crag-high", type=float, default=0.6, help="cRAG 高相关阈值")
    p.add_argument("--crag-low", type=float, default=0.3, help="cRAG 低相关阈值")
    p.add_argument("--rerank-high", type=float, default=0.5)
    p.add_argument("--rerank-low", type=float, default=0.3)
    args = p.parse_args()

    # 先设好环境变量，再 import backend（config 才会读到正确的地址/模型/镜像）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("EMBED_MODEL", "BAAI/bge-large-zh-v1.5")
    if args.qdrant not in ("local", "memory", ":memory:"):
        os.environ["QDRANT_URL"] = args.qdrant
    if args.collection:
        os.environ["QDRANT_COLLECTION"] = args.collection

    BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    sys.path.insert(0, BACKEND_DIR)
    from agent.tools.search_regulations import tool as rag  # noqa: E402
    from agent.tools.search_regulations import tool as vector  # noqa: E402

    # 内嵌 Qdrant（无需 Docker）：数据存到 <项目根>/qdrant_local
    if args.qdrant in ("local", "memory", ":memory:"):
        from qdrant_client import QdrantClient
        if args.qdrant in ("memory", ":memory:"):
            vector.set_client(QdrantClient(":memory:"))
        else:
            root = os.path.dirname(BACKEND_DIR)
            vector.set_client(QdrantClient(path=os.path.join(root, "qdrant_local")))

    def do_query(q: str):
        try:
            result = rag.retrieve(
                q, top_k=args.top_k,
                crag_high=args.crag_high, crag_low=args.crag_low,
                use_llm_grader=False,
                use_reranker=args.rerank,
                rerank_high=args.rerank_high, rerank_low=args.rerank_low,
            )
        except Exception as e:
            print(f"检索失败：{e}")
            return
        print_result(q, result, args.rerank, args.full)

    if args.query:
        do_query(args.query)
    else:
        mode = "cRAG + reranker" if args.rerank else "cRAG"
        print(f"Qdrant: {args.qdrant}　模式: {mode}")
        print("输入问题回车检索；q/quit 退出")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in ("q", "quit", "exit"):
                break
            do_query(q)


def print_result(q: str, result: dict, want_rerank: bool, full: bool = False):
    contexts = result.get("contexts", [])
    print(f"\n📌 {q}")
    print(f"档位 {result.get('tier')}（{result.get('reason')}），召回 {len(contexts)} 条")
    if want_rerank and contexts and all("rerank_score" not in c for c in contexts):
        print("⚠️ reranker 未生效（可能未安装/下载失败），已回退 embedding 分数")
    for i, c in enumerate(contexts, 1):
        score = c.get("score")
        rerank = c.get("rerank_score")
        score_str = f"score={score}"
        if rerank is not None:
            score_str += f"　rerank={rerank}"
        print(f"\n[{i}] 《{c.get('source')}》 {score_str}")
        if full:
            snippet = (c.get("snippet") or "").strip()
            text = (c.get("text") or "").strip()
            if snippet:
                print("    ---- 命中小块 ----")
                print(snippet)
            if text and text != snippet:
                print("    ---- 完整父块 ----")
                print(text)
        else:
            text = (c.get("text") or "").replace("\n", " ").strip()
            if len(text) > 180:
                text = text[:180] + " …"
            print(f"    {text}")


if __name__ == "__main__":
    main()
