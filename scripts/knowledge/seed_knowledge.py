# seed_knowledge.py —— 批量导入 _md/ 知识库语料（已人工校验的 Markdown）
#
# 用法（服务器，backend 容器内，复用容器依赖 + 已挂载的本地模型）：
#   docker compose exec -e MD_DIR=/kb -e BACKEND_DIR=/app \
#     backend python /scripts/knowledge/seed_knowledge.py
# 用法（本地，需 QDRANT_URL / MYSQL_URL 指向目标库）：
#   cd backend && python ../scripts/knowledge/seed_knowledge.py
#
# 目录约定：<MD_DIR>/<大类>/<子类>/xxx.md —— 相对 MD_DIR 的目录路径会作为文档分类写入，
# 与后台「知识库」页面的分类分组一致。
import os
import sys

# backend 代码目录（容器里是 /app，本地是仓库下的 backend/），可用 BACKEND_DIR 覆盖
BACKEND_DIR = os.environ.get("BACKEND_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend"
)
sys.path.insert(0, BACKEND_DIR)

from documents.ingest import mdify  # noqa: E402
from agent.tools.search_regulations import tool as vector  # noqa: E402

MD_DIR = os.environ.get("MD_DIR") or os.path.join(os.path.dirname(BACKEND_DIR), "_md")

_EXTS = (".md", ".txt", ".pdf", ".docx", ".html", ".htm")


def collect() -> list[tuple[str, str]]:
    """递归收集语料，返回 [(文件路径, category)]；category 取相对 MD_DIR 的目录路径。"""
    items: list[tuple[str, str]] = []
    for dirpath, _dirs, names in os.walk(MD_DIR):
        for n in sorted(names):
            if not n.lower().endswith(_EXTS):
                continue
            rel_dir = os.path.relpath(dirpath, MD_DIR)
            category = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            items.append((os.path.join(dirpath, n), category))
    return items


def main():
    if not os.path.isdir(MD_DIR):
        print(f"❌ 语料目录不存在：{MD_DIR}")
        return

    items = collect()
    print(f"找到 {len(items)} 个语料文件，来自 {MD_DIR}")

    # ★ BM25 词表必须在入库**之前**建：idf/avgdl 是全语料统计量，单篇入库时现算不出来。
    #   不建的话 sparse 向量写不出来（_encode_sparse 会直接报错拦下），线上就退化成
    #   dense 单路——混合召回白做。见 backend/bm25.py 与 CLAUDE.md。
    n_terms = vector.build_bm25_vocab([MD_DIR])
    print(f"BM25 词表：{n_terms} 个词元 → model/bm25_vocab.json")

    ok = fail = rejected = 0
    rejects: list[tuple[str, list[str]]] = []
    for path, category in items:
        rel = os.path.relpath(path, MD_DIR)
        title = os.path.splitext(os.path.basename(path))[0]
        with open(path, "rb") as fp:
            content = fp.read()
        try:
            # md 文件会被原样返回；非 md 才走格式转换
            md_text = mdify.to_markdown(os.path.basename(path), content)
            # ★ 入库质量门：不合格的**拒绝入库**，并汇总原因。
            #   空文档进库比不进库更糟——它让检索看起来正常、实际答不出来，
            #   而且「期望文档缺失」这类检查也会被骗过去（文档名确实在库里）。
            reasons = mdify.quality_reasons(md_text)
            if reasons:
                rejects.append((rel, reasons))
                rejected += 1
                continue
            doc_id = vector.add_document(
                title, md_text, mdify.source_type_of(path), category
            )
            print(f"✅ [{category or '未分类'}] {rel} → document_id={doc_id}")
            ok += 1
        except Exception as e:
            print(f"❌ {rel} 入库失败：{e}")
            fail += 1

    print("-" * 60)
    print(f"统计：成功={ok}，失败={fail}，**拒绝入库={rejected}**")
    if rejects:
        print(f"\n⚠️ 以下 {len(rejects)} 份被判不合格、**没有入库**"
              f"（按提示补齐内容后重跑本脚本即可）：")
        for rel, reasons in sorted(rejects, key=lambda x: x[0]):
            print(f"   ⛔ {rel[:70]}")
            for r in reasons:
                print(f"        · {r}")


if __name__ == "__main__":
    main()
