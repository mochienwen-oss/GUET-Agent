# -*- coding: utf-8 -*-
"""多轮 prior-context 的**共享可信边界**（runtime / compiler / replay 同一份）。

★ 只负责「前序已确认实体/事实是否可信」，**不补参**、不执行工具、不解析当前问句。
★ 可信来源是**枚举**：EasyDataset assistant、未审核 Teacher、LLM、embedding/top-1、
  失败工具原始参数、unresolved mention 都不在枚举里，因此在装载时就被拒。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

TRUSTED_ENTITY_SOURCES = (
    "user_explicit",          # 前序用户明确写出的规范实体
    "resolver_selected",      # 前序 resolver 确定性选中
    "tool_canonical_args",    # 前序真实工具成功使用的 canonical_args
    "human_correction",       # 人工审核通过的实体纠正
)
TRUSTED_FACT_SOURCES = (
    "tool_observation",       # 前序真实工具 observation
    "human_correction",       # 人工审核通过的纠正事实
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PriorEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: Literal["major", "course"]
    canonical_name: str
    entity_id: str
    source: Literal[TRUSTED_ENTITY_SOURCES]
    source_turn: int = Field(ge=1)
    evidence_identity: str | None = None
    active: bool = True
    catalog_valid: bool = True

    @field_validator("canonical_name", "entity_id")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("evidence_identity")
    @classmethod
    def _sha(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.match(value):
            raise ValueError("evidence_identity 必须是 64 位小写十六进制")
        return value

    @model_validator(mode="after")
    def _tool_source_needs_evidence(self) -> "PriorEntity":
        if self.source == "tool_canonical_args" and not self.evidence_identity:
            raise ValueError("source=tool_canonical_args 必须提供 evidence_identity")
        return self


class PriorFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_key: str
    value: Any
    source: Literal[TRUSTED_FACT_SOURCES]
    source_turn: int = Field(ge=1)
    evidence_identity: str

    @field_validator("fact_key", "evidence_identity")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("evidence_identity")
    @classmethod
    def _sha(cls, value: str) -> str:
        if not _SHA256.match(value):
            raise ValueError("evidence_identity 必须是 64 位小写十六进制")
        return value


class PriorUnresolvedMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_text: str
    resolution_status: str
    source_turn: int = Field(ge=1)
    candidate_names: tuple[str, ...] = ()

    @field_validator("raw_text", "resolution_status")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class PriorContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    entities: tuple[PriorEntity, ...] = ()
    facts: tuple[PriorFact, ...] = ()
    unresolved: tuple[PriorUnresolvedMention, ...] = ()

    def entities_of_type(self, entity_type: str) -> tuple[PriorEntity, ...]:
        """只返回可信、active、catalog_valid 的实体；不猜、不取 top-1。"""
        return tuple(entity for entity in self.entities
                     if entity.entity_type == entity_type
                     and entity.active and entity.catalog_valid
                     and entity.source in TRUSTED_ENTITY_SOURCES)

    def trusted_facts(self) -> tuple[PriorFact, ...]:
        return tuple(fact for fact in self.facts
                     if fact.source in TRUSTED_FACT_SOURCES
                     and bool(fact.evidence_identity))


def invalid_prior_entities(context: PriorContext, catalog_names: dict[str, str]) -> list[str]:
    """用 catalog 的 entity_id → canonical_name 检查 prior entity 是否被篡改/不存在。"""
    bad = []
    for entity in context.entities:
        if catalog_names.get(entity.entity_id) != entity.canonical_name:
            bad.append(entity.entity_id)
    return bad


def validate_prior_context(context: PriorContext, catalog_names: dict[str, str]) -> None:
    """装载门：任何不可信/被篡改的前序实体都拒绝；不静默修。"""
    bad = invalid_prior_entities(context, catalog_names)
    if bad:
        raise ValueError("prior_confirmed_entities 的 entity_id 不在 catalog 或名称不符：%s"
                         % bad)
    for fact in context.facts:
        if fact.source not in TRUSTED_FACT_SOURCES:
            raise ValueError("prior fact 来源不可信：%s" % fact.source)
