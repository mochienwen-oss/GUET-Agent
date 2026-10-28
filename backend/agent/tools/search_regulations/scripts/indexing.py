# indexing.py —— 向量库：Qdrant 连接 + bge embedding + 文档入库/删除/重建/分块/预览
# 章节/条款父级溯源 + 完整表格行证据；不再按字符长度切分。
# 检索、精排与证据门在 retrieval.py。
import os
import re
from documents.document_availability import writer_lock, mutation

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

from core.config import settings
from agent.core import citations

_client: QdrantClient | None = None
_encoder: SentenceTransformer | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = (QdrantClient(path=settings.qdrant_path, force_disable_check_same_thread=True) if settings.qdrant_path
                   else QdrantClient(url=settings.qdrant_url))
    return _client


def _device_kwargs() -> dict:
    """embedding 的 device 参数：`auto` 交给 ST 自选，否则显式指定（默认 cpu）。

    见 config.embed_device 的注释：线上是为了不和 vLLM 抢显存，
    本地是因为 dense + sparse 两份 bge-m3 会把 15G 内存吃满并卡死。
    """
    if settings.embed_device == "auto":
        return {}
    return {"device": settings.embed_device}


def _get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        import torch
        torch.set_num_threads(settings.inference_threads)
        _encoder = SentenceTransformer(settings.embed_model, **_device_kwargs())
    return _encoder


# ⚠️ 这里**故意没有** bge-m3 的 SparseEncoder。
#   它是 bge-m3 自带的稀疏头，但本机跑不动：dense + sparse 两份 bge-m3 同时驻留会把
#   8G 显存/15G 内存吃穿（进程直接消失，连异常都没有），退到 CPU 则实测 2.3s/块、
#   全量入库要 117 分钟。稀疏路改用自建 BM25 权重（backend/bm25.py）：
#   信号更准、全量分词秒级、且不需要第二个模型。
#   如果将来换到大显存机器想开回来，把 _encode_sparse 换回 SparseEncoder 即可，
#   Qdrant 侧的 named vector / RRF 融合都不用改。


def _encode_sparse(texts: list[str]) -> list:
    """批量稀疏编码（自建 BM25 权重，见 backend/bm25.py）。

    ⚠️ 这里**不是** bge-m3 的稀疏输出。实测本机跑不动 dense + sparse 两份 bge-m3：
      8G 显存/15G 内存会吃穿（进程直接消失，连异常都没有），退到 CPU 则 2.3s/块、
      全量 117 分钟。BM25 给的「精确词面」信号更好也更便宜（全量分词秒级）。

    词表必须**先**由 build_bm25_vocab() 建好并落盘：doc 侧要 idf，
    而 idf 是全语料统计量，不能在单篇入库时现算。
    """
    from . import bm25

    vocab = bm25.load_vocab()
    if vocab is None:
        raise RuntimeError(
            "缺少 BM25 词表（model/bm25_vocab.json）。入库前先建：\n"
            "  python scripts/knowledge/ingest_local.py --sync <目录...>   # 会自动建\n"
            "或显式：python -c \"import sys;sys.path.insert(0,'backend');"
            "import vector;vector.build_bm25_vocab(['_md'])\"")
    out = []
    for t in texts:
        idx, vals = bm25.doc_vector(t, vocab)
        out.append(models.SparseVector(indices=idx, values=vals) if idx else None)
    return out


def build_bm25_vocab(roots: list[str]) -> int:
    """从 md 目录/文件建 BM25 词表并落盘，返回词表大小。

    在**全量入库之前**跑一次。词表是语料级统计量（df/idf/avgdl），必须覆盖所有
    将要入库的块，否则查询侧分词出来的词找不到 id。

    ★ 统计单位是**块**（走 `_build_index_units`，与入库完全同一条切分路径），
      不是「整篇 md」。理由见 bm25.build_vocab 的注释：用文件统计会让 BM25 退化成
      按 TF 排序，实测把表格题 R@1 拉低 16.7~30.8pp。
    """
    from . import bm25

    chunk_texts: list[str] = []
    for r in roots:
        paths: list[str] = []
        if os.path.isdir(r):
            for dp, _d, ns in os.walk(r):
                paths.extend(os.path.join(dp, n) for n in sorted(ns)
                             if n.lower().endswith(".md"))
        elif os.path.isfile(r):
            paths.append(r)
        for p in paths:
            text = open(p, encoding="utf-8", errors="ignore").read()
            chunk_texts.extend(u["text"] for u in _build_index_units(text))
    vocab = bm25.build_vocab(chunk_texts)
    bm25.save_vocab(vocab)
    return len(vocab["terms"])


