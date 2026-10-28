"""PPT 任务查询与受保护下载。"""
from fastapi import APIRouter, Depends, Body, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from security.auth import require_auth
from database.db import User, Conversation, get_db
from core.errors import ApiError
from agent.tools.create_ppt import tool as ppt_tool
from agent.tools.create_ppt.scripts import background, workflow, subgraph
from agent.tools.create_ppt.scripts.materials import Material, read_materials, MAX_UPLOAD_BYTES
from pydantic import BaseModel, Field
from typing import Literal
from agent.core.request_control import run_request


router = APIRouter()


@router.get('/ppt/style-reference/{style_id}')
def style_reference(style_id: str):
    """Public reference artwork shipped with the installed Skill, not task data."""
    from agent.tools.create_ppt.scripts.master_workspace import SKILL_DIR
    if style_id not in {'swiss-minimal', 'editorial', 'blueprint'}:
        raise ApiError(404, '风格参考不存在')
    path = SKILL_DIR / 'scripts' / 'confirm_ui' / 'static' / 'style_previews' / (style_id + '.svg')
    if not path.is_file():
        raise ApiError(404, '风格参考不存在')
    return FileResponse(path, media_type='image/svg+xml')


@router.get("/ppt/tasks")
def list_tasks(user: User = Depends(require_auth)):
    """工作区的只读摘要；沿用已有任务文件和归属校验。"""
    from pathlib import Path
    from datetime import datetime, timezone

    result = []
    for path in Path(subgraph._workdir()).glob("*.json"):
        try:
            state = ppt_tool.owned_state(path.stem, user.id)
            updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except (ApiError, ppt_tool.TaskCorrupt, OSError):
            continue
        result.append({"task_id": state["task_id"],
                       "title": state.get('title') or (state.get("brief") or {}).get("goal") or "未命名演示文稿",
                       "stage": state.get("stage"), "revision": state.get("revision", 0),
                       "background_running": background.running(state['task_id']),
                       "pages": (state.get("artifacts") or {}).get("pages", 0),
                       "conversation_id": state.get("conversation_id"), "updated_at": updated})
    return sorted(result, key=lambda task: task["updated_at"], reverse=True)


def _review(state: dict) -> dict:
    """把已生成的内容投影成**可审阅**的形状：逐页要点 + 被引用到的来源。

    ★ 只给出**被引用到**的来源（按 `evidence_id` 过滤），正文截断到 400 字 ——
      审阅要看的是「这一条依据哪份文件」，不是把整篇文档塞进一次响应。
    """
    slides = [s for s in (state.get("slides") or []) if isinstance(s, dict)]
    evidence = state.get("evidence") or {}
    used = {eid for s in slides
            for c in [*(s.get("claims") or []), *(s.get("notes") or [])]
            if isinstance(c, dict)
            for eid in (c.get("evidence_ids") or []) if isinstance(eid, str)}
    used.update(data["material_id"] for s in slides for data in (s.get("chart"), s.get("table"))
                if data and data.get("material_id"))
    pages = []
    for s in slides:
        def pick(key, _s=s):
            return [{k: c.get(k) for k in ("text", "evidence_ids", "gap", "column", "kind")}
                    for c in (_s.get(key) or []) if isinstance(c, dict)]
        pages.append({"kind": s.get("kind"), "title": s.get("title") or "",
                      "slide_id": s.get("slide_id"), "purpose": s.get("purpose"),
                      "speaker_notes": s.get('speaker_notes') or '',
                      "chart": s.get("chart"), "table": s.get("table"),
                      "subtitle": s.get("subtitle") or "", "items": s.get("items") or [],
                      "left_title": s.get("left_title") or "",
                      "right_title": s.get("right_title") or "",
                      "claims": pick("claims"), "notes": pick("notes")})
    return {
        "slides": pages,
        "sources": {eid: {"title": evidence[eid].get("title") or "",
                          "heading_path": evidence[eid].get("heading_path") or [],
                          "line": evidence[eid].get("line"),
                          "scope": evidence[eid].get("scope"), "date": evidence[eid].get("date"),
                          "url": evidence[eid].get("url"),
                          "text": (evidence[eid].get("text") or "")[:400]}
                    for eid in sorted(used) if isinstance(evidence.get(eid), dict)},
    }


