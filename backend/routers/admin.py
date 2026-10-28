# routers/admin.py —— 文档管理 + 对话统计（仅管理员）
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, Request
from agent.core.request_control import run_request
from sqlalchemy.orm import Session

from documents import mdify
from documents.document_chunks import VERSION as CHUNKING_VERSION, build_chunks
from agent.tools.search_regulations import tool as rag
from agent.tools.search_regulations import tool as vector
from security.auth import require_admin
from core.config import settings
from database.db import (Conversation, Document, Message, User, get_db,
                get_setting_bool, get_setting_float, get_setting_int, set_setting)
from core.errors import ApiError
from routers.schemas import AdminSettings, CategoryRename, DocumentCreate, DocumentUpdate, RetrievalTest, SourceMetadataBody
from sqlalchemy import func

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.post("/documents/parse")
async def parse_document(request: Request, file: UploadFile = File(...), user: User = Depends(require_admin)):
    """上传文件，仅解析为 Markdown 文本返回（不向量化入库）。"""
    content = await file.read()
    if not content:
        raise ApiError(400, "文件为空")
    filename = (file.filename or "未命名").strip()
    try:
        def parse():
            markdown, stats = mdify.to_markdown_report(filename, content)
            return markdown, stats, build_chunks(markdown)
        markdown, stats, units = await run_request(parse, request)
    except ValueError as e:
        raise ApiError(400, str(e))
    except Exception as e:
        raise ApiError(500, f"文件解析失败：{e}")
    warnings = mdify.quality_reasons(markdown, pages=stats.get("pages"), stats=stats)
    warnings.extend(sorted({f for u in units for f in u["review_flags"]}))
    return {"filename": filename, "source_type": mdify.source_type_of(filename), "markdown": markdown,
            "chunking_version": CHUNKING_VERSION, "chunk_count": len(units),
            "table_rows": sum(u["block_type"] == "table" for u in units),
            "warnings": warnings, "parse_stats": stats}


@router.post("/documents/preview")
def preview_document(body: DocumentCreate, user: User = Depends(require_admin)):
    """Preview edited Markdown through exactly the same splitter as indexing."""
    try:
        units = build_chunks(body.content, body.category or "")
    except ValueError as exc:
        raise ApiError(400, str(exc))
    return {"chunking_version": CHUNKING_VERSION, "chunks": units}


def _ingest_document(title: str, task) -> None:
    """后台向量化：`BackgroundTasks` 在响应发出**之后**才跑，不占这次请求的时长。

    ★ 2026-10-12：入库原先在请求里**同步**跑。一份培养方案几十块要在 **CPU** 上编码
    （线上 `EMBED_DEVICE=cpu`，见 config.py），超过前端 axios 的 30s 超时 ——
    现象是前端弹「请求失败，请稍后重试」、api.log 里**连一条访问日志都没有**，
    但服务端线程照常写完（`2025级人工智能专业培养方案(主修)` 39 块就是这么进库的）。
    搬到后台后请求立刻返回，这类**假失败**从根上消失。

    add_document 获取维护锁后才写 processing，成功改 ready、失败改 failed。
    重启时将中断任务标为 failed，不自动恢复可查询状态。
    """
    try:
        task()
    except Exception:
        # The writer lease/status belongs to add_document, not the queued task.
        # 后台任务没有请求上下文可以把错误抛回去（add_document 已把该行标成 failed），
        # 只留日志，免得异常在响应之后冒出来把线程池日志冲掉
        logger.exception("后台入库失败：%s", title)


@router.post("/documents")
def create_document(body: DocumentCreate, background: BackgroundTasks,
                    user: User = Depends(require_admin)):
    """入库：先过质量门，再把（可经前端编辑后的）Markdown 交给后台向量化写入知识库。

    质量门仍**同步**判（秒级，用于拦「解析出一小段就提交」）；向量化那段慢活走后台。
    """
    title = (body.title or "").strip()
    content = body.content or ""
    if not title:
        raise ApiError(400, "标题不能为空")
    if not content.strip():
        raise ApiError(400, "内容不能为空")
    # ★ 入库质量门：**拒绝不合格文档入库**。
    #   前端「上传 → 编辑 → 入库」这条路的 md 是人手确认过的，但仍要挡住
    #   「解析出一小段就提交」的情况——空文档进库比不进库更糟：它让检索看起来正常、
    #   实际答不出来，还会骗过「期望文档是否在库」这类检查。
    reasons = mdify.quality_reasons(content)
    if reasons:
        raise ApiError(400, "文档质量不合格，未入库：" + "；".join(reasons))
    try:
        if not build_chunks(content, body.category or ""):
            raise ValueError("没有可入库的完整结构块")
    except ValueError as exc:
        raise ApiError(400, str(exc))
    from documents.document_availability import SourceUnavailable
    try:
        task = vector.prepare_add_document(title, content, body.source_type or "txt", body.category or "",
                                           source_filename=body.source_filename)
    except SourceUnavailable as exc:
        raise ApiError(409, str(exc)) from exc
    background.add_task(_ingest_document, title, task)
    return {"status": "processing", "title": title}


