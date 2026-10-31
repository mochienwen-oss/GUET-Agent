# -*- coding: utf-8 -*-
"""校园服务任务的持久化（轻量 JSON，不建表）。

对应实施计划 3.4：任务至少关联 task_id、用户、会话、服务类型与用户明确的条件。
状态只由桌面端回报真实页面情况，服务端与模型都不下发 URL / 选择器 / 脚本。
"""
from __future__ import annotations

import json
import os
from functools import wraps
from threading import RLock
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.paths import BACKEND_ROOT

ROOT = BACKEND_ROOT / "data" / "campus_tasks"
SERVICES = {"empty_classroom": "空教室查询"}
_lock = RLock()


def _synchronized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapped


STATUSES = {
    "awaiting_login": "等待用户登录",
    "navigating": "正在导航",
    "at_query_page": "已到达查询页面",
    "filled": "已按条件填写",
    "completed": "查询已完成",
    "needs_user": "需要用户接手",
    "paused": "已暂停自动导航",
    "failed": "未能完成",
}


class TaskNotAvailable(LookupError):
    """任务不存在或不属于当前用户。"""


def _path(task_id: str) -> Path:
    return ROOT / ("%s.json" % task_id)


def _write(record: dict) -> None:
    """原子写，且临时文件名唯一。

    早先用固定的 `<task_id>.tmp`，桌面端连续回传两个状态时会撞在同一个临时文件上，
    Windows 直接抛 PermissionError（写一个、替换另一个）。改成唯一名后不再互相踩。
    """
    target = _path(str(record["task_id"]))
    tmp = target.with_name("%s.%s.tmp" % (target.stem, uuid4().hex[:8]))
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    try:
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def create(*, user_id: int, conversation_id: int, service: str,
           conditions: list[str], query: dict | None = None) -> dict:
    ROOT.mkdir(parents=True, exist_ok=True)
    task_id = uuid4().hex[:12]
    record = {"task_id": task_id, "user_id": int(user_id),
              "conversation_id": int(conversation_id), "service": service,
              "conditions": [str(item)[:120] for item in conditions][:12],
              "status": "awaiting_login", "detail": "",
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if query:
        record["query"] = query
    _write(record)
    return record


def load(task_id: str, *, user_id: int | None = None) -> dict:
    path = _path(str(task_id or ""))
    if not path.is_file():
        raise TaskNotAvailable("校园服务任务不存在")
    record = json.loads(path.read_text(encoding="utf-8"))
    if user_id is not None and int(record.get("user_id", 0)) != int(user_id):
        raise TaskNotAvailable("校园服务任务不存在")
    return record


@_synchronized
def update_status(task_id: str, *, user_id: int, status: str, detail: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("未知的校园服务状态：%s" % status)
    record = load(task_id, user_id=user_id)
    record["status"] = status
    record["detail"] = str(detail or "")[:300]
    record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(record)
    return record


@_synchronized
def add_instruction(task_id: str, *, user_id: int, instruction: str) -> dict:
    record = load(task_id, user_id=user_id)
    text = str(instruction or '').strip()
    if not text:
        raise ValueError('请补充查询要求')
    record['conditions'] = (record.get('conditions', []) + [text[:500]])[-12:]
    _write(record)
    return record


@_synchronized
def set_query(task_id: str, *, user_id: int, query: dict) -> dict:
    """把翻译好的结构化查询条件写进任务（桌面端据此直接调接口）。"""
    record = load(task_id, user_id=user_id)
    record['query'] = query if isinstance(query, dict) else {}
    _write(record)
    return record


@_synchronized
def save_result(task_id: str, *, user_id: int, date: str, units: list, summary: dict, query: dict | None = None) -> dict:
    """桌面端查询成功后回传的结构化结果（不含任何凭据）。"""
    record = load(task_id, user_id=user_id)
    if query and query != record.get('query'):
        raise ValueError('查询条件已更新，请重新查询')
    record['result'] = {
        'date': str(date or '')[:10],
        'units': [str(u) for u in (units or [])][:12],
        'query': query or {},
        'summary': summary if isinstance(summary, dict) else {},
        'saved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    record['status'] = 'completed'
    record['detail'] = '已查到 %d 间空闲教室' % int((summary or {}).get('total', 0))
    record['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    _write(record)
    return record


def public(record: dict) -> dict:
    data = {"task_id": record["task_id"], "service": record["service"],
            "conversation_id": record['conversation_id'],
            "conditions": record.get("conditions", []), "status": record["status"],
            "status_text": STATUSES.get(record["status"], record["status"]),
            "detail": record.get("detail", "")}
    if record.get('query'):
        data['query'] = record['query']
    if record.get('result'):
        data['result'] = record['result']
    return data


def latest(*, user_id: int, conversation_id: int) -> dict | None:
    records = []
    if ROOT.exists():
        for path in ROOT.glob('*.json'):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                continue
            if record.get('user_id') == user_id and record.get('conversation_id') == conversation_id:
                records.append(record)
    return max(records, key=lambda item: item['created_at']) if records else None