@router.get("/ppt/task/{task_id}")
def task_status(task_id: str, user: User = Depends(require_auth)):
    try:
        state = ppt_tool.owned_state(task_id, user.id)
    except ppt_tool.TaskCorrupt:
        return JSONResponse(status_code=409,
                            content={"error": "state_corrupt", "recoverable": True})
    return _status(state)


def _status(state):
    from agent.tools.create_ppt.scripts import chat_history
    chat_history.ensure_history(state)
    task_id = state["task_id"]
    usage = [item for item in (state.get('master') or {}).get('usage', []) if isinstance(item, dict)]
    # 新记录已由 llm_api_client 归一化；旧任务保留 prompt/completion 回退，避免历史数据变 0。
    input_tokens = sum(item.get('input_tokens', item.get('prompt_tokens', 0)) or 0 for item in usage)
    output_tokens = sum(item.get('output_tokens', item.get('completion_tokens', 0)) or 0 for item in usage)
    cached_tokens = sum(item.get('cached_tokens') or 0 for item in usage)
    reasoning_tokens = sum(item.get('reasoning_tokens') or 0 for item in usage)
    body = {"task_id": task_id, "stage": state.get("stage"),
            "background_running": background.running(task_id),
            "pause_requested": background.pausing(task_id) or bool(state.get('pause_requested')),
            "can_pause": background.running(task_id) and state.get('stage') in {'starting', 'generating', 'master_continue'}
                and not background.pausing(task_id),
            "background_error": state.get('background_error'),
            "can_continue": workflow.can_continue(state) and not background.running(task_id)
                and task_id not in workflow._ACTIVE_TASKS,
            "title": state.get('title') or (state.get('brief') or {}).get('goal') or '未命名演示文稿',
            "renderer": state.get('renderer') or (state.get('artifacts') or {}).get('renderer'),
            "workflow_version": state.get("workflow_version", 1),
            "revision": state.get("revision", 0), "brief": state.get("brief"),
            "materials": [{k: m.get(k) for k in ("material_id", "title", "source_type", "locator", "date", "scope", "columns", "units", "url", "final_url", "accessed_at", "published_at")}
                          | {"row_count": len(m.get("rows") or [])} for m in state.get("materials") or []],
            "analyses": state.get("analyses") or [], "stale_slide_ids": state.get("stale_slide_ids") or [],
            "issues": state.get("errors") or [], "revision_error": state.get("revision_error"),
            "pending_user": state.get("pending_user"),
            "chat_messages": state['chat_messages'], "outline": chat_history.outline(state),
            "selected_slide_id": state.get('selected_slide_id'),
            "pages": (state.get("artifacts") or {}).get("pages", 0),
            "progress": {"tool_calls": len(state.get("tool_calls") or []),
                         "label": state.get('activity') or '',
                         "model_requests": (state.get('master') or {}).get('api_requests', 0),
                         "input_tokens": input_tokens, "output_tokens": output_tokens,
                         "total_tokens": input_tokens + output_tokens,
                         "cached_tokens": cached_tokens, "reasoning_tokens": reasoning_tokens,
                         "open": sum(not x.get("covered") for x in
                                     (state.get("coverage") or {}).values())}}
    from agent.tools.create_ppt.scripts import live_preview
    body.update(live_preview.snapshot(state))
    # 逐页内容只在「已生成」之后才有意义（草稿与已确认两种态都带上）。
    if state.get("artifacts") or state.get("stage") in ("draft", "ready", "materials_changed"):
        body.update(_review(state))
        if (state.get("artifacts") or {}).get("previews"):
            body["previews"] = {s["slide_id"]: "/api/ppt/task/%s/preview/%d/%s" %
                 (task_id, state["revision"], s["slide_id"]) for s in state.get("slides") or []}
    if state.get('chat_workspace'):
        used = {eid for slide in state.get('slides') or []
                for claim in [*(slide.get('claims') or []), *(slide.get('notes') or [])]
                for eid in claim.get('evidence_ids') or []}
        body['sources'] = {eid: {key: source.get(key) for key in (
            'title', 'heading_path', 'scope', 'date', 'url', 'final_url', 'accessed_at',
            'published_at', 'content_scope', 'reading_status', 'full_paper_verified')}
            | {'text': (source.get('text') or '')[:800], 'used': eid in used}
            for eid, source in (state.get('evidence') or {}).items()}
        body['issues'] = [{'detail': issue.get('user_message') or
            '本轮制作未完成，已保存的资料与页面仍保留；可以继续制作或补充要求。'}
            for issue in state.get('errors') or []]
    return body