@router.get("/documents")
def list_documents(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    from documents.document_availability import snapshot
    availability = snapshot()
    docs = db.query(Document).order_by(Document.id.desc()).all()
    return [
        {"id": d.id, "title": d.title, "source_type": d.source_type,
         "chunk_count": d.chunk_count,
         "status": (availability["documents"].get(str(d.id))
                    if availability["documents"].get(str(d.id)) in {"processing", "failed"} else d.status),
         "query_available": availability["status"] == "ready" and d.status == "ready"
                            and availability["documents"].get(str(d.id), "ready") == "ready",
         "category": d.category or "",
         "source_metadata": d.source_metadata or {},
         "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None}
        for d in docs
    ]


@router.patch("/documents/{document_id}/source-metadata")
def update_source_metadata(document_id: int, body: SourceMetadataBody,
                           user: User = Depends(require_admin), db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).with_for_update().first()
    if not doc:
        raise ApiError(404, "文档不存在")
    doc.source_metadata = dict(doc.source_metadata or {}, **{
        key: value.strip() if isinstance(value, str) else value
        for key, value in body.model_dump().items()
    })
    db.commit()
    return {"ok": True, "source_metadata": doc.source_metadata}


@router.patch("/documents/{document_id}")
def update_document(document_id: int, body: DocumentUpdate, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """移动文档到分类（分类为自由字符串，文档只存归属，不建独立分类表）。"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise ApiError(404, "文档不存在")
    doc.category = (body.category or "").strip()
    db.commit()
    return {"ok": True}


@router.patch("/categories")
def rename_category(body: CategoryRename, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """分类文件夹重命名：把 old_name 分类下的所有文档改到 new_name。

    文档只存归属字符串（没有独立分类表），所以重命名 = 批量改归属。
    重命名**不动向量**（分类只是展示归属），无需重建索引。
    """
    old = (body.old_name or "").strip()
    new = (body.new_name or "").strip()
    if not old:
        raise ApiError(400, "原分类名不能为空")
    if not new:
        raise ApiError(400, "新分类名不能为空")
    docs = db.query(Document).filter(Document.category == old).all()
    if not docs:
        raise ApiError(404, "该分类下没有文档")
    for d in docs:
        d.category = new
    db.commit()
    return {"ok": True, "moved": len(docs)}


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise ApiError(404, "文档不存在")
    try:
        vector.delete_document(document_id)
    except Exception as e:
        raise ApiError(500, f"删除失败：{e}")
    return {"ok": True}


@router.post("/documents/{document_id}/reindex")
def reindex_document(document_id: int, user: User = Depends(require_admin)):
    try:
        vector.reindex_document(document_id)
    except Exception as e:
        raise ApiError(500, f"重建失败：{e}")
    return {"ok": True}


@router.post("/reindex")
def reindex_all(user: User = Depends(require_admin)):
    try:
        vector.reindex_all()
    except Exception as e:
        raise ApiError(500, f"全量重建失败：{e}")
    return {"ok": True}


@router.post("/documents/sync")
def sync_documents(user: User = Depends(require_admin)):
    """对账：把**灌库脚本写进 Qdrant、却没进 `documents` 表**的文档补出来。

    为什么需要：`scripts/knowledge/ingest_local.py` 直接调 `vector._index_document()` 写 Qdrant
    （document_id 取 crc32），全程不碰 MySQL；而本页的文档列表与总览的文档数都读 MySQL。
    结果是灌完库只看得见向量数、看不见任何文档（bug③）。详见 `vector.sync_documents`。

    **只增不删**：缺失的补上，MySQL 有而 Qdrant 没有的只作为 `orphans` 报告，
    不在这里顺手删除 —— 删是破坏性的，得由人明确发起。
    """
    try:
        return vector.sync_documents()
    except Exception as e:
        raise ApiError(500, f"同步失败：{e}")


@router.get("/stats")
def stats(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_messages = db.query(Message).count()
    total_conversations = db.query(Conversation).count()
    total_chunks = db.query(func.sum(Document.chunk_count)).scalar() or 0
    rag_stats = vector.get_stats()
    return {
        "total_messages": total_messages,
        "total_conversations": total_conversations,
        "vector_count": rag_stats["vector_count"],
        "document_count": rag_stats["document_count"],
        "total_chunks": total_chunks,
    }


def _read_settings(db: Session) -> dict:
    return {
        "retrieval_top_k": get_setting_int(db, "retrieval_top_k", settings.top_k),
        "crag_high_score": get_setting_float(db, "crag_high_score", 0.6),
        "crag_low_score": get_setting_float(db, "crag_low_score", 0.3),
        "use_llm_grader": get_setting_bool(db, "use_llm_grader", False),
        "use_reranker": get_setting_bool(db, "use_reranker", True),
        "rerank_high_score": get_setting_float(db, "rerank_high_score", rag.DEFAULT_RERANK_HIGH),
        "rerank_low_score": get_setting_float(db, "rerank_low_score", rag.DEFAULT_RERANK_LOW),
    }


@router.get("/settings")
def get_settings(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _read_settings(db)


def _check_range(value: float, lo: float, hi: float, label: str) -> None:
    if value < lo or value > hi:
        raise ApiError(400, f"{label}需在 {lo}~{hi} 之间")


@router.put("/settings")
def update_settings(body: AdminSettings, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.retrieval_top_k is not None:
        _check_range(body.retrieval_top_k, 1, 20, "top_k 召回数")
        set_setting(db, "retrieval_top_k", str(body.retrieval_top_k))
    if body.crag_high_score is not None:
        _check_range(body.crag_high_score, 0, 1, "cRAG 高相关阈值")
        set_setting(db, "crag_high_score", str(body.crag_high_score))
    if body.crag_low_score is not None:
        _check_range(body.crag_low_score, 0, 1, "cRAG 低相关阈值")
        set_setting(db, "crag_low_score", str(body.crag_low_score))
    if body.rerank_high_score is not None:
        _check_range(body.rerank_high_score, 0, 1, "reranker 高相关阈值")
        set_setting(db, "rerank_high_score", str(body.rerank_high_score))
    if body.rerank_low_score is not None:
        _check_range(body.rerank_low_score, 0, 1, "reranker 低相关阈值")
        set_setting(db, "rerank_low_score", str(body.rerank_low_score))
    if body.use_llm_grader is not None:
        set_setting(db, "use_llm_grader", "1" if body.use_llm_grader else "0")
    if body.use_reranker is not None:
        set_setting(db, "use_reranker", "1" if body.use_reranker else "0")
    return _read_settings(db)


@router.post("/test-retrieval")
async def test_retrieval(body: RetrievalTest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """检索调试：**只做效果查看**（2026-10-01）。参数冻结为标定常量，忽略请求体里的设置项。

    唯一的例外是 `ungated`（2026-10-02）：门控判空时 `contexts` 为空、排序信息丢失，
    复标阈值与排查"库里明明有却说没有"时看不到分数，所以留一个把 low 阈值压到 0 的
    **标定用**开关。它仍走 `rag.retrieve`（检索逻辑的单一来源），没有复制任何检索代码。
    """
    query = (body.query or "").strip()
    if not query:
        raise ApiError(400, "请输入测试问题")
    try:
        if body.ungated:
            result = await run_request(rag.retrieve, request, query, top_k=None,
                                  crag_high=rag.DEFAULT_CRAG_HIGH, crag_low=0.0,
                                  use_llm_grader=False,
                                  use_reranker=rag.FROZEN_USE_RERANKER,
                                  rerank_high=rag.DEFAULT_RERANK_HIGH, rerank_low=0.0)
        else:
            result = await run_request(rag.retrieve_frozen, request, query)
    except Exception as e:
        raise ApiError(500, f"检索失败：{e}")
    return {"query": query, "ungated": bool(body.ungated),
            "use_reranker": rag.FROZEN_USE_RERANKER, **result}


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: int, user: User = Depends(require_admin)):
    try:
        return {"chunks": vector.get_document_chunks(document_id)}
    except Exception as e:
        raise ApiError(500, f"读取分块失败：{e}")
