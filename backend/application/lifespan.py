"""应用启动与关闭流程。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from database.db import init_db, seed_admin
from core.config import settings
from agent.tools.search_regulations.tool import DEFAULT_RERANK_HIGH, DEFAULT_RERANK_LOW


@asynccontextmanager
async def lifespan(app: FastAPI):
    _b = lambda x: "on" if x else "off"
    from llm.llm_api_config import get_api_config
    api = get_api_config()
    api.active.api_key()
    generation = '%s/%s/%s/%s' % (api.active_profile, api.active.protocol, api.active.base_url, api.active.model)
    logging.getLogger("uvicorn.error").info(
        "启动配置 | collection=%s | qdrant=%s | embed=%s | rerank=%s/fp16=%s | "
        "generation=%s | max_tokens=%s ctx_chars=%s | top_k=%s rerank_pool=%s recall_pool=%s "
        "near_dup=%s | 阈值 low=%.2f high=%.2f | 开关 2a=%s 2b=%s 2d=%s 方案1=%s 方案4=%s 方案6=%s",
        settings.qdrant_collection, settings.qdrant_path or settings.qdrant_url, settings.embed_device,
        settings.rerank_device, settings.rerank_fp16,
        generation, api.tasks['planning'].max_output_tokens,
        settings.max_context_chars, settings.top_k, settings.rerank_pool,
        settings.recall_pool, settings.near_dup_threshold,
        DEFAULT_RERANK_LOW, DEFAULT_RERANK_HIGH,
        _b(settings.flow_expand), _b(settings.ref_follow), _b(settings.multiturn_carry),
        _b(settings.sparse_fuse_gate), _b(settings.aux_route),
        _b(settings.gate_scenario_band))
    init_db()
    seed_admin()
    from documents.document_availability import recover_interrupted
    recover_interrupted()
    from agent.tools.create_ppt.scripts.subgraph import recover_interrupted as recover_presentations
    recover_presentations()
    from application.runtime_identity import verify_runtime
    verify_runtime()
    try:
        yield
    finally:
        from agent.tools.create_ppt.scripts.background import shutdown
        await shutdown()


