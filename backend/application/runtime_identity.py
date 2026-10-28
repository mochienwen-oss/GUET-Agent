"""Startup identity checks for the approved deployed profile (no rebuilding)."""
import hashlib
import json
import logging
import os
from pathlib import Path

from core.config import settings


def verify_runtime():
    from llm.llm_api_config import get_api_config
    api = get_api_config()
    api.active.api_key()  # Validate credentials without a paid probe or /models dependency.
    profile_path = os.environ.get("SAI_RUNTIME_PROFILE")
    if not profile_path:
        return  # Tests and legacy tools do not claim to run this deployment.
    profile = json.loads(Path(profile_path).read_text("utf-8"))
    if settings.qdrant_collection != profile["collection"]:
        raise RuntimeError("启动配置与正式运行配置不一致，拒绝静默连接旧库")
    path = Path(settings.bm25_vocab_path)
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError("BM25词表必须是存在的绝对路径")
    from agent.tools.search_regulations.scripts import bm25
    vocab = bm25.load_vocab()
    if not vocab or not vocab.get("terms"):
        raise RuntimeError("BM25词表为空，拒绝缺少词面的运行")
    from database.db import SessionLocal, Document
    from qdrant_client import QdrantClient
    active = json.loads(Path(settings.active_documents_path).read_text("utf-8"))
    from documents.document_availability import snapshot
    maintenance = snapshot()["status"] != "ready"
    with SessionLocal() as db:
        actual = {d.id: (d.title, d.chunk_count) for d in db.query(Document).all()}
    wanted = {d["document_id"]: (d["title"], d["chunks"]) for d in active["documents"]}
    if actual != wanted and not maintenance:
        raise RuntimeError("管理文档与活动文档ID/标题/块数未对齐，拒绝启动维护入口")
    if settings.qdrant_path:
        from agent.tools.search_regulations.scripts.indexing import _get_client
        info = _get_client().get_collection(settings.qdrant_collection)
    else:
        info = QdrantClient(url=settings.qdrant_url).get_collection(settings.qdrant_collection)
    if info.points_count != sum(x[1] for x in actual.values()) and not maintenance:
        raise RuntimeError("向量点数与文档分块清单不一致")
    if maintenance:
        logging.getLogger("uvicorn.error").warning(
            "上次文档任务未完成：以维护状态启动，知识库查询保持关闭，允许管理员重试")
    logging.getLogger("uvicorn.error").info(
        "运行身份通过 | documents=%d points=%d prompt_sha=%s bm25_sha=%s",
        len(actual), info.points_count,
        hashlib.sha256(Path(settings.system_prompt_path).read_bytes()).hexdigest(),
        hashlib.sha256(path.read_bytes()).hexdigest())