@router.post("/ppt/task/{task_id}/resume")
async def resume(task_id: str, user: User = Depends(require_auth)):
    try:
        state = ppt_tool.owned_state(task_id, user.id)
    except ppt_tool.TaskCorrupt:
        return JSONResponse(status_code=409,
                            content={"error": "state_corrupt", "recoverable": True})
    if state.get("stage") == "await_clarify":
        raise ApiError(409, "请在聊天框选择继续制作 PPT 并回答澄清问题")
    if not state.get("next_node"):
        raise ApiError(409, "任务已结束，不能续跑")
    # 阶段边界续跑；返回新的事件流，前端可沿用聊天 SSE 消费方式。
    from fastapi.responses import StreamingResponse
    import json

    async def events():
        try:
            async for kind, data in ppt_tool.run_stream(
                    [], "", task_id=task_id, conversation_id=state.get("conversation_id"),
                    owner_id=user.id, resume_stage=state["next_node"]):
                payload = data if kind == "ppt" else {"content": data} if kind == "delta" else None
                if payload is not None:
                    yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        except ppt_tool.StateCorrupt:
            yield 'data: {"ppt_error":{"stage":"resume","error":"state_corrupt","recoverable":true}}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/ppt/download/{token}")
def download(token: str, user: User = Depends(require_auth)):
    path = ppt_tool.consume(token, user.id)
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


@router.post("/ppt/task/{task_id}/download-token")
def refresh_download_token(task_id: str, user: User = Depends(require_auth)):
    try:
        state = ppt_tool.owned_state(task_id, user.id)
    except ppt_tool.TaskCorrupt:
        return JSONResponse(status_code=409,
                            content={"error": "state_corrupt", "recoverable": True})
    token = ppt_tool.issue_token(task_id, user.id)
    return {"task_id": task_id, "download_url": "/api/ppt/download/" + token,
            "filename": task_id + ".pptx",
            "pages": (state.get("artifacts") or {}).get("pages", 0)}


@router.post("/ppt/task/{task_id}/confirm")
def confirm(task_id: str, user: User = Depends(require_auth), body: dict | None = Body(default=None)):
    """**人工确认位** —— 用户看过草稿后确认；**只有到这里**才可能拿到下载地址。"""
    try:
        current = ppt_tool.owned_state(task_id, user.id)
        expected = (body or {}).get("expected_revision")
        if current.get("workflow_version") == 2 and expected is None:
            raise ApiError(409, "请确认当前草稿版本")
        state, token = ppt_tool.confirm(task_id, user.id, expected_revision=expected)
    except ppt_tool.TaskCorrupt:
        return JSONResponse(status_code=409,
                            content={"error": "state_corrupt", "recoverable": True})
    return {"task_id": task_id, "stage": "ready", "revision": state.get("revision", 0), "download_url": "/api/ppt/download/" + token,
            "filename": task_id + ".pptx",
            "pages": (state.get("artifacts") or {}).get("pages", 0)}


