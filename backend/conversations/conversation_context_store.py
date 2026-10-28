# -*- coding: utf-8 -*-
"""ConversationContext v1 的持久化与轮次收口（runtime 专用）。

★ 上下文存在 `Conversation.context_json`，`context_revision` 做乐观锁；
  Message 表继续保存原始 user/assistant 文本（审计与重建用）。
★ 本模块不调 LLM、不执行工具；`record_turn` 只接收图已经产出的调用/校验结果。
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import update

from conversations import conversation_context as CC
from entities import entity_prior
from database.db import Conversation, Message


class ContextNotFound(LookupError):
    pass


class ContextVersionError(ValueError):
    """未知 / 损坏的 context 版本 —— 失败关闭，不降级为无上下文。"""


class ContextRevisionConflict(RuntimeError):
    """乐观锁冲突：调用方基于过期 revision 写入。"""


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _fresh(conversation_id) -> CC.ConversationContext:
    key = str(conversation_id)
    return CC.ConversationContext(
        conversation_id=key, revision=1,
        prior_context=entity_prior.PriorContext(conversation_id=key))


def load_context(db, conversation_id) -> CC.ConversationContext:
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise ContextNotFound("会话不存在：%s" % conversation_id)
    raw, stored_revision = row.context_json, row.context_revision
    if raw is None:
        if stored_revision is not None:
            raise ContextVersionError("context_json 为空但 context_revision 非空")
        return _fresh(row.id)
    if not isinstance(raw, dict):
        raise ContextVersionError("context_json 不是对象")
    if raw.get("context_version") != CC.CONTEXT_VERSION:
        raise ContextVersionError("未知 context_version：%r"
                                  % raw.get("context_version"))
    try:
        context = CC.ConversationContext.model_validate(raw)
    except ValueError as error:
        raise ContextVersionError("上下文损坏：%s" % error) from error
    if stored_revision is None or context.revision != stored_revision:
        raise ContextVersionError("context_json 的 revision 与列不一致")
    if context.conversation_id != str(row.id):
        raise ContextVersionError("context_json 的 conversation_id 与行不一致")
    return context


def save_context(db, conversation_id, context: CC.ConversationContext, *,
                 expected_revision: int) -> CC.ConversationContext:
    """按 `expected_revision` 原子写入下一版；过期写入抛冲突，绝不覆盖。"""
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise ContextNotFound("会话不存在：%s" % conversation_id)
    if context.conversation_id != str(row.id):
        raise ValueError("context.conversation_id 与目标会话不一致")
    if row.context_json is None:
        if row.context_revision is not None:
            raise ContextVersionError("context_json 为空但 context_revision 非空")
        current = 1
    else:
        if row.context_revision is None:
            raise ContextVersionError("context_json 非空但 context_revision 为空")
        current = row.context_revision
    if current != expected_revision:
        raise ContextRevisionConflict("期望 revision=%s，实际 %s"
                                      % (expected_revision, current))
    if context.revision != expected_revision + 1:
        raise ValueError("save_context 只写下一版：context.revision=%s，expect=%s"
                         % (context.revision, expected_revision))

    condition = (Conversation.context_revision.is_(None)
                 if row.context_json is None
                 else Conversation.context_revision == current)
    result = db.execute(
        update(Conversation)
        .where(Conversation.id == row.id, condition)
        .values(context_json=context.model_dump(mode="json"),
                context_revision=context.revision))
    if result.rowcount != 1:
        db.rollback()
        raise ContextRevisionConflict("并发写入：revision=%s 已被占用"
                                      % expected_revision)
    db.commit()
    db.expire(row)                      # Core UPDATE 不保证刷新身份映射
    return context


def _successful_calls(calls) -> dict:
    keyed: dict = {}
    for call in calls or ():
        if call.get("status") != "success":
            continue
        key = (call.get("name"), _canonical(call.get("canonical_args") or {}))
        keyed.setdefault(key, []).append(call)
    return keyed


def _derive_entities(validations, calls, turn_index: int) -> tuple:
    """只从**允许且真实成功**的 canonical 调用派生 `tool_canonical_args` 实体。"""
    successes = _successful_calls(calls)
    derived, seen = [], set()
    for audit in validations or ():
        if audit.get("decision") != "allow":
            continue
        key = (audit.get("tool"), _canonical(audit.get("canonical_args") or {}))
        matches = successes.get(key)
        if not matches:
            continue
        call = matches.pop(0)
        observation_sha = call.get("observation_sha256")
        if not observation_sha:
            continue                     # 没有 observation 身份 → 不产生可信实体
        identity = hashlib.sha256(_canonical({
            "tool": call.get("name"),
            "canonical_args": call.get("canonical_args") or {},
            "observation_sha256": observation_sha,
        }).encode("utf-8")).hexdigest()
        for binding in audit.get("bindings") or ():
            entity_id = binding.get("entity_id")
            name = binding.get("canonical_name")
            if entity_id is None or not name or entity_id in seen:
                continue
            if binding.get("entity_type") not in ("major", "course"):
                continue
            seen.add(entity_id)
            derived.append(entity_prior.PriorEntity(
                entity_type=binding["entity_type"], canonical_name=name,
                entity_id=entity_id, source="tool_canonical_args",
                source_turn=turn_index, evidence_identity=identity))
    return tuple(derived)


def _derive_pending_task(validations, current: CC.ConversationContext,
                         turn_index: int):
    for audit in validations or ():
        if audit.get("decision") != "reject":
            continue
        reason = audit.get("reason") or ""
        if not reason.startswith("bad_arg:"):
            continue
        field = reason.split(":", 1)[1]
        if not field:
            continue
        existing = current.pending_task
        created = (existing.created_turn
                   if existing is not None and existing.task_type == audit.get("tool")
                   else turn_index)
        return CC.PendingTask(
            task_type=audit.get("tool"), missing_fields=(field,),
            allowed_entities=tuple(one for one in current.prior_context.entities
                                   if one.active and one.catalog_valid),
            created_turn=created, last_updated_turn=turn_index)
    if current.pending_task is not None and current.pending_task.task_type == "score_conversion":
        return current.pending_task
    if any(audit.get("decision") == "allow" for audit in validations or ()):
        return None
    return current.pending_task


def record_turn(db, conversation_id, *, validations, calls, correction=None,
                explicit_entities=(), explicit_facts=(), unresolved=(),
                context_action=None):
    """一轮结束后：派生可信状态 → `apply_turn_update` → 乐观锁保存。"""
    current = load_context(db, conversation_id)
    pending = _derive_pending_task(validations, current, current.revision)
    if context_action is not None:
        keys = set(context_action)
        if keys not in ({"clear_pending", "expected_revision"}, {"set_pending", "expected_revision"}):
            raise ValueError("未知上下文操作")
        if context_action["expected_revision"] != current.revision:
            raise ContextRevisionConflict("上下文操作基于过期会话状态")
        if "set_pending" in context_action:
            pending = CC.PendingTask.model_validate(context_action["set_pending"])
            if pending.task_type != "score_conversion" or pending.last_updated_turn != current.revision:
                raise ValueError("待办不属于本轮换算请求")
        else:
            if context_action["clear_pending"] is not True or current.pending_task is None:
                raise ValueError("没有可取消的待办或清除操作无效")
            pending = None
    turn_index = current.revision
    entities = _derive_entities(validations, calls, turn_index) \
        + tuple(explicit_entities)
    updated = CC.apply_turn_update(
        current, entities=entities, facts=tuple(explicit_facts),
        unresolved=tuple(unresolved), correction=correction,
        pending_task=pending)
    return save_context(db, conversation_id, updated,
                        expected_revision=current.revision)


def recent_history(db, conversation_id, *, max_messages: int = 6):
    """当前聊天按时间顺序读取最近 max_messages 条消息，默认 6 条。"""
    rows = (db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(max_messages).all())
    return [{"role": row.role, "content": row.content}
            for row in reversed(rows)]
