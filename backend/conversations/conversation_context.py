# -*- coding: utf-8 -*-
"""ConversationContext v1：runtime / replay / compiler 共用的会话上下文契约。

★ 可信实体与事实**只**来自 `entity_prior` 的枚举来源；摘要（`CompressedHistory`）
  是纯文本派生产物，**不创建**实体、事实、待办或工具权限。
★ 本模块只做纯模型与纯函数：不读数据库、不调工具、不调 LLM。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator, model_serializer)

from entities import entity_prior

CONTEXT_VERSION = "conversation-context/1"
SUMMARY_VERSION = "conversation-summary/1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


def _sha(value: str) -> str:
    if not _SHA256.match(value):
        raise ValueError("必须是 64 位小写十六进制")
    return value


class UserCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_index: int = Field(ge=1)
    replaced_entity_id: str
    replacement_entity_id: str

    @field_validator("replaced_entity_id", "replacement_entity_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _nonblank(value)

    @model_validator(mode="after")
    def _different(self) -> "UserCorrection":
        if self.replaced_entity_id == self.replacement_entity_id:
            raise ValueError("纠正前后不能是同一实体")
        return self


class PendingInput(BaseModel):
    """Explicit user inputs; not school facts or model-generated defaults."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal["exam", "score"]
    value: str
    source_turn: int = Field(ge=1)

    @model_validator(mode="after")
    def _valid(self):
        from decimal import Decimal
        if self.name == "exam" and self.value not in {"CET4", "CET6"}:
            raise ValueError("不支持的考试类型")
        if self.name == "score" and (not re.fullmatch(r"\d{3}(?:\.\d+)?", self.value)
                or not 0 <= Decimal(self.value) <= 710):
            raise ValueError("无效CET成绩")
        return self


class PendingTask(BaseModel):
    """跨用户轮次恢复的任务状态；**不存放**未经证据确认的业务事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: str
    missing_fields: tuple[str, ...] = ()
    unresolved_mentions: tuple[str, ...] = ()
    allowed_entities: tuple[entity_prior.PriorEntity, ...] = ()
    created_turn: int = Field(ge=1)
    last_updated_turn: int = Field(ge=1)
    inputs: tuple[PendingInput, ...] = ()

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        data = handler(self)
        if not self.inputs:
            data.pop("inputs", None)
        return data

    @field_validator("task_type")
    @classmethod
    def _task_type(cls, value: str) -> str:
        return _nonblank(value)

    @model_validator(mode="after")
    def _consistent(self) -> "PendingTask":
        if self.last_updated_turn < self.created_turn:
            raise ValueError("last_updated_turn 不能早于 created_turn")
        if self.inputs and (self.task_type != "score_conversion"
                or len({item.name for item in self.inputs}) != len(self.inputs)
                or any(item.source_turn > self.last_updated_turn for item in self.inputs)
                or set(self.missing_fields) & {item.name for item in self.inputs}):
            raise ValueError("换算待办输入与缺口或来源轮次不一致")
        for one in self.allowed_entities:
            if not one.active or not one.catalog_valid:
                raise ValueError("pending_task 只能引用 active 且 catalog 有效的实体")
        return self


class CompressedHistory(BaseModel):
    """较早历史的派生摘要；`summary` 无事实权限。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    covered_turns: tuple[int, int]
    source_hash: str
    summary_sha256: str
    summary_version: Literal[SUMMARY_VERSION] = SUMMARY_VERSION

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("source_hash", "summary_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        return _sha(value)

    @model_validator(mode="after")
    def _covered(self) -> "CompressedHistory":
        start, end = self.covered_turns
        if start < 1 or end < start:
            raise ValueError("covered_turns 必须是 [start, end]，且 1 <= start <= end")
        expected = hashlib.sha256(self.summary.encode("utf-8")).hexdigest()
        if self.summary_sha256 != expected:
            raise ValueError("summary_sha256 与摘要正文不一致")
        return self


class ConversationMessage(BaseModel):
    """最近完整消息；工具调用必须与 observation 成对出现。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant", "tool_call", "observation"]
    turn_index: int = Field(ge=1)
    content: str = ""
    tool: str | None = None
    arguments: dict | None = None

    @model_validator(mode="after")
    def _shape(self) -> "ConversationMessage":
        if self.role == "tool_call":
            if not self.tool or self.arguments is None:
                raise ValueError("tool_call 必须有 tool 与 arguments")
        elif self.role == "observation":
            if not self.tool:
                raise ValueError("observation 必须有 tool")
        elif self.tool is not None or self.arguments is not None:
            raise ValueError("非工具消息不得携带 tool / arguments")
        if self.role in ("user", "assistant", "observation") and not self.content.strip():
            raise ValueError("%s 消息的 content 不能为空" % self.role)
        return self


class SkillContext(BaseModel):
    """当前轮选中的 Skill 身份快照（schema 在 Task 5 由 Registry 提供）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    version: str
    allowed_tools: tuple[str, ...] = ()

    @field_validator("skill_id", "version")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("allowed_tools")
    @classmethod
    def _tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_nonblank(one) for one in value)


class ConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    context_version: Literal[CONTEXT_VERSION] = CONTEXT_VERSION
    revision: int = Field(ge=1)
    prior_context: entity_prior.PriorContext
    pending_task: PendingTask | None = None
    user_corrections: tuple[UserCorrection, ...] = ()
    compressed_history: CompressedHistory | None = None
    recent_messages: tuple[ConversationMessage, ...] = ()
    skill_context: SkillContext | None = None

    @field_validator("conversation_id")
    @classmethod
    def _conversation_id(cls, value: str) -> str:
        return _nonblank(value)

    @model_validator(mode="after")
    def _consistent(self) -> "ConversationContext":
        if self.prior_context.conversation_id != self.conversation_id:
            raise ValueError("prior_context.conversation_id 必须与上下文一致")
        previous_turn = 0
        index = 0
        messages = self.recent_messages
        while index < len(messages):
            message = messages[index]
            if message.turn_index < previous_turn:
                raise ValueError("recent_messages 必须按 turn_index 有序")
            previous_turn = message.turn_index
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


def _merge_entities(existing, incoming) -> tuple:
    by_id = {one.entity_id: one for one in existing}
    for one in incoming:
        previous = by_id.get(one.entity_id)
        if previous is not None and previous.canonical_name != one.canonical_name:
            raise ValueError("实体 %s 的名称与已有实体不符" % one.entity_id)
        by_id[one.entity_id] = one
    return tuple(by_id[key] for key in sorted(by_id))


def _merge_facts(existing, incoming) -> tuple:
    keyed = {(one.fact_key, one.evidence_identity, one.source_turn): one
             for one in tuple(existing) + tuple(incoming)}
    return tuple(keyed[key] for key in sorted(keyed))


def _merge_unresolved(existing, incoming) -> tuple:
    keyed = {(one.source_turn, one.raw_text, one.resolution_status): one
             for one in tuple(existing) + tuple(incoming)}
    return tuple(keyed[key] for key in sorted(keyed))


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def apply_turn_update(context: ConversationContext, *, entities, facts,
                      unresolved, correction, pending_task) -> ConversationContext:
    """把**当前轮**派生结果并入可信状态，并递增 revision。

    ★ `entities` / `facts` 必须已经带可信 `source` 与 evidence identity；
      本函数不从摘要、assistant 文本或模型猜测中推导任何一项。
    ★ `correction` 指向的旧实体必须存在；替换实体必须在 `entities` 中且 active。
    """
    merged_entities = _merge_entities(context.prior_context.entities, entities)
    corrections = context.user_corrections

    if correction is not None:
        by_id = {one.entity_id: one for one in merged_entities}
        replaced = by_id.get(correction.replaced_entity_id)
        replacement = by_id.get(correction.replacement_entity_id)
        if replaced is None:
            raise ValueError("纠正的旧实体不存在：%s"
                             % correction.replaced_entity_id)
        if replacement is None:
            raise ValueError("纠正的替换实体不在本轮的 entities 中：%s"
                             % correction.replacement_entity_id)
        if not replacement.active or not replacement.catalog_valid:
            raise ValueError("纠正的替换实体必须 active 且 catalog 有效")
        if replacement.source not in ("user_explicit", "human_correction"):
            raise ValueError("纠正的替换实体来源必须是用户明确或人工纠正")
        by_id[replaced.entity_id] = replaced.model_copy(update={"active": False})
        merged_entities = tuple(by_id[key] for key in sorted(by_id))
        corrections = corrections + (correction,)

    merged_facts = _merge_facts(context.prior_context.facts, facts)
    merged_unresolved = _merge_unresolved(context.prior_context.unresolved,
                                          unresolved)

    if pending_task is not None:
        active = {one.entity_id: one for one in merged_entities
                  if one.active and one.catalog_valid}
        for one in pending_task.allowed_entities:
            current = active.get(one.entity_id)
            if current is None or current.canonical_name != one.canonical_name:
                raise ValueError("pending_task 引用了 inactive / 不存在 / "
                                 "名称不符的实体：%s" % one.entity_id)

    return ConversationContext(
        conversation_id=context.conversation_id,
        context_version=context.context_version,
        revision=context.revision + 1,
        prior_context=entity_prior.PriorContext(
            conversation_id=context.conversation_id,
            entities=merged_entities,
            facts=merged_facts,
            unresolved=merged_unresolved),
        pending_task=pending_task,
        user_corrections=corrections,
        compressed_history=context.compressed_history,
        recent_messages=context.recent_messages,
        skill_context=context.skill_context)


def context_binding_payload(context: ConversationContext) -> dict:
    """上下文身份：版本、revision、可信状态、摘要身份、最近消息身份。

    ★ 摘要只进**身份**（版本 / source_hash / summary_sha256 / covered_turns），
      不进正文 —— 正文没有事实权限，也不该成为绑定的一部分。
    """
    summary = context.compressed_history
    messages = [one.model_dump(mode="json") for one in context.recent_messages]
    recent_turns = ([messages[0]["turn_index"], messages[-1]["turn_index"]]
                    if messages else None)
    return {
        "conversation_id": context.conversation_id,
        "context_version": context.context_version,
        "revision": context.revision,
        "prior_context": context.prior_context.model_dump(mode="json"),
        "pending_task": (context.pending_task.model_dump(mode="json")
                         if context.pending_task else None),
        "user_corrections": [one.model_dump(mode="json")
                             for one in context.user_corrections],
        "compressed_history": (None if summary is None else {
            "summary_version": summary.summary_version,
            "source_hash": summary.source_hash,
            "summary_sha256": summary.summary_sha256,
            "covered_turns": list(summary.covered_turns),
        }),
        "recent_messages_sha256": hashlib.sha256(
            _canonical_json(messages).encode("utf-8")).hexdigest(),
        "recent_messages_count": len(messages),
        "recent_message_range": recent_turns,
        "skill_context": (context.skill_context.model_dump(mode="json")
                          if context.skill_context else None),
    }
