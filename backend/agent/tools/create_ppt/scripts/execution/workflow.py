"""PPT product operations backed by an external Skill and the shared Agent loop."""

from functools import wraps
from contextvars import ContextVar
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.core.request_control import check_cancelled
from core.errors import ApiError
from agent.tools.create_ppt.scripts.content.analysis import analyse
from agent.tools.create_ppt.scripts.content.materials import Material


class PublicCandidate(BaseModel):
    url: str = Field(max_length=2000)
    question: str = Field(min_length=1, max_length=1000)
    school_policy: bool = False


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=2000)
    audience: str = Field(min_length=1, max_length=300)
    outcome: str = ""
    page_count: int | None = Field(default=None, ge=3, le=20)
    duration_minutes: int | None = Field(default=None, ge=1, le=120)
    must_keep: list[str] = Field(default_factory=list)
    style: str = "清晰、简洁的中文演示"
    material_policy: str = "general"
    public_candidates: list[PublicCandidate] = Field(default_factory=list, max_length=5)


_ACTIVE_TASKS = set()
_TASK_CONTEXT = ContextVar('ppt_task_execution', default=None)


def _exclusive(operation):
    @wraps(operation)
    def execute(task_id, *args, **kwargs):
        if _TASK_CONTEXT.get() == task_id:
            return operation(task_id, *args, **kwargs)
        from agent.tools.create_ppt.scripts.rendering import delivery
        with delivery._LOCK:
            if task_id in _ACTIVE_TASKS:
                raise ApiError(409, "该任务正在制作，请等待本轮结束")
            _ACTIVE_TASKS.add(task_id)
        token = _TASK_CONTEXT.set(task_id)
        try:
            return operation(task_id, *args, **kwargs)
        finally:
            _TASK_CONTEXT.reset(token)
            with delivery._LOCK:
                _ACTIVE_TASKS.discard(task_id)
    return execute


def _owned(task_id, owner_id):
    from agent.tools.create_ppt.scripts.rendering import delivery
    return delivery.owned_state(task_id, owner_id)


def _save(state, *, selection_update=False):
    from agent.tools.create_ppt.scripts.execution.subgraph import save_state
    save_state(state["task_id"], state, selection_update=selection_update)


def can_continue(state):
    if state.get('renderer') != 'ppt-master':
        return False
    return state.get('stage') in {'paused', 'master_continue'} or (
        state.get('stage') == 'failed' and bool(state.get('master')))


def create_chat(message, owner_id):
    from agent.tools.create_ppt.scripts.execution.chat_history import record
    content = message.strip()
    state = create({'goal': content[:2000] or '未命名演示文稿', 'audience': '一般听众'}, [], owner_id)
    state.update(chat_workspace=True, chat_messages=[], selected_slide_id=None)
    if content:
        record(state, 'user', content)
        state['pending_chat_message'] = content
    _save(state)
    return state


def select_slide(task_id, owner_id, slide_id):
    from agent.tools.create_ppt.scripts.execution.subgraph import _STATE_LOCK
    with _STATE_LOCK:
        state = _owned(task_id, owner_id)
        if slide_id is not None and slide_id not in {page['slide_id'] for page in state.get('slides') or []}:
            raise ApiError(409, '当前讨论页不属于已保存的作品，请刷新后选择')
        state['selected_slide_id'] = slide_id
        _save(state, selection_update=True)
        return state


