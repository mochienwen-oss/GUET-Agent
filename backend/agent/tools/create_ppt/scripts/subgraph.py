"""PPT task state storage; model execution lives in the shared runtime loop."""
import json
import os
from pathlib import Path
import re
from threading import RLock
import time

from core.config import settings

_REQUIRED_KEYS = {'task_id', 'stage', 'next_node', 'request_raw', 'brief', 'plan',
                  'evidence', 'coverage', 'tool_calls', 'budget'}
_STATE_LOCK = RLock()


class StateCorrupt(Exception):
    pass


def _workdir():
    path = Path(settings.agent_ppt_workdir)
    return str(path if path.is_absolute() else Path(__file__).resolve().parents[4] / path)


def _state_path(task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id):
        raise StateCorrupt('任务ID格式无效')
    return str(Path(_workdir()) / (task_id + '.json'))


def save_state(task_id, data, *, selection_update=False):
    with _STATE_LOCK:
        Path(_workdir()).mkdir(parents=True, exist_ok=True)
        path = _state_path(task_id)
        if not selection_update and Path(path).is_file():
            latest = json.loads(Path(path).read_text(encoding='utf-8'))
            if 'selected_slide_id' in latest:
                selected = latest['selected_slide_id']
                valid = {page.get('slide_id') for page in data.get('slides') or []}
                data['selected_slide_id'] = selected if selected is None or selected in valid else None
        temporary = path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        # Windows readers may briefly hold the destination without delete sharing.
        for delay in (0.05, 0.1, 0.2, None):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if os.name != 'nt' or error.winerror not in {5, 32, 33} or delay is None:
                    raise
                time.sleep(delay)
    return path


def load_state(task_id, *, for_resume):
    try:
        with _STATE_LOCK:
            data = json.loads(Path(_state_path(task_id)).read_text(encoding='utf-8'))
        if not isinstance(data, dict) or _REQUIRED_KEYS - set(data):
            raise ValueError('任务状态缺少必需字段')
        return data
    except (ValueError, OSError, UnicodeError) as error:
        if for_resume:
            raise StateCorrupt('任务状态缺失或损坏') from error
        return None


def recover_interrupted():
    """Startup only: an interrupted process no longer owns a generating task."""
    for path in Path(_workdir()).glob('*.json'):
        state = load_state(path.stem, for_resume=False)
        if not state or state.get('renderer') != 'ppt-master' or state.get('stage') != 'generating':
            continue
        state.update(stage='paused', next_node='master', activity='上次制作已中断，可核对进度后继续')
        if state.get('master'):
            messages = state['master']['messages']
            answered = {message.get('tool_call_id') for message in messages if message.get('role') == 'tool'}
            pending = [call['id'] for message in messages if message.get('role') == 'assistant'
                       for call in message.get('tool_calls') or [] if call.get('id') not in answered]
            for call_id in pending:
                messages.append({'role': 'tool', 'tool_call_id': call_id,
                                 'content': json.dumps({'status': 'error', 'error': {
                                     'code': 'interrupted', 'detail': '上次服务中断，调用完成状态未确认；先读取实际文件，不自动重放。'}}, ensure_ascii=False)})
            state['master']['messages'].append({'role': 'system', 'content':
                '上次服务在执行中中断。继续前先读取实际项目文件核对进度，不自动重放可能已完成的写入或导出。'})
        save_state(state['task_id'], state)


def _budget_init():
    return {'tool_calls_left': settings.agent_ppt_max_tool_calls,
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
