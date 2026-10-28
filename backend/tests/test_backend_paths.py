"""包迁移后的实际资源定位与 main:app 入口回归。"""
import asyncio
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Settings, settings
from core.paths import BACKEND_ROOT, PROJECT_ROOT
from entities import entity_aliases, entity_catalog
from llm import ctx_budget, final_answer_profile, llm_api_config


def test_default_resources_stay_at_existing_locations():
    assert Path(Settings.model_config["env_file"]) == PROJECT_ROOT / ".env"
    assert Path(Settings.model_fields["system_prompt_path"].default).is_file()
    assert Path(Settings.model_fields["active_documents_path"].default) == BACKEND_ROOT / "data/documents.active.json"
    assert entity_catalog.DEFAULT_PLAN_DB == BACKEND_ROOT / "data/plan_db.json"
    assert entity_catalog.DEFAULT_CORPUS == PROJECT_ROOT / "final-md"
    assert entity_aliases.DEFAULT_DIR == BACKEND_ROOT / "data"
    assert Path(ctx_budget._manifest_path()) == BACKEND_ROOT / "data/ctx_budget.json"
    assert final_answer_profile.WRITER_SYSTEM == (
        BACKEND_ROOT / "prompts" / (final_answer_profile.WRITER_PROFILE + ".txt")
    ).read_text(encoding="utf-8")


def test_relative_api_config_keeps_project_root_semantics(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_config_path", str(PROJECT_ROOT / "deploy/llm-api.json"))
    absolute = llm_api_config.get_api_config()
    monkeypatch.setattr(settings, "llm_api_config_path", "deploy/llm-api.json")
    assert llm_api_config.get_api_config() is absolute


def test_main_entry_exports_the_same_app_and_health_route():
    from main import app
    from application.app import app as assembled

    assert app is assembled

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
    asyncio.run(run())
