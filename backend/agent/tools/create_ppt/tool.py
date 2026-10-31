"""PPT task service facade; no presentation function is offered to the model."""
from agent.tools.create_ppt.scripts.rendering import delivery
from agent.tools.create_ppt.scripts.execution import subgraph


async def run_stream(history, question, *, task_id=None, owner_id=None, **kwargs):
    from agent.core.request_control import run_sync
    from core.errors import ApiError
    from agent.tools.create_ppt.scripts.execution import workflow
    if not task_id:
        raise ApiError(400, '请从演示文稿工作区创建制作任务')
    current = delivery.owned_state(task_id, owner_id)
    if current.get('renderer') != 'ppt-master':
        raise ApiError(409, '旧任务仍可查看和下载；继续制作请新建外挂Skill任务')
    if current.get('stage') in {'await_design', 'draft', 'ready'}:
        result = current
    elif current.get('master') and current.get('stage') in {'paused', 'master_continue'}:
        result = await run_sync(workflow.continue_master, task_id, owner_id, current.get('revision', 0))
    else:
        result = await run_sync(workflow.generate, task_id, owner_id, current.get('revision', 0))
    yield 'ppt', {'ppt_task': {'task_id': task_id, 'stage': result['stage'], 'renderer': 'ppt-master'}}
    yield 'delta', (result.get('pending_user') or {}).get('proposal') or '制作进度已保存，请在演示文稿工作区继续。'
    yield 'done', {'task_id': task_id, 'calls': result.get('tool_calls', []),
                   'contexts': [], 'evidence': result.get('evidence', {})}


save_state = subgraph.save_state
load_state = subgraph.load_state
StateCorrupt = subgraph.StateCorrupt
TaskCorrupt = delivery.TaskCorrupt
owned_state = delivery.owned_state
reserve_task = delivery.reserve_task
issue_token = delivery.issue_token
confirm = delivery.confirm
consume = delivery.consume
