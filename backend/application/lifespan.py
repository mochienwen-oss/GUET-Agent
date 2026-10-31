"""应用启动与关闭流程。"""
import logging
import asyncio
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
        "启动配置 | collection=%s | vector_backend=%s | embed=%s | rerank=%s/fp16=%s | "
        "generation=%s | max_tokens=%s ctx_chars=%s | top_k=%s rerank_pool=%s recall_pool=%s "
        "near_dup=%s | 阈值 low=%.2f high=%.2f | 开关 2a=%s 2b=%s 2d=%s 方案1=%s 方案4=%s 方案6=%s",
        settings.qdrant_collection, settings.vector_backend, settings.embed_device,
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
    if settings.database_url:
        from sqlalchemy.engine import make_url
        if make_url(settings.database_url).get_backend_name() == 'postgresql':
            from agent.runtime.user_memory import setup
            setup()
            from database.db import MemoryControl, SessionLocal
            with SessionLocal() as db:
                if db.get(MemoryControl, 1) is None:
                    raise RuntimeError('新记忆系统尚未完成初始化；请在停机状态执行旧记忆清理')
    from documents.catalog.document_availability import recover_interrupted
    recover_interrupted()
    from agent.tools.create_ppt.scripts.execution.subgraph import recover_interrupted as recover_presentations
    recover_presentations()
    from application.runtime_identity import verify_runtime
    verify_runtime()
    async def memory_worker():
        from agent.runtime.user_memory import work_once, maintain_once
        while True:
            try:
                worked = await asyncio.to_thread(work_once)
                if not worked:
                    worked = await asyncio.to_thread(maintain_once)
            except Exception as error:
                logging.getLogger(__name__).warning('memory_worker_failed kind=%s', type(error).__name__)
                worked = False
            if not worked:
                await asyncio.sleep(2)

    memory_task = asyncio.create_task(memory_worker()) if settings.database_url else None
    try:
        yield
    finally:
        if memory_task:
            memory_task.cancel()
            try:
                await memory_task
            except asyncio.CancelledError:
                pass
        from agent.tools.create_ppt.scripts.execution.background import shutdown
        await shutdown()


