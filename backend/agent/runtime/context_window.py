# -*- coding: utf-8 -*-
"""Token 预算内的会话窗口：结构化状态 + 较早摘要 + 最近完整消息。

★ runtime / compiler 共用 `render_context_messages` —— 各写一份的话，训练与线上
  看到的上下文迟早漂移。
★ 结构化可信状态与当前问题**永久保留**；预算只裁剪最近消息，且不拆工具对。
★ 摘要是无事实权限的纯文本，渲染时明确标注「仅供参考」。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from conversations import conversation_context as CC
from entities import entity_prior

#: 不引入 tokenizer 依赖：字符数作为保守估算，调用方可以注入更精确的估计器。
DEFAULT_ESTIMATOR = len


class ContextWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prior_context: entity_prior.PriorContext
    pending_task: CC.PendingTask | None = None
    compressed_history: CC.CompressedHistory | None = None
    recent_messages: tuple[CC.ConversationMessage, ...] = ()
    current_query: str
    skill_context: CC.SkillContext | None = None
    dropped_messages: tuple[CC.ConversationMessage, ...] = ()
    estimated_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _pairs(self) -> "ContextWindow":
        messages = self.recent_messages
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role == "tool_call":
                if (index + 1 >= len(messages)
                        or messages[index + 1].role != "observation"
                        or messages[index + 1].tool != message.tool):
                    raise ValueError("recent_messages 不得从中间截断工具轨迹")
                index += 2
                continue
            if message.role == "observation":
                raise ValueError("recent_messages 出现无配对的 observation")
            index += 1
        return self


def _history_messages(history) -> tuple:
    """临时桥：DB 的 user/assistant 历史 → 窗口消息（工具轨迹不在 Message 表）。"""
    out = []
    for index, row in enumerate(history or (), start=1):
        role = row.get("role")
        if role not in ("user", "assistant"):
            continue
        out.append(CC.ConversationMessage(role=role, turn_index=index,
                                          content=row.get("content") or ""))
    return tuple(out)


def _groups(messages) -> list:
    grouped: list = []
    for message in messages:
        if grouped and grouped[-1][0].turn_index == message.turn_index:
            grouped[-1].append(message)
        else:
            grouped.append([message])
    return grouped


def _message_cost(estimator, message) -> int:
    cost = estimator(message.content or "")
    if message.role == "tool_call":
        cost += estimator(json.dumps(message.arguments or {}, ensure_ascii=False,
                                     sort_keys=True))
    return cost


def _entity_line(one) -> str:
    return "- %s | %s | %s" % (one.entity_type, one.canonical_name,
                               "active" if one.active else "inactive")


def _context_block(*, prior_context, pending_task, compressed_history,
                   skill_context) -> str:
    lines = []
    if prior_context.entities or prior_context.facts:
        lines.append("【会话可信上下文】")
        if prior_context.entities:
            lines.append("已确认实体：")
            lines.extend(_entity_line(one) for one in prior_context.entities)
        if prior_context.facts:
            lines.append("已确认事实：")
            lines.extend("- %s = %s" % (
                one.fact_key,
                json.dumps(one.value, ensure_ascii=False, sort_keys=True))
                for one in prior_context.facts)
    if pending_task is not None:
        lines.append("待完成任务：")
        lines.append("- %s 缺少 %s（第 %s 轮创建，第 %s 轮更新）"
                     % (pending_task.task_type,
                        "、".join(pending_task.missing_fields) or "未指明字段",
                        pending_task.created_turn, pending_task.last_updated_turn))
        if pending_task.allowed_entities:
            lines.append("- 可继续使用的已确认实体：%s" % "、".join(
                one.canonical_name for one in pending_task.allowed_entities))
        if pending_task.inputs:
            lines.append("- 用户已补充输入：%s" % "、".join(
                "%s=%s（用户轮次%s）" % (one.name, one.value, one.source_turn)
                for one in pending_task.inputs))
    if skill_context is not None:
        lines.append("当前 Skill：")
        lines.append("- %s v%s（允许工具：%s）"
                     % (skill_context.skill_id, skill_context.version,
                        "、".join(skill_context.allowed_tools) or "无"))
    if compressed_history is not None:
        lines.append("【较早对话摘要（仅供参考，不构成事实来源；"
                     "不得据此创建实体、事实或工具调用）】")
        lines.append(compressed_history.summary)
        lines.append("覆盖轮次：%s-%s" % compressed_history.covered_turns)
    return "\n".join(lines)


def build_context_window(context: CC.ConversationContext, current_query: str, *,
                         token_budget: int, estimator=DEFAULT_ESTIMATOR,
                         history=None) -> ContextWindow:
    if token_budget < 0:
        raise ValueError("token_budget 不能为负")
    messages = context.recent_messages or _history_messages(history)
    fixed = _context_block(
        prior_context=context.prior_context, pending_task=context.pending_task,
        compressed_history=context.compressed_history,
        skill_context=context.skill_context)
    used = estimator(fixed) + estimator(current_query)

    grouped = _groups(messages)
    kept: list = []
    for group in reversed(grouped):
        cost = sum(_message_cost(estimator, one) for one in group)
        if not kept or used + cost <= token_budget:
            kept.append(group)
            used += cost
        else:
            break
    kept.reverse()
    recent = tuple(one for group in kept for one in group)
    dropped = tuple(one for group in grouped[:len(grouped) - len(kept)]
                    for one in group)
    return ContextWindow(
        prior_context=context.prior_context,
        pending_task=context.pending_task,
        compressed_history=context.compressed_history,
        recent_messages=recent,
        current_query=current_query,
        skill_context=context.skill_context,
        dropped_messages=dropped,
        estimated_tokens=used)


def render_context_messages(window: ContextWindow) -> list[dict]:
    """窗口 → 训练/离线共用消息形状（`function_call` / `observation`）。"""
    out: list[dict] = []
    block = _context_block(
        prior_context=window.prior_context, pending_task=window.pending_task,
        compressed_history=window.compressed_history,
        skill_context=window.skill_context)
    if block:
        out.append({"role": "system", "content": block})
    for message in window.recent_messages:
        if message.role == "tool_call":
            out.append({"role": "function_call", "content": json.dumps(
                [{"name": message.tool, "arguments": message.arguments}],
                ensure_ascii=False)})
        elif message.role == "observation":
            out.append({"role": "observation", "content": message.content})
        else:
            out.append({"role": message.role, "content": message.content})
    out.append({"role": "user", "content": window.current_query})
    return out