class CreateTaskBody(BaseModel):
    brief: workflow.Brief
    materials: list[Material] = Field(default_factory=list, max_length=20)
    conversation_id: int | None = None


class GenerateBody(BaseModel):
    expected_revision: int = Field(ge=0)


class RenameTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch('/ppt/task/{task_id}/title')
def rename_task(task_id: str, body: RenameTaskBody, user: User = Depends(require_auth)):
    from agent.tools.create_ppt.scripts import task_management
    return _status(task_management.rename(task_id, user.id, body.title))


@router.delete('/ppt/task/{task_id}')
def delete_task(task_id: str, user: User = Depends(require_auth)):
    from agent.tools.create_ppt.scripts import task_management
    return task_management.remove(task_id, user.id)


class CreateChatBody(BaseModel):
    message: str = Field(default='', max_length=12000)


class ChatAttachment(BaseModel):
    material_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default='', max_length=300)


class ChatMessageBody(GenerateBody):
    message: str = Field(default='', max_length=12000)
    selected_slide_id: str | None = Field(default=None, max_length=64)
    confirmation_id: str | None = Field(default=None, max_length=64)
    approved: bool | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=20)


class SlideSelectionBody(BaseModel):
    slide_id: str | None = Field(default=None, max_length=64)


@router.post('/ppt/chat')
def create_chat(body: CreateChatBody, user: User = Depends(require_auth)):
    return _status(workflow.create_chat(body.message, user.id))


@router.post('/ppt/task/{task_id}/messages', status_code=202)
async def chat_message(task_id: str, body: ChatMessageBody,
                       user: User = Depends(require_auth)):
    try:
        return _status(background.start(workflow.message, task_id, user.id,
            body.expected_revision, body.message, body.selected_slide_id,
            body.confirmation_id, body.approved, [item.model_dump() for item in body.attachments]))
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.patch('/ppt/task/{task_id}/selection')
def select_slide(task_id: str, body: SlideSelectionBody, user: User = Depends(require_auth)):
    return _status(workflow.select_slide(task_id, user.id, body.slide_id))


class BriefBody(GenerateBody):
    brief: workflow.Brief


class DesignAnswerBody(GenerateBody):
    confirmation_id: str = Field(min_length=1, max_length=64)
    approved: bool
    feedback: str = Field(default='', max_length=6000)


class ReviseBody(GenerateBody):
    feedback: str = Field(min_length=1, max_length=3000)
    target_ids: list[str] | None = Field(default=None, max_length=20)
    action: Literal["replace", "split", "append"] = "replace"


class AnalysisBody(BaseModel):
    material_id: str
    operation: Literal["count", "sum", "mean", "share"]
    field: str | None = None
    group_by: str | None = None
    denominator_field: str | None = None
    denominator_aggregation: Literal["sum", "single"] | None = None
    filters: dict = Field(default_factory=dict)
    unit: str = ""
    chart_type: Literal["bar", "line"] = "bar"


class PublicSourceBody(BaseModel):
    url: str = Field(max_length=2000)
    question: str = Field(min_length=1, max_length=1000)
    school_policy: bool = False