@_exclusive
def message(task_id, owner_id, expected_revision, content, selected_slide_id=None,
            confirmation_id=None, approved=None, attachments=None):
    from agent.tools.create_ppt.scripts.execution import chat_history
    from agent.tools.create_ppt.scripts.master.master_host import advance, confirmation_action, validate_confirmation
    state = _owned(task_id, owner_id)
    if state.get('renderer') != 'ppt-master':
        raise ApiError(409, '该历史任务请保留成品，另建聊天制作任务')
    if state.get('revision', 0) != expected_revision:
        raise ApiError(409, '作品版本已变化，请刷新后发送')
    if state.get('stage') == 'generating':
        raise ApiError(409, '本轮制作仍在进行，请等待保存后再发送')
    pending = state.get('pending_user') or {}
    if confirmation_id is not None and (state.get('stage') != 'await_design' or
            confirmation_id != pending.get('confirmation_id')):
        raise ApiError(409, '待确认方案已变化，请查看最新内容后继续')
    known_pages = {page['slide_id'] for page in state.get('slides') or []}
    if selected_slide_id is not None and selected_slide_id not in known_pages:
        raise ApiError(409, '当前讨论页已变化，请重新选择')
    is_confirmation = confirmation_id is not None and approved is not None
    if is_confirmation:
        validate_confirmation(state, confirmation_id, approved)
    text = content.strip()
    if is_confirmation and approved:
        action_message = confirmation_action(pending['stage'])['action_message']
        text = action_message + ('\n' + text if text and text != action_message else '')
    if not text:
        raise ValueError('请描述制作需求、修改意见或补充信息')
    chat_history.ensure_history(state)
    by_id = {material['material_id']: material for material in state.get('materials') or []}
    shown = {item['material_id'] for entry in state['chat_messages'] for item in entry.get('attachments') or []}
    requested = [item['material_id'] for item in attachments] if attachments else [mid for mid in by_id if mid not in shown]
    if any(mid not in by_id for mid in requested):
        raise ValueError('附件不属于当前制作任务')
    actual_attachments = [{'material_id': mid, 'title': by_id[mid]['title']} for mid in dict.fromkeys(requested)]
    if state.pop('pending_chat_message', None) != text:
        chat_history.record(state, 'user', text, attachments=actual_attachments,
                            selected_slide_id=selected_slide_id)
    elif state['chat_messages']:
        state['chat_messages'][-1].update(attachments=actual_attachments, selected_slide_id=selected_slide_id)
    state.update(chat_workspace=True, selected_slide_id=selected_slide_id, last_user_message=text)
    previous_stage = state.get('stage')
    if state.get('artifacts') and previous_stage in {'draft', 'ready', 'materials_changed'}:
        state.update(revision_pending=True, return_stage=previous_stage)
    if state.get('master'):
        from agent.tools.create_ppt.scripts.master.master_workspace import MasterWorkspace
        MasterWorkspace(state['master']['project']).import_materials(state)
        if not is_confirmation:
            state['master'].pop('proposal_stage', None)
            note = ('当前消息正在讨论第%d页（%s），这是理解请求的上下文，不限制用户明确要求的整份修改。' %
                    (next(i for i, page in enumerate(state['slides'], 1) if page['slide_id'] == selected_slide_id), selected_slide_id)
                    if selected_slide_id else '当前消息讨论整份演示文稿，没有指定页面。')
            state['master']['messages'].append({'role': 'user', 'content': text + '\n' + note + (
                '\n本条消息附有资料：' + '、'.join(item['title'] for item in actual_attachments) if actual_attachments else '')})
            state['pending_user'] = None
    if not is_confirmation:
        state['stage'] = 'materials_changed' if previous_stage == 'materials_changed' and not state.get('artifacts') else 'generating'
    _save(state, selection_update=True)
    confirmation = ({'confirmation_id': confirmation_id, 'approved': approved, 'feedback': text}
                    if is_confirmation else None)
    return advance(state, confirmation=confirmation)


def create(brief: dict, materials: list[dict], owner_id: int, conversation_id=None):
    from agent.tools.create_ppt.scripts.rendering import delivery
    from agent.tools.create_ppt.scripts.execution.subgraph import _budget_init
    normalized = Brief.model_validate(brief)
    if not normalized.goal.strip() or not normalized.audience.strip():
        raise ValueError("请补充制作目标和受众")
    if normalized.material_policy not in {"general", "provided_only", "campus", "public"}:
        raise ValueError("材料范围无效")
    parsed = [Material.model_validate(item) for item in materials]
    if len({m.material_id for m in parsed}) != len(parsed):
        raise ValueError("材料标识重复")
    tid = delivery.reserve_task(owner_id, conversation_id)
    state = _owned(tid, owner_id)
    state.update(workflow_version=2, renderer='ppt-master', revision=0, brief=normalized.model_dump(),
                 materials=[m.model_dump() for m in parsed], analyses=[],
                 evidence={m.material_id: m.evidence() for m in parsed},
                 stage="await_materials", next_node="plan", request_raw=normalized.goal,
                 budget=_budget_init())
    _save(state)
    return state


def add_materials(task_id, owner_id, materials: list[Material], replace_id=None):
    from agent.tools.create_ppt.scripts.rendering import delivery
    check_cancelled()
    with delivery._LOCK:
        state = _owned(task_id, owner_id)
        if state.get("stage") in {"generating", "master_continue"}:
            raise ApiError(409, "任务正在制作，不能同时更换材料")
        current = list(state.get("materials") or [])
        stale = set(state.get("stale_slide_ids") or [])
        if replace_id:
            if len(materials) != 1 or not any(m["material_id"] == replace_id for m in current):
                raise ValueError("替换材料须对应一个已有材料")
            materials = [materials[0].model_copy(update={"material_id": replace_id})]
            current = [m for m in current if m["material_id"] != replace_id]
            for slide in state.get("slides") or []:
                ids = {eid for c in [*slide.get("claims", []), *slide.get("notes", [])] for eid in c.get("evidence_ids", [])}
                ids.update(d.get("material_id") for d in (slide.get("chart"), slide.get("table")) if d)
                if replace_id in ids:
                    stale.add(slide["slide_id"])
            for result in state.get("analyses") or []:
                if result["material_id"] == replace_id:
                    fresh = analyse(materials[0], result)
                    fresh["analysis_id"] = result["analysis_id"]
                    result.update(fresh)
        if len(current) + len(materials) > 20:
            raise ValueError("单任务最多20份材料，请选定相关材料")
        state["materials"] = current + [m.model_dump() for m in materials]
        state.setdefault("evidence", {}).update({m.material_id: m.evidence() for m in materials})
        state["stale_slide_ids"] = sorted(stale)
        if state.get('renderer') == 'ppt-master':
            state.update(pending_user=None, stage='materials_changed' if state.get('slides') else 'await_materials')
            state.pop('download', None)
        if stale:
            state["stage"] = "materials_changed"
            state.pop("download", None)
        _save(state)
        return state