def ensure_collection() -> None:
    """确保 collection 存在（**命名向量**：dense 1024 维余弦 + sparse 词权重）。

    ★ 命名向量而不是单一匿名向量：混合召回要在同一个 collection 里放两种向量，
      Qdrant 的 prefetch/融合都要求按名字取（`using="dense"` / `using="sparse"`）。
    ⚠️ 从旧版（匿名单向量）升级过来必须**重建 collection**：
       旧点没有 dense/sparse 命名向量，混在一起检索会直接报错。
       本地：`rm -rf qdrant_local`；线上：删掉 collection 后全量重建索引。
    """
    client = _get_client()
    if client.collection_exists(settings.qdrant_collection):
        return
    enc = _get_encoder()
    dim = enc.get_sentence_embedding_dimension() or 1024
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config={"dense": models.VectorParams(size=dim,
                                                     distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )


def _is_table_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[ :|\-]+\|?\s*", line)) and "-" in line


def _is_meaningless(text: str) -> bool:
    return not re.sub(r"[\s#|:—\-]+", "", text)


def _is_complete_article(text: str) -> bool:
    return bool(re.match(r"\s*(?:#{1,6}\s*)?第[一二三四五六七八九十百零〇]+条", text))


def split_text(text: str, max_len: int = 500, overlap: int = 50) -> list[str]:
    """Compatibility API; size arguments no longer create chunk boundaries."""
    return [u["text"] for u in _build_index_units(text)]


def recursive_split(text: str, min_len: int = 400, target: int = 600,
                    max_len: int = 800, overlap_ratio: float = 0.2) -> list[str]:
    """Complete structural units, never fixed-size windows."""
    return split_text(text)


def _split_with_path(text: str, min_len: int = 400, target: int = 600,
                     max_len: int = 800, overlap_ratio: float = 0.2):
    return [(u["text"], u["heading_path"]) for u in _build_index_units(text)]


def _build_index_units(text: str, category: str = "") -> list[dict]:
    """The single upload, reindex, preview and batch chunking implementation."""
    from documents.document_chunks import build_chunks
    return build_chunks(text, category)


def _delete_vectors(client: QdrantClient, document_id: int) -> None:
    """删除某文档在 Qdrant 中的全部向量（按 payload.document_id 过滤）。"""
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                )]
            )
        ),
        wait=True,
    )


def _refresh_sparse():
    """Recompute the shared sparse feature space during closed maintenance.

    Dense vectors are untouched. Never publish a new vocabulary while some
    points still use the previous term IDs/weights.
    """
    if not settings.use_sparse:
        return
    from . import bm25
    client = _get_client()
    rows, offset = [], None
    while True:
        records, offset = client.scroll(collection_name=settings.qdrant_collection,
                                         limit=512, offset=offset,
                                         with_payload=True, with_vectors=False)
        rows.extend(records)
        if offset is None:
            break
    texts = []
    for row in rows:
        payload = row.payload or {}
        title = payload.get("source") or ""
        heading = " / ".join(citations.clean_path(payload.get("heading_path") or [], title))
        texts.append(f"《{title}》\n" + (heading + "\n" if heading else "") + payload["text"])
    vocab = bm25.build_vocab(texts)
    batch_size = max(1, settings.index_upsert_batch_size)
    for start in range(0, len(rows), batch_size):
        updates = []
        for row, text in zip(rows[start:start + batch_size], texts[start:start + batch_size]):
            indices, values = bm25.doc_vector(text, vocab)
            updates.append(models.PointVectors(id=row.id, vector={
                "sparse": models.SparseVector(indices=indices, values=values)}))
        client.update_vectors(collection_name=settings.qdrant_collection,
                              points=updates, wait=True)
    # Publish last, atomically. A failure leaves the maintenance marker closed.
    bm25.save_vocab(vocab)


