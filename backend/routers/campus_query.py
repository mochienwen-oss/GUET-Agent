# -*- coding: utf-8 -*-
"""网页端的空教室查询。

Electron 客户端是靠内嵌学校网页、用**用户本机**的会话去发请求的；网页端没有那一层，
所以这里由后端代发。查询本身是纯 HTTP JSON 接口（`/student/ws/room-borrow/free-list`），
不涉及任何 DOM 解析 —— 这也是它能脱离 Electron 的前提。

前提：用户必须先在网页端完成一次学校账号登录。登录成功后会话会留在服务端
（`security/school_auth.remember_session`），这里按 `user.id` 取用。

安全边界：
· 会话只存内存、按用户隔离、2 小时过期，不进数据库也不落日志；
· 教务侧一旦把我们踢回登录页（302/401），立即清掉该用户的会话并返回 401，
  而不是拿一个已经失效的会话反复重试。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends

from core.errors import ApiError
from database.db import User
from security import school_auth
from security.auth import require_auth

router = APIRouter()

BASE = 'https://bkjwtest.guet.edu.cn'
FREE_LIST = BASE + '/student/ws/room-borrow/free-list'
TIMEOUT = 20.0

# 时段 → 节次。映射放在服务端：前端只传「上午/下午」这样的词，不用各自维护一份表。
# 与桌面端 campus-api.js 的 PERIOD_UNITS 保持一致。
PERIOD_UNITS = {
    'morning': [1, 2, 3, 4],
    'noon': [5],
    'afternoon': [6, 7, 8, 9],
    'evening': [10, 11, 12],
}


def _cookie_jar(cookies):
    jar = httpx.Cookies()
    for cookie in cookies:
        domain = (cookie.get('domain') or '').lstrip('.') or 'bkjwtest.guet.edu.cn'
        jar.set(cookie['name'], cookie['value'], domain=domain,
                path=cookie.get('path') or '/')
    return jar


def _summarize(rooms):
    """把教务的 roomList 收敛成前端要的结构：按楼栋分组 + 计数。

    字段名来自真实抓包（2026-10-31，308 间）：
      room.nameZh / room.code / room.seats / room.remark
      room.building.nameZh / room.roomType.nameZh
    """
    grouped = {}
    for room in rooms:
        if not isinstance(room, dict):
            continue
        building = room.get('building') or {}
        name = str(building.get('nameZh') or '未标注楼栋').strip()
        room_type = (room.get('roomType') or {}).get('nameZh') or ''
        grouped.setdefault(name, []).append({
            'code': str(room.get('code') or room.get('nameZh') or ''),
            'name': str(room.get('nameZh') or ''),
            'type': str(room_type),
            'seats': int(room.get('seats') or 0),
            'remark': str(room.get('remark') or '').strip(),
        })
    buildings = [{'name': name, 'rooms': sorted(items, key=lambda item: item['code'])}
                 for name, items in sorted(grouped.items())]
    return {'total': sum(len(building['rooms']) for building in buildings),
            'buildings': buildings}


@router.post('/campus/free-classroom')
def free_classroom(body: dict, user: User = Depends(require_auth)):
    cookies = school_auth.session_cookies(user.id)
    if not cookies:
        raise ApiError(401, '请先在网页端完成学校账号登录，登录后即可查询')

    date = str(body.get('date') or '')[:10]
    if not date:
        raise ApiError(400, '缺少查询日期')
    units = body.get('units') or PERIOD_UNITS.get(str(body.get('period') or ''), [1, 2, 3, 4])
    try:
        units = [int(unit) for unit in units]
    except (TypeError, ValueError):
        raise ApiError(400, '查询节次无效')

    payload = {
        'buildingId': str(body.get('buildingId') or ''),
        'campusId': int(body.get('campusId') or 3),
        'roomId': str(body.get('roomId') or ''),
        'dateTimeSegmentCmd': {
            'startDateTime': date, 'endDateTime': date,
            'startTime': '', 'endTime': '', 'weekdays': [], 'units': units,
        },
        'seatsForLessonGte': str(body.get('seats') or ''),
        'hasDataPermission': False,
    }

    try:
        with httpx.Client(cookies=_cookie_jar(cookies), timeout=TIMEOUT,
                          follow_redirects=False,
                          headers={'Content-Type': 'application/json',
                                   'Accept': 'application/json'}) as client:
            response = client.post(FREE_LIST, json=payload)
    except httpx.HTTPError:
        raise ApiError(503, '暂时无法连接教务系统，请稍后重试')

    # 被踢回登录页 = 会话在教务侧已失效（过期，或密码在别处被改）
    if response.status_code in (301, 302, 303, 307, 308) or response.status_code == 401:
        school_auth.clear_session(user.id)
        raise ApiError(401, '学校会话已过期，请重新登录学校账号')
    if response.status_code != 200:
        raise ApiError(502, '教务系统返回异常（%d）' % response.status_code)

    try:
        data = response.json()
    except ValueError:
        school_auth.clear_session(user.id)
        raise ApiError(401, '学校会话已失效，请重新登录学校账号')

    rooms = data.get('roomList') if isinstance(data, dict) else None
    if not isinstance(rooms, list):
        raise ApiError(502, '教务系统返回的数据无法识别')

    return {'date': date, 'units': [str(unit) for unit in units],
            'summary': _summarize(rooms), 'source': 'server'}


@router.get('/campus/session-state')
def session_state(user: User = Depends(require_auth)):
    """网页端用来判断「还要不要先登录学校账号」，避免用户白问一次。"""
    return {'has_session': school_auth.has_session(user.id)}
