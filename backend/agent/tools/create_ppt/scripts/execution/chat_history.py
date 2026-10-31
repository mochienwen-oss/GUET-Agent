"""Student-visible task conversation, separate from provider/tool history."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def record(state, role, content, **details):
    message = {'id': uuid4().hex[:16], 'role': role, 'content': content,
               'created_at': datetime.now(timezone.utc).isoformat(), **details}
    state.setdefault('chat_messages', []).append(message)
    return message


def ensure_history(state):
    if 'chat_messages' in state:
        return
    state['chat_messages'] = []
    goal = (state.get('brief') or {}).get('goal') or state.get('request_raw')
    if goal:
        record(state, 'user', goal, id='legacy-request', created_at='', attachments=[
            {'material_id': material['material_id'], 'title': material['title']}
            for material in state.get('materials') or []])
    proposal = (state.get('pending_user') or {}).get('proposal')
    if proposal:
        record(state, 'assistant', proposal, id='legacy-proposal', created_at='')


def outline(state):
    project = (state.get('master') or {}).get('project')
    if project:
        from agent.tools.create_ppt.scripts.execution.subgraph import _workdir
        root = Path(_workdir()).resolve() / state['task_id']
        path = Path(project).resolve() / 'outline.md'
        if path.is_relative_to(root) and path.is_file() and path.stat().st_size <= 200000:
            return path.read_text(encoding='utf-8-sig')
    return state.get('outline') or ''


def api_history(state):
    return [{'role': message['role'], 'content': message['content'] + (
        '\n本条消息附有当前任务材料：' + '、'.join(item['title'] for item in message['attachments'])
        if message.get('attachments') else '')}
        for message in state.get('chat_messages') or [] if message.get('role') in {'user', 'assistant'}]