def _index_document(document_id: int, title: str, text: str, category: str = "",
                    *, units: list[dict] | None = None) -> int:
    """Structure evidence -> named dense/sparse vectors; source stays in MySQL.

    Optional units are for batch filtering only. Uploads and reindexing use
    the same structural splitter. All encoding/length checks precede deletion.
    """
    ensure_collection()
    client = _get_client()
    encoder = _get_encoder()
    if units is None:
        units = _build_index_units(text, category=category)
    if not units:
        raise ValueError("没有可入库的完整结构块，未修改原索引")

    # Embed, rerank and LLM evidence share the title/path/plain-field shape.
    embed_inputs = []
    for u in units:
        # ★ 前缀里的 heading_path 要**洗过**（`citations.clean_path`），与生成层 / 来源注入
        #   用**同一套**规则 —— 这是「三处看到同一种文档身份」那条设计的一部分
        #   （此前只有生成层洗了，向量化与精排都还在吃原始路径）。
        #
        # 为什么必须洗：解析层的标题识别只看「行首是不是编号」，于是**编号与正文黏在一行**
        # 的（PDF 里没换行）整段被当成标题 —— 实测全库 **467 条**「标题行」正文超 40 字
        # （最长 584 字），如
        # `## 九、学分修读要求毕业总学分不低于160学分。其中,通识必修课程46学分,…`
        # （真实标题 p95 才 22 字）。不洗的话这段整段进**向量化输入的前缀**，
        # 等于把正文复制一遍去算向量，稀释掉真正的语义。
        # ⚠️ 这里**只洗前缀、不动分块边界** —— 语料与块都没变，A/B 隔离的就是这一个变量。
        hp = " / ".join(citations.clean_path(u["heading_path"], title))
        embed_inputs.append(f"《{title}》\n{hp}\n{u['text']}" if hp else f"《{title}》\n{u['text']}")
    # Complete units are never silently truncated by the embedding tokenizer.
    limit = encoder.max_seq_length
    for value in embed_inputs:
        if len(encoder.tokenizer(value, truncation=False)["input_ids"]) > limit:
            raise ValueError(f"完整结构块超过 embedding 上限 {limit} tokens，未修改原索引")
    embeddings = (encoder.encode(embed_inputs, normalize_embeddings=True,
                                 batch_size=settings.embed_batch_size).tolist()
                  if embed_inputs else [])
    # 稀疏路（词权重）：同样吃 embed_inputs，这样《标题》+heading_path 也被计入词面，
    # 于是「桂电教〔2025〕28号」这类文号、以及「XX专业培养方案」这类专有名词在
    # 双路上都有一致的判别信息。
    sparse_vecs = (_encode_sparse(embed_inputs)
                   if settings.use_sparse and embed_inputs else [None] * len(embed_inputs))

    _delete_vectors(client, document_id)
    points = [
        models.PointStruct(
            id=document_id * 1000000 + u["chunk_idx"],
            vector=({"dense": emb, "sparse": sv} if sv is not None else {"dense": emb}),
            payload={
                "document_id": document_id,
                "chunk_idx": u["chunk_idx"],
                "source": title,
                "text": u["text"],                  # 明确字段的证据文本，不是表格源码
                "heading_path": u["heading_path"],  # 章节归属（溯源引用）
                "block_type": u["block_type"],
                "category": u["category"],
                **{key: u[key] for key in ("chunk_id", "chunking_version", "source_sha256",
                    "raw_text", "display_html", "source_span", "parent_id", "section_parent_id", "section_span",
                    "previous_id", "next_id", "row_number", "table_fields", "review_flags") if key in u},
            },
        )
        for u, emb, sv in zip(units, embeddings, sparse_vecs)
    ]
    if points:
        batch_size = max(1, settings.index_upsert_batch_size)
        for start in range(0, len(points), batch_size):
            client.upsert(collection_name=settings.qdrant_collection,
                          points=points[start:start + batch_size], wait=True)
    actual = client.count(collection_name=settings.qdrant_collection, exact=True,
                          count_filter=models.Filter(must=[models.FieldCondition(
                              key="document_id", match=models.MatchValue(value=document_id))])).count
    if actual != len(points):
        raise RuntimeError("文档写入块数核对失败，未开放查询")
    from documents.document_metadata import record_document
    record_document(document_id, title, text, category, len(points))
    return len(points)


