# -*- coding: utf-8 -*-
"""创建或继续空教室任务；模型负责提取语义，桌面端负责执行学校查询。"""
from datetime import date

from agent.runtime.context import RuntimeContext
from documents import campus_tasks

DESCRIPTION = ('用户要求查询空教室时调用。根据用户的话提取日期、星期、节次及可选条件；'
               '模型不负责猜学校的楼栋或教室 ID。当前工具不会直接返回实时结果。')

_OLD_PERIODS = {'morning': ['1', '2', '3', '4'], 'noon': ['5'],
                'afternoon': ['6', '7', '8', '9'], 'evening': ['10', '11', '12']}


def _normalize(query, previous=None, clear=()):
    previous = dict(previous or {})
    # Preserve tasks created before the full query contract was introduced.
    if 'date' in previous and 'start_date' not in previous:
        previous['start_date'] = previous.get('date')
        previous['units'] = _OLD_PERIODS.get(previous.get('period'))
        if previous.get('building_id'):
            previous['building_id'] = str(previous['building_id'])
    previous.pop('date', None)
    previous.pop('period', None)
    for key in clear or []:
        previous.pop(key, None)
        if key == 'building':
            previous.pop('building_id', None)
            previous.pop('room', None)
    if 'building' in (query or {}) and query.get('building') is not None:
        previous.pop('building_id', None)
        # A room belongs to its previous building unless the user supplied a new room too.
        if (query or {}).get('room') is None: previous.pop('room', None)
    for key, value in (query or {}).items():
        if value is not None:
            previous[key] = value.strip() if isinstance(value, str) else value
    start = previous.get('start_date')
    if not start:
        return None
    try:
        start_parsed = date.fromisoformat(start)
        if start_parsed.isoformat() != start:
            raise ValueError('日期格式无效')
        end = previous.get('end_date')
        if end:
            end_parsed = date.fromisoformat(end)
            if end_parsed.isoformat() != end or end_parsed < start_parsed:
                raise ValueError('结束日期无效')
    except (TypeError, ValueError) as error:
        raise ValueError('日期无效，请使用 YYYY-MM-DD 且结束日期不得早于起始日期') from error
    for field, allowed in [('weekdays', set(map(str, range(1, 8)))), ('units', set(map(str, range(1, 13))))]:
        values = previous.get(field)
        if values is not None:
            if not isinstance(values, list) or not values or any(str(v) not in allowed for v in values):
                raise ValueError(f'{field} 包含无效选项')
            previous[field] = list(dict.fromkeys(map(str, values)))
    if previous.get('room') and not (previous.get('building') or previous.get('building_id')):
        raise ValueError('指定教室时请一并说明所在教学楼')
    previous['campus_id'] = 3  # 学校页面当前只提供花江校区。
    return previous


def start_campus_service(runtime_context: RuntimeContext, service: str,
                         conditions: list[str] | None = None, query: dict | None = None):
    if service not in campus_tasks.SERVICES:
        raise ValueError('暂不支持该校园服务')
    query = _normalize(query)
    if query is None:
        return {'status': 'needs_user', 'data': {'message': '请先询问要查询的起始日期'}}
    record = campus_tasks.create(user_id=runtime_context.user_id,
        conversation_id=runtime_context.conversation_id, service=service,
        conditions=list(conditions or []), query=query)
    return {'status': 'success', 'data': {'task_id': record['task_id'], 'query': query},
            'campus_action': {'task_id': record['task_id'], 'service': service, 'query': query}}


def continue_campus_service(runtime_context: RuntimeContext, instruction: str,
                            query: dict | None = None, clear: list[str] | None = None):
    record = campus_tasks.latest(user_id=runtime_context.user_id,
                                 conversation_id=runtime_context.conversation_id)
    if not record:
        raise ValueError('当前会话没有校园服务任务')
    updated = _normalize(query, record.get('query'), clear)
    if updated is None:
        return {'status': 'needs_user', 'data': {'message': '请先询问要查询的起始日期'}}
    record = campus_tasks.add_instruction(record['task_id'], user_id=runtime_context.user_id,
                                          instruction=instruction)
    record = campus_tasks.set_query(record['task_id'], user_id=runtime_context.user_id, query=updated)
    return {'status': 'success', 'data': {'query': updated},
            'campus_action': {'task_id': record['task_id'], 'service': record['service'], 'query': updated}}