@router.post("/ppt/task/{task_id}/public-candidates")
def add_public_candidate(task_id: str, body: PublicSourceBody, user: User = Depends(require_auth)):
    state = ppt_tool.owned_state(task_id, user.id)
    brief = dict(state["brief"])
    if brief.get("material_policy") == "provided_only":
        raise ApiError(409, "本任务限定仅用提供材料")
    brief["public_candidates"] = [*brief.get("public_candidates", []), body.model_dump()]
    try:
        return _status(workflow.update_brief(task_id, user.id, state.get("revision", 0), brief))
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.post("/ppt/tasks")
def create_task(body: CreateTaskBody, user: User = Depends(require_auth), db=Depends(get_db)):
    if body.conversation_id is not None:
        conv = db.query(Conversation).filter(Conversation.id == body.conversation_id, Conversation.user_id == user.id).first()
        if not conv:
            raise ApiError(404, "会话不存在")
    try:
        return _status(workflow.create(body.brief.model_dump(), [m.model_dump() for m in body.materials],
                                        user.id, body.conversation_id))
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.post("/ppt/task/{task_id}/materials")
async def upload_materials(task_id: str, request: Request, files: list[UploadFile] = File(...),
                           replace_id: str | None = Form(default=None), user: User = Depends(require_auth)):
    ppt_tool.owned_state(task_id, user.id)
    contents, size = [], 0
    for upload in files:
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        size += len(data)
        if size > 40 * 1024 * 1024:
            raise ApiError(400, "一次上传总大小超过40MiB")
        contents.append((upload.filename or "", data))
    def execute():
        parsed = []
        try:
            from pathlib import Path
            asset_dir = Path(subgraph._workdir()) / task_id / 'uploads'
            for name, data in contents:
                parsed.extend(read_materials(name, data, asset_dir=asset_dir))
            return _status(workflow.add_materials(task_id, user.id, parsed, replace_id))
        except (ValueError, OSError) as error:
            for material in parsed:
                if material.asset_file:
                    (asset_dir / material.asset_file).unlink(missing_ok=True)
            raise ApiError(400, str(error)) from None
    return await run_request(execute, request)


@router.patch("/ppt/task/{task_id}/brief")
def update_brief(task_id: str, body: BriefBody, user: User = Depends(require_auth)):
    try:
        return _status(workflow.update_brief(task_id, user.id, body.expected_revision, body.brief.model_dump()))
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.post("/ppt/task/{task_id}/materials/text")
def add_text_material(task_id: str, body: Material, user: User = Depends(require_auth)):
    return _status(workflow.add_materials(task_id, user.id, [body]))


@router.post("/ppt/task/{task_id}/analysis")
def add_analysis(task_id: str, body: AnalysisBody, user: User = Depends(require_auth)):
    try:
        return workflow.add_analysis(task_id, user.id, body.material_id, body.model_dump(exclude={"material_id"}))
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.post("/ppt/task/{task_id}/public-source")
async def add_public_source(task_id: str, body: PublicSourceBody, request: Request,
                            user: User = Depends(require_auth)):
    from agent.tools.create_ppt.scripts.public_materials import read_public_material
    state = ppt_tool.owned_state(task_id, user.id)
    policy = (state.get("brief") or {}).get("material_policy", "provided_only")
    if policy == "provided_only":
        raise ApiError(409, "本任务限定仅用提供材料，不允许联网补充")
    def execute():
        try:
            material = read_public_material(body.url, body.question,
                                             school_policy=body.school_policy or policy == "campus")
            return _status(workflow.add_materials(task_id, user.id, [material]))
        except (ValueError, OSError) as error:
            raise ApiError(400, str(error)) from None
    return await run_request(execute, request)


@router.post("/ppt/task/{task_id}/generate", status_code=202)
async def generate_task(task_id: str, body: GenerateBody, user: User = Depends(require_auth)):
    return _status(background.start(workflow.generate, task_id, user.id, body.expected_revision))


@router.post("/ppt/task/{task_id}/revise", status_code=202)
async def revise_task(task_id: str, body: ReviseBody, user: User = Depends(require_auth)):
    try:
        return _status(background.start(workflow.revise, task_id, user.id, body.expected_revision,
                                        body.feedback, body.target_ids, body.action))
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.post('/ppt/task/{task_id}/continue', status_code=202)
async def continue_master(task_id: str, body: GenerateBody, user: User = Depends(require_auth)):
    return _status(background.start(workflow.continue_master, task_id, user.id, body.expected_revision,
                                    manual=True))


@router.post('/ppt/task/{task_id}/pause', status_code=202)
def pause_task(task_id: str, body: GenerateBody, user: User = Depends(require_auth)):
    return _status(background.pause(task_id, user.id, body.expected_revision))