def add_document(title: str, text: str, source_type: str = "txt", category: str = "") -> int:
    with writer_lock():
        return _add_document(title, text, source_type, category)


def prepare_add_document(title: str, text: str, source_type: str, category: str, *, source_filename: str = ''):
    """Reserve maintenance before returning HTTP success, then run in background.

    The returned closure owns the OS lease until its finally block. No second
    upload can be accepted as 'processing' while an earlier task owns it.
    """
    import sys
    from database.db import Document, SessionLocal
    from documents.document_availability import snapshot, SourceUnavailable
    lease = writer_lock()
    lease.__enter__()
    processing = None
    try:
        with SessionLocal() as db:
            doc = db.query(Document).filter(Document.title == title).first()
            state = snapshot()
            if state["status"] != "ready" and (
                    doc is None or state.get("updating_document") != str(doc.id)):
                raise SourceUnavailable("请先处理上次中断的文档任务")
            if doc is None:
                doc = Document(title=title, source_type=source_type,
                               category=category or "", status="processing")
                db.add(doc)
                db.commit()
                db.refresh(doc)
            processing = mutation(doc.id)
            processing.__enter__()
            doc.status = "processing"
            db.commit()
    except BaseException:
        info = sys.exc_info()
        try:
            if processing is not None:
                processing.__exit__(*info)
        finally:
            lease.__exit__(*info)
        raise

    def run():
        try:
            _add_document(title, text, source_type, category, source_filename=source_filename)
        except BaseException:
            processing.__exit__(*sys.exc_info())
            raise
        else:
            processing.__exit__(None, None, None)
        finally:
            lease.__exit__(None, None, None)
    return run


def _add_document(title: str, text: str, source_type: str, category: str, *, source_filename: str = '') -> int:
    """文档入库：两层切分 → 向量化 → 写 Qdrant + 回写 MySQL 元数据（含原文/分类）。

    对同 title 文档做覆盖式更新：先删旧向量，再写新向量；
    覆盖且未指定分类（category 为空）时继承旧分类。返回 document_id。
    """
    from database.db import Document, SessionLocal

    db = SessionLocal()
    try:
        existing = db.query(Document).filter(Document.title == title).first()
        from documents.document_availability import snapshot, SourceUnavailable
        state = snapshot()
        if state["status"] != "ready" and (
                existing is None or state.get("updating_document") != str(existing.id)):
            raise SourceUnavailable("请先处理上次中断的文档，未创建新的入库任务")
        if existing:
            doc = existing
            if category:
                doc.category = category
        else:
            doc = Document(title=title, source_type=source_type, category=category or "")
            db.add(doc)
            db.commit()
            db.refresh(doc)

        try:
            with mutation(doc.id):
                doc.status = "processing"
                db.commit()
                doc.content = text
                doc.source_type = source_type
                doc.chunk_count = _index_document(doc.id, title, text, category=doc.category or "")
                from documents.source_metadata import document_catalog, extract_metadata
                entry = document_catalog().get(str(doc.id), {})
                maintained = db.query(Document.source_metadata).filter(Document.id == doc.id).with_for_update().scalar()
                doc.source_metadata = dict(maintained or {}, extracted=extract_metadata(
                    title, text, source_filename=source_filename, scope=entry.get('scope')))
                _refresh_sparse()
                doc.status = "ready"
                db.commit()
        except Exception:
            # Closing the session would otherwise roll this back to processing/ready.
            # This records failure; it does not claim Qdrant and SQL are transactional.
            document_id = doc.id
            db.rollback()
            failed = db.query(Document).filter(Document.id == document_id).first()
            if failed:
                failed.status = "failed"
                db.commit()
            raise
        db.commit()
        return doc.id
    finally:
        db.close()


def delete_document(document_id: int) -> None:
    with writer_lock(), mutation(document_id):
        _delete_document(document_id)


