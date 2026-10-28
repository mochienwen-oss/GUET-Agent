import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from core.config import settings
from security.auth import require_auth
from database.db import get_db
from core.errors import ApiError
from routers.ppt import router
from agent.tools.create_ppt.scripts import public_materials


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: None
    @app.exception_handler(ApiError)
    async def api_error(request, error):
        return JSONResponse({"error": error.message}, status_code=error.status_code)
    return TestClient(app), app


def test_material_upload_analysis_and_provided_only_boundary(client, monkeypatch):
    c, app = client
    created = c.post("/api/ppt/tasks", json={"brief": {"goal": "统计", "audience": "教师"}})
    assert created.status_code == 200
    tid = created.json()["task_id"]
    added = c.post(f"/api/ppt/task/{tid}/materials", files=[("files", ("课时.csv", "课程,学时\n操作系统,40\n计算机网络,48\n".encode(), "text/csv"))])
    assert added.status_code == 200
    mid = added.json()["materials"][0]["material_id"]
    result = c.post(f"/api/ppt/task/{tid}/analysis", json={"material_id": mid, "operation": "sum", "field": "学时"})
    assert result.status_code == 200 and result.json()["values"] == ["88"]
    missing = c.post(f"/api/ppt/task/{tid}/analysis", json={"material_id": mid, "operation": "share", "field": "学时"})
    assert missing.status_code == 400 and "分母" in missing.json()["error"]
    monkeypatch.setattr(public_materials, "read_public_material", lambda *a, **k: pytest.fail("Must not access network"))
    blocked = c.post(f"/api/ppt/task/{tid}/public-source", json={"url": "https://example.org", "question": "补充背景"})
    assert blocked.status_code == 409
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=2)
    assert c.get(f"/api/ppt/task/{tid}").status_code == 404
    assert c.post(f"/api/ppt/task/{tid}/analysis", json={"material_id": mid, "operation": "count"}).status_code == 404


def test_missing_materials_remains_waiting_instead_of_fake_success(client):
    c, _ = client
    tid = c.post("/api/ppt/tasks", json={"brief": {"goal": "说明", "audience": "新生"}}).json()["task_id"]
    result = c.post(f"/api/ppt/task/{tid}/generate", json={"expected_revision": 0})
    assert result.status_code == 200 and result.json()["stage"] == "await_materials"


def test_workspace_list_is_owned_read_only_and_contains_only_summaries(client):
    from agent.tools.create_ppt.scripts import subgraph
    c, app = client
    first = c.post('/api/ppt/tasks', json={'brief': {'goal': '自己的演示文稿', 'audience': '新生'}}).json()
    path = Path(subgraph._state_path(first['task_id']))
    before = path.read_bytes(), path.stat().st_mtime_ns
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=2)
    other = c.post('/api/ppt/tasks', json={'brief': {'goal': '别人的演示文稿', 'audience': '教师'}}).json()
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=1)
    rows = c.get('/api/ppt/tasks').json()
    assert [row['task_id'] for row in rows] == [first['task_id']]
    assert rows[0]['title'] == '自己的演示文稿'
    assert set(rows[0]) == {'task_id', 'title', 'stage', 'revision', 'pages', 'conversation_id', 'updated_at'}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=2)
    assert [row['task_id'] for row in c.get('/api/ppt/tasks').json()] == [other['task_id']]


def test_master_design_answer_is_owned_and_does_not_expose_execution_context(client, monkeypatch):
    from llm import llm_api_client
    from agent.tools.create_ppt.scripts.master_workspace import MasterWorkspace
    monkeypatch.setattr(MasterWorkspace, 'initialize', lambda workspace, state: workspace.folder.mkdir(parents=True, exist_ok=True))
    def model(messages, **kwargs):
        answered = any('"approved": true' in m.get('content', '') for m in messages if m['role'] == 'user')
        return {'calls': [{'name': 'request_confirmation', 'arguments': {'stage': 'stage2' if answered else 'stage1',
            'question': '确认方案？', 'proposal': '使用现有校园材料'}}], 'usage': {}}
    monkeypatch.setattr(llm_api_client, 'complete_sync', model)
    c, app = client
    task = c.post('/api/ppt/tasks', json={'brief': {'goal': '说明', 'audience': '新生'},
        'materials': [{'title': '材料', 'text': '事实'}]}).json()
    tid = task['task_id']
    waiting = c.post(f'/api/ppt/task/{tid}/generate', json={'expected_revision': 0}).json()
    assert waiting['renderer'] == 'ppt-master' and waiting['stage'] == 'await_design'
    assert 'master' not in waiting
    body = {'expected_revision': 0, 'confirmation_id': waiting['pending_user']['confirmation_id'], 'approved': True}
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=2)
    assert c.post(f'/api/ppt/task/{tid}/design-answer', json=body).status_code == 404
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(id=1)
    approved = c.post(f'/api/ppt/task/{tid}/design-answer', json=body)
    assert approved.status_code == 200 and approved.json()['pending_user']['stage'] == 'stage2'
    assert c.post(f'/api/ppt/task/{tid}/design-answer', json=body).status_code == 409