def add_analysis(task_id, owner_id, material_id, spec):
    from agent.tools.create_ppt.scripts.rendering import delivery
    with delivery._LOCK:
        state = _owned(task_id, owner_id)
        if state.get("stage") in {"generating", "master_continue"}:
            raise ApiError(409, "任务正在制作")
        material = next((m for m in state.get("materials", []) if m["material_id"] == material_id), None)
        if not material:
            raise ValueError("数据材料不属于当前任务")
        result = analyse(Material.model_validate(material), spec)
        state.setdefault("analyses", []).append(result)
        if state.get('renderer') == 'ppt-master':
            state.update(pending_user=None, stage='materials_changed' if state.get('slides') else 'await_materials')
            state.pop('download', None)
        _save(state)
        return result


def update_brief(task_id, owner_id, expected_revision, brief):
    from agent.tools.create_ppt.scripts.rendering import delivery
    normalized = Brief.model_validate(brief)
    if not normalized.goal.strip() or not normalized.audience.strip() or normalized.material_policy not in {"general", "provided_only", "campus", "public"}:
        raise ValueError("制作目标、受众或材料范围无效")
    with delivery._LOCK:
        state = _owned(task_id, owner_id)
        if state.get("revision", 0) != expected_revision or state.get("stage") in {"generating", "master_continue"}:
            raise ApiError(409, "任务版本已变化，请刷新后修改简报")
        if state.get("brief") != normalized.model_dump():
            state.update(brief=normalized.model_dump(), request_raw=normalized.goal,
                         stage="brief_changed", next_node="plan")
            state.pop("download", None)
            state.pop("effective_page_count", None)
            state['pending_user'] = None
            _save(state)
        return state


def _begin(task_id, owner_id, expected_revision):
    from agent.tools.create_ppt.scripts.rendering import delivery
    with delivery._LOCK:
        state = _owned(task_id, owner_id)
        if state.get("revision", 0) != expected_revision or state.get("stage") in {"generating", "master_continue"}:
            raise ApiError(409, "草稿版本已变化或正在制作，请刷新后再操作")
        if not state.get("workflow_version"):
            raise ApiError(409, "旧任务请重新创建后使用材料和页级修订功能")
        state["stage"] = "generating"
        state.pop("download", None)
        _save(state)
        return state


@_exclusive
def generate(task_id, owner_id, expected_revision):
    if _owned(task_id, owner_id).get('renderer') != 'ppt-master':
        raise ApiError(409, '旧渲染任务请新建后使用外挂Skill')
    state = _begin(task_id, owner_id, expected_revision)
    if not state.get('materials') and state['brief']['material_policy'] == 'provided_only':
        state.update(stage='await_materials', pending_user={'question': '请添加要制作的材料'})
        _save(state)
        return state
    from agent.tools.create_ppt.scripts.master.master_host import advance
    state.pop('master', None)
    return advance(state)


@_exclusive
def continue_master(task_id, owner_id, expected_revision, confirmation=None, *, manual=False):
    from agent.tools.create_ppt.scripts.master.master_host import advance, validate_confirmation
    from agent.tools.create_ppt.scripts.rendering import delivery
    with delivery._LOCK:
        state = _owned(task_id, owner_id)
        if state.get('renderer') != 'ppt-master':
            raise ApiError(409, '该任务没有可继续的PPT Master工作流')
        resumable_failure = manual and state.get('stage') == 'failed' and bool(state.get('master'))
        if state.get('revision', 0) != expected_revision or (
                state.get('stage') not in {'paused', 'master_continue', 'await_design'} and not resumable_failure):
            raise ApiError(409, '任务状态已变化，请刷新后操作')
        waiting = state['stage']
        if waiting == 'await_design' and confirmation is None:
            raise ApiError(409, '请先提交方案确认或补充意见')
        if confirmation is not None and (waiting != 'await_design' or
                confirmation['confirmation_id'] != (state.get('pending_user') or {}).get('confirmation_id')):
            raise ApiError(409, '策划确认已变化，请刷新当前任务')
        if confirmation is not None:
            validate_confirmation(state, confirmation['confirmation_id'], confirmation['approved'])
        state['stage'] = 'generating'
        state['activity'] = '正在核对已保存进度，继续尚未完成的制作'
        _save(state)
    if confirmation is not None:
        state['stage'] = waiting
    return advance(state, confirmation=confirmation)


