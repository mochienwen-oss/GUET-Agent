"""Task-owned PPT execution, independent of browser requests and navigation."""
import asyncio
import logging
from threading import Event

from agent.core.request_control import request_scope, run_sync
from core.config import settings
from core.errors import ApiError
from . import delivery, subgraph, workflow

_RUNNING = {}
_CANCEL_EVENTS = {}
logger = logging.getLogger('uvicorn.error')


def running(task_id):
    task = _RUNNING.get(task_id)
    return task is not None and not task.done()


def pausing(task_id):
    event = _CANCEL_EVENTS.get(task_id)
    return event is not None and event.is_set()


def pause(task_id, owner_id, expected_revision):
    with delivery._LOCK:
        state = delivery.owned_state(task_id, owner_id)
        if state.get('revision', 0) != expected_revision:
            raise ApiError(409, '作品版本已变化，请刷新后操作')
        if not running(task_id):
            return state
        _CANCEL_EVENTS[task_id].set()
        state['activity'] = '正在暂停，等待当前步骤结束；已有内容会保留'
        subgraph.save_state(task_id, state)
        return state


def start(operation, task_id, owner_id, expected_revision, *args, **kwargs):
    with delivery._LOCK:
        state = delivery.owned_state(task_id, owner_id)
        if state.get('revision', 0) != expected_revision:
            raise ApiError(409, '作品版本已变化，请刷新后操作')
        if running(task_id) or task_id in workflow._ACTIVE_TASKS or state.get('stage') in {'starting', 'generating'}:
            raise ApiError(409, '该任务正在后台制作，请等待本轮结束')
        if operation is workflow.continue_master and kwargs.get('manual') and not workflow.can_continue(state):
            raise ApiError(409, '当前任务没有可继续的已保存制作，请刷新后查看状态')
        workflow._ACTIVE_TASKS.add(task_id)
        try:
            _CANCEL_EVENTS[task_id] = Event()
            state.pop('background_error', None)
            state.pop('pause_requested', None)
            state['activity'] = '已提交后台，正在处理制作需求'
            subgraph.save_state(task_id, state)
            _RUNNING[task_id] = asyncio.create_task(_execute(
                operation, task_id, owner_id, expected_revision, args, kwargs))
        except BaseException:
            _CANCEL_EVENTS.pop(task_id, None)
            workflow._ACTIVE_TASKS.discard(task_id)
            raise
    return state


async def _execute(operation, task_id, owner_id, revision, args, kwargs):
    token = workflow._TASK_CONTEXT.set(task_id)
    stop = _CANCEL_EVENTS[task_id]
    try:
        while not stop.is_set():
            # Each segment has its own deadline, independent of the browser.
            # 一次生成需要连续多轮模型请求，这里按 PPT 自己的墙钟预算放行。
            with request_scope(settings.agent_ppt_timeout_sec, cancel_event=stop):
                state = await run_sync(operation, task_id, owner_id, revision, *args, **kwargs)
            if state.get('stage') != 'master_continue':
                break
            operation, revision, args, kwargs = workflow.continue_master, state.get('revision', 0), (), {}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if stop.is_set():
            return
        logger.warning('ppt_background_failed task=%s kind=%s', task_id, type(error).__name__)
        try:
            state = delivery.owned_state(task_id, owner_id)
            state['background_error'] = (error.message if isinstance(error, ApiError) else
                str(error) if isinstance(error, (ValueError, TimeoutError, InterruptedError)) else
                '本轮后台制作未能完成，已保存的资料与页面仍保留，请稍后继续。')
            if state.get('stage') in {'generating', 'master_continue'}:
                state.update(stage='paused', next_node='master')
            subgraph.save_state(task_id, state)
        except (ApiError, delivery.TaskCorrupt, subgraph.StateCorrupt, OSError):
            logger.warning('ppt_background_state_unavailable task=%s', task_id)
    finally:
        try:
            if stop.is_set():
                with delivery._LOCK:
                    state = delivery.owned_state(task_id, owner_id)
                    if state.get('stage') not in {'draft', 'ready', 'await_design'}:
                        from . import chat_history
                        state.update(stage='paused', next_node='master', pause_requested=True,
                                     activity='已暂停制作，可继续已保存的进度')
                        state.pop('background_error', None)
                        chat_history.ensure_history(state)
                        chat_history.record(state, 'assistant', '已暂停制作，已有资料、文件和调用记录已保留，可以继续已保存的制作。')
                        subgraph.save_state(task_id, state)
                    else:
                        state['activity'] = '当前步骤已结束，请查看已保存的结果'
                        subgraph.save_state(task_id, state)
        finally:
            workflow._TASK_CONTEXT.reset(token)
            with delivery._LOCK:
                workflow._ACTIVE_TASKS.discard(task_id)
                _RUNNING.pop(task_id, None)
                _CANCEL_EVENTS.pop(task_id, None)


async def shutdown():
    tasks = list(_RUNNING.values())
    for event in _CANCEL_EVENTS.values():
        event.set()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