def _delete_document(document_id: int) -> None:
    """删除文档：同步删除 Qdrant 向量与 MySQL 元数据。"""
    ensure_collection()
    _delete_vectors(_get_client(), document_id)
    _refresh_sparse()

    from database.db import Document, SessionLocal

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            from documents.document_metadata import forget_document
            forget_document(document_id)
            db.delete(doc)
            db.commit()
    finally:
        db.close()


def get_stats() -> dict:
    """向量库统计：向量总数 + 文档总数。"""
    client = _get_client()
    vector_count = 0
    try:
        if client.collection_exists(settings.qdrant_collection):
            info = client.count(collection_name=settings.qdrant_collection)
            vector_count = info.count
    except Exception:
        pass

    from database.db import Document, SessionLocal

    db = SessionLocal()
    try:
        document_count = db.query(Document).count()
    finally:
        db.close()
    return {"vector_count": vector_count, "document_count": document_count}


# ★ 接缝的**最小重叠长度**（`_join_chunk_texts` 用）。
#   分块器的契约是「自然断点处 0% 重叠、只有字符硬切才叠 overlap」（见 _split_with_path），
#   所以真实接缝要么**完全不重叠**、要么是**几十个字**的重叠。
#   而实测《2025级自动化专业培养方案》99 条接缝里，**82 条"重叠"只有 1~4 个字**——
#   那是巧合（前后两块恰好都以换行开头 / 都以「的」结尾），根本不是接缝。
#   旧实现写的是 `while k > 0`，会照着这些巧合**静默删掉真实的字符**：
#   而 `content` 正是"重建索引"要用的那一份，被删掉的字没有任何地方会报错。
#   ⚠️ 上限仍是 50：真正硬切的 overlap = round(target*0.2) 可能到 120，
#      超过 50 的就不去重了（宁可留一点重复，也不拿长匹配去赌）。
_MIN_SEAM = 8


def _join_chunk_texts(texts: list[str]) -> str:
    """把一串分块正文拼回全文（用于对账时回填 `Document.content`）。

    块之间有重叠（`split_text(..., overlap=50)` 与 `recursive_split` 的接缝都留了上下文），
    直接首尾相接会把接缝重复一遍。所以对每个后续块先量出「它开头有多少字是上一块的结尾」
    并去掉 —— 这样拼出来的全文能直接用于重建索引，而不是一篇被复制的残文。
    """
    out = ""
    for t in texts:
        if not t:
            continue
        if not out:
            out = t
            continue
        k = min(50, len(out), len(t))
        while k >= _MIN_SEAM and not out.endswith(t[:k]):
            k -= 1
        if k < _MIN_SEAM:
            k = 0
        out += t[k:]
    return out


def sync_documents() -> dict:
    with writer_lock():
        from documents.document_availability import require_ready
        require_ready()
        return _sync_documents()