@_exclusive
def revise(task_id, owner_id, expected_revision, feedback, target_ids=None, action='replace'):
    previous = _owned(task_id, owner_id)
    if previous.get('revision', 0) != expected_revision:
        raise ApiError(409, '草稿版本已变化，请刷新后再修改')
    if previous.get('renderer') != 'ppt-master' or previous.get('stage') not in {'draft', 'ready', 'materials_changed'}:
        raise ApiError(409, '请先生成PPT Master草稿')
    if not feedback.strip():
        raise ValueError('请输入修改要求')
    if action not in {'replace', 'split', 'append'}:
        raise ValueError('整体目标或受众变化请更新简报后重新生成')
    known = {slide['slide_id'] for slide in previous['slides']}
    target_ids = list(target_ids or [])
    if not target_ids and action != 'append':
        raise ValueError('请在工作区选择要修改的页面')
    if len(set(target_ids)) != len(target_ids) or any(sid not in known for sid in target_ids):
        raise ValueError('目标页面不属于当前草稿')
    if action == 'split' and len(target_ids) != 1:
        raise ValueError('一次拆分一页')
    if not set(previous.get('stale_slide_ids') or []).issubset(target_ids):
        raise ValueError('材料已更新，请一并更新引用该材料的页面')
    state = _begin(task_id, owner_id, expected_revision)
    from agent.tools.create_ppt.scripts.master.master_host import start, advance
    try:
        start(state, revision={'feedback': feedback, 'target_ids': target_ids, 'action': action})
        state['last_revision'] = {'feedback': feedback, 'target_ids': target_ids, 'action': action}
        return advance(state)
    except BaseException:
        # 原地修订失败时按修订前快照还原页面，成品不留在半改状态
        from agent.tools.create_ppt.scripts.master.master_host import _restore_snapshot
        session = previous.get('master') or {}
        if session.get('previous_snapshot') is not None and session.get('project'):
            _restore_snapshot(session['project'], session['previous_snapshot'])
        previous.pop('download', None)
        previous['revision_error'] = '本次修订未完成，页面已还原'
        _save(previous)
        raise


def _editable(state, task_id):
    if state.get('renderer') != 'ppt-master':
        raise ApiError(409, '该任务不支持在页面上直接编辑')
    from agent.tools.create_ppt.scripts.execution import background
    if state.get('stage') in {'generating', 'starting'} or background.running(task_id):
        raise ApiError(409, '当前任务正在制作，请等本轮结束后再编辑')
    if not state.get('slides'):
        raise ApiError(409, '当前还没有可编辑的页面，请先生成PPT')


@_exclusive
def edit_page_texts(task_id, owner_id, expected_revision, slide_id, edits):
    """直接改写指定页的真实 SVG 文本，不调用模型。"""
    from agent.tools.create_ppt.scripts.rendering import page_edit
    state = _owned(task_id, owner_id)
    if state.get('revision', 0) != expected_revision:
        raise ApiError(409, '作品版本已变化，请刷新后重试')
    _editable(state, task_id)
    slide = next((item for item in state['slides'] if item['slide_id'] == slide_id), None)
    if slide is None:
        raise ApiError(404, '页面不存在')
    path = Path(state['master']['project']) / 'svg_output' / slide['master_file']
    original = path.read_bytes()
    try:
        page_edit.apply_page_texts(state['master']['project'], slide['master_file'], edits)
        page_edit.republish(state, state['master'], activity='已按你的修改更新页面文字')
    except BaseException:
        # 改字或重新导出失败时还原页面，保留上一份可下载的成品
        path.write_bytes(original)
        raise
    _save(state)
    return state


@_exclusive
def reorder_pages(task_id, owner_id, expected_revision, slide_ids):
    """按给定顺序重排页面文件，标识与顺序保持一致。"""
    from agent.tools.create_ppt.scripts.rendering import page_edit
    state = _owned(task_id, owner_id)
    if state.get('revision', 0) != expected_revision:
        raise ApiError(409, '作品版本已变化，请刷新后重试')
    _editable(state, task_id)
    moved = page_edit.reorder_pages(state['master']['project'], list(slide_ids), state['slides'])
    if moved['changed']:
        try:
            page_edit.republish(state, state['master'], activity='已按新顺序更新页面')
        except BaseException:
            # 导出失败时把文件名与清单还原，顺序不能停在半途
            page_edit.undo_reorder(state['master']['project'], moved['mapping'])
            raise
        _save(state)
    return state