@router.post('/ppt/task/{task_id}/design-answer', status_code=202)
async def answer_design(task_id: str, body: DesignAnswerBody, user: User = Depends(require_auth)):
    return _status(background.start(workflow.continue_master, task_id, user.id,
                   body.expected_revision, body.model_dump(exclude={'expected_revision'})))


@router.get("/ppt/task/{task_id}/preview/{revision}/{slide_id}")
def preview(task_id: str, revision: int, slide_id: str, user: User = Depends(require_auth)):
    from pathlib import Path
    from agent.tools.create_ppt.scripts.subgraph import _workdir
    state = ppt_tool.owned_state(task_id, user.id)
    if state.get("revision") != revision or slide_id not in {s.get("slide_id") for s in state.get("slides") or []}:
        raise ApiError(404, "页面预览不存在")
    root = Path(_workdir()).resolve()
    path = (root / task_id / ("v%d" % revision) / "previews" / (slide_id + ".png")).resolve()
    if not path.is_relative_to(root / task_id) or str(path) not in [str(Path(p).resolve()) for p in (state.get("artifacts") or {}).get("previews", [])] or not path.is_file():
        raise ApiError(404, "页面预览不存在")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get('/ppt/task/{task_id}/live-preview/{filename}')
def live_preview(task_id: str, filename: str, user: User = Depends(require_auth)):
    from agent.tools.create_ppt.scripts.live_preview import svg
    import xml.etree.ElementTree as ET
    state = ppt_tool.owned_state(task_id, user.id)
    try:
        content = svg(state, filename)
    except (ValueError, OSError, UnicodeError, ET.ParseError):
        raise ApiError(409, '当前页尚未保存完整或含不可预览的资源，请等待下一次保存') from None
    return Response(content, media_type='image/svg+xml', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"})


class PageTextEdit(BaseModel):
    id: str = Field(min_length=1, max_length=16)
    text: str = Field(max_length=400)
    original: str | None = Field(default=None, max_length=400)


class PageTextsBody(BaseModel):
    expected_revision: int = Field(ge=0)
    edits: list[PageTextEdit] = Field(min_length=1, max_length=60)


class ReorderBody(BaseModel):
    expected_revision: int = Field(ge=0)
    order: list[str] = Field(min_length=3, max_length=20)


def _page(state, slide_id):
    slide = next((item for item in state.get('slides') or [] if item['slide_id'] == slide_id), None)
    if slide is None:
        raise ApiError(404, '页面不存在')
    return slide


@router.get('/ppt/task/{task_id}/page/{slide_id}/texts')
def page_texts(task_id: str, slide_id: str, user: User = Depends(require_auth)):
    """返回该页真实 SVG 里的可编辑文本，供工作区直接改字。"""
    from agent.tools.create_ppt.scripts import page_edit
    state = ppt_tool.owned_state(task_id, user.id)
    if (state.get('master') or {}).get('project') is None:
        raise ApiError(409, '该任务没有可编辑的PPT Master工程')
    slide = _page(state, slide_id)
    return {'slide_id': slide_id, 'title': slide.get('title') or '',
            'texts': page_edit.read_page_texts(state['master']['project'], slide['master_file'])}


@router.post('/ppt/task/{task_id}/page/{slide_id}/texts')
async def save_page_texts(task_id: str, slide_id: str, body: PageTextsBody, request: Request,
                          user: User = Depends(require_auth)):
    def execute():
        return _status(workflow.edit_page_texts(task_id, user.id, body.expected_revision, slide_id,
                                                [edit.model_dump() for edit in body.edits]))
    try:
        return await run_request(execute, request)
    except ValueError as error:
        raise ApiError(400, str(error)) from None


@router.post('/ppt/task/{task_id}/order')
async def reorder_pages(task_id: str, body: ReorderBody, request: Request,
                        user: User = Depends(require_auth)):
    def execute():
        return _status(workflow.reorder_pages(task_id, user.id, body.expected_revision, body.order))
    try:
        return await run_request(execute, request)
    except ValueError as error:
        raise ApiError(400, str(error)) from None