def _sync_documents() -> dict:
    """对账：把 **Qdrant 里有、MySQL `documents` 表里没有**的文档补进表里。

    ★ 为什么需要它（bug③ 的根因）：
      灌库脚本 `scripts/knowledge/ingest_local.py` 是**直接调 `_index_document()`** 写 Qdrant 的
      （document_id 取 `crc32(标题)`），**从头到尾不碰 MySQL**。
      而后台「知识库」的文档列表读的是 MySQL（`routers/admin.list_documents`），
      「总览」里的文档数也读它（`get_stats`）、向量数才读 Qdrant。
      所以灌完库之后：**向量数 3393 在，文档列表一篇都没有** —— 页面上只剩一个数字，
      预览/移动/删除全都无从谈起。

    语义：**只增不删**。缺失的补上；MySQL 有而 Qdrant 没有的（`orphans`）只报告不动 ——
    删除是破坏性操作，不该被一个「同步」按钮顺手做掉。
    已存在的行只修**块数/状态**这类客观字段，**不动分类**（那是人手工标过的）。
    """
    from collections import defaultdict

    from database.db import Document, SessionLocal

    ensure_collection()
    client = _get_client()

    agg: dict[int, dict] = {}
    offset = None
    while True:
        recs, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=1000, offset=offset, with_payload=True, with_vectors=False,
        )
        for r in recs:
            p = r.payload or {}
            did = p.get("document_id")
            if did is None:
                continue
            a = agg.setdefault(int(did), {"source": "", "category": "", "chunks": []})
            if p.get("chunking_version"):
                a["structured_evidence"] = True
            if not a["source"] and p.get("source"):
                a["source"] = p["source"]
            if not a["category"] and p.get("category"):
                a["category"] = p["category"]
            a["chunks"].append((p.get("chunk_idx") or 0, p.get("text") or ""))
        if offset is None:
            break

    db = SessionLocal()
    try:
        existing = {row[0] for row in db.query(Document.id).all()}
        created = repaired = 0
        for did, a in sorted(agg.items()):
            texts = [t for _, t in sorted(a["chunks"])]
            if did in existing:
                row = db.query(Document).filter(Document.id == did).first()
                if row and row.status == "ready" and (row.chunk_count or 0) != len(texts):
                    row.chunk_count = len(texts)
                    repaired += 1
                continue
            db.add(Document(
                id=did,                                   # 沿用 Qdrant 的 document_id，两边必须一致
                title=a["source"] or f"未命名文档-{did}",
                source_type="md",                         # 灌库脚本吃的是 md
                chunk_count=len(texts),
                status="ready",
                category=a["category"] or "",
                # Field projections are NOT source Markdown. A missing original
                # must be re-uploaded, not reconstructed into a counterfeit table.
                content="" if a.get("structured_evidence") else _join_chunk_texts(texts),
            ))
            created += 1
        db.commit()
        return {
            "created": created,
            "repaired": repaired,
            "qdrant_documents": len(agg),
            "orphans": sorted(existing - set(agg)),
        }
    finally:
        db.close()


def _get_doc_records(document_id: int) -> list[dict]:
    """从 Qdrant 读回某文档的所有 chunk（payload 含 text/source/heading_path）。"""
    client = _get_client()
    records, _ = client.scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(
                key="document_id", match=models.MatchValue(value=document_id)
            )]
        ),
        limit=100000,
        with_payload=True,
    )
    return [r.payload or {} for r in records]


def get_document_chunks(document_id: int) -> list[dict]:
    """按 chunk_idx 顺序返回某文档的全部块（供后台分块预览）。"""
    records = _get_doc_records(document_id)
    records.sort(key=lambda r: r.get("chunk_idx", 0))
    return [
        {"chunk_idx": int(r.get("chunk_idx", i)),
         "text": r.get("text", ""),
         "source": r.get("source", ""),
         "heading_path": r.get("heading_path") or [],
         "block_type": r.get("block_type") or "",
         **{k: r[k] for k in ("document_id", "chunk_id", "source_sha256", "section_parent_id",
                             "previous_id", "next_id", "row_number",
                             "chunking_version", "raw_text", "display_html", "source_span", "parent_id",
                             "table_fields", "review_flags") if k in r}}
        for i, r in enumerate(records)
    ]


def reindex_document(document_id: int) -> None:
    with writer_lock():
        from database.db import Document, SessionLocal
        with SessionLocal() as db:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if doc is None or not doc.content:
                raise RuntimeError("文档不存在或缺少原文，请重新上传后再重建")
        with mutation(document_id):
            _reindex_document(document_id)


def _reindex_document(document_id: int) -> None:
    """重建单文档索引：从存储的原文重新做父子切分并重新向量化。"""
    from database.db import Document, SessionLocal

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise RuntimeError("文档不存在")
        if not doc.content:
            raise RuntimeError("该文档缺少原文（旧数据），请重新上传后再重建")
        doc.status = "processing"
        db.commit()
        doc.chunk_count = _index_document(document_id, doc.title, doc.content,
                                          category=doc.category or "")
        _refresh_sparse()
        doc.status = "ready"
        db.commit()
    except Exception:
        db.rollback()
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "failed"
            db.commit()
        raise
    finally:
        db.close()


def reindex_all() -> None:
    """全量重建索引（跳过缺少原文的旧数据）。"""
    from database.db import Document, SessionLocal

    db = SessionLocal()
    try:
        docs = db.query(Document).all()
    finally:
        db.close()
    for doc in docs:
        if not doc.content:
            continue
        reindex_document(doc.id)
