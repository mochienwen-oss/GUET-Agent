# -*- coding: utf-8 -*-
"""结构化工具调用验证与参数规范化 —— **纯函数、不接图、不执行工具**（设计 §17.9）。

输入：原始 tool calls + `QueryResolutionEnvelope`。
输出：`raw_args` / `canonical_args` **双留痕** + 每个参数的
`mention_id` / `entity_id` / `instance_ids` / `narrowed_by`。

它**只**判断「这次调用能不能安全地发出去」，以及「发出去时参数该写什么」。
它**不**调 Registry、**不**执行工具、**不**碰 budget / duplicate 流程
（只提供基于 `canonical_args` 的纯函数 key，接入留给后续 Task）。

★ 三种决策，**没有第四种**（ruling 8）：

    allow    可以调用；`canonical_args` 齐备
    clarify  参数对应的实体**没被唯一确认**（歧义 / 未知 / scope 冲突 / 弱证据）
             —— 明确要澄清，**不得**伪装成 no_evidence / irrelevant_result / tool_failed
    reject   调用**本身**不成立（未知工具 / 缺参 / 多参 / 类别错 / 参数在问句里找不到 /
             专业重复）—— 整次不做，**不得**部分执行

★ 拒绝的调用**不产出** `canonical_args`（返回 `{}`）—— 免得下游把它误当可执行。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import entities.entity_prior as entity_prior
import entities.entity_resolver as ER

VALIDATION_VERSION = "entity_calls/1.0.0"

CERTIFIED_TIERS = ER.CERTIFIED_TIERS
WEAK_TIERS = ER.WEAK_TIERS

#: 需要实体解析的结构化工具：参数 → (期望 entity_type, 是否为列表)
STRUCTURED_ARGS = {
    "query_course": {"major": ("major", False), "course": ("course", False)},
    "list_courses": {"major": ("major", False)},
    "compare_course": {"course": ("course", False), "majors": ("major", True)},
    "query_major_requirements": {"major": ("major", False)},
}
#: 结构化工具里**不参与解析**的旁路参数（原样透传）
PASSTHROUGH_ARGS = {
    "list_courses": ("semester", "module", "required", "core_only"),
}
#: 不参与实体参数验证、保持原参数通过的工具。
OPAQUE_TOOLS = ("search_regulations", "query_empty_classroom", "create_ppt", "calculate")

DECISIONS = ("allow", "clarify", "reject")

#: 列表参数至少要有几个元素（与 compare_course 的 schema 校验器同口径）
MIN_LIST_ITEMS = 2


@dataclass(frozen=True)
class ArgBinding:
    arg: str
    index: int | None
    mention_id: str | None
    entity_id: str | None
    instance_ids: tuple
    entity_type: str | None
    canonical_name: str | None
    kind: str | None
    narrowed_by: str | None
    narrowed_from: tuple


@dataclass(frozen=True)
class CallValidation:
    tool: str
    decision: str
    reason: str | None
    raw_args: dict
    canonical_args: dict
    bindings: tuple
    clarification: dict | None
    validation_version: str


class _Unbound:
    """一个参数没能绑定到「已唯一确认」的实体。"""

    __slots__ = ("kind", "reason", "clarification")

    def __init__(self, kind, reason, clarification=None):
        self.kind = kind                    # "reject" | "clarify"
        self.reason = reason
        self.clarification = clarification


def _binding_of(arg, index, mention, entity_id, instance_ids, entity_type,
                canonical_name, kind, narrowed_by, narrowed_from):
    return ArgBinding(arg=arg, index=index, mention_id=mention.mention_id,
                      entity_id=entity_id, instance_ids=tuple(instance_ids),
                      entity_type=entity_type, canonical_name=canonical_name,
                      kind=kind, narrowed_by=narrowed_by,
                      narrowed_from=tuple(narrowed_from))


def _clarify(mention, arg, index, reason):
    """澄清决策 —— 带上**可复现**的候选名，够 Task 4 写澄清话术。"""
    names = sorted({c.canonical_name for c in mention.candidates})
    return _Unbound("clarify", reason, {
        "arg": arg, "index": index, "mention_id": mention.mention_id,
        "raw_text": mention.raw_text,
        "resolution_status": mention.resolution_status,
        "support_tier": mention.support_tier,
        "candidate_names": names[:8],
        "candidate_entity_types": sorted({c.entity_type for c in mention.candidates}),
    })


def _bind_arg(envelope, value, expected, arg, index, *, tool=None,
              prior_context=None):
    """把一个参数值绑到**已唯一确认**的实体上。绑定失败一律带原因返回。

    ★ prior_context 只用来**验证模型已经完整给出的参数值**是否与前序唯一已确认实体
      一致；它**绝不补齐**缺失参数（缺失参数在 `_bad_argument` 已经 reject）。
    """
    if not isinstance(value, str) or not value.strip():
        return None, _Unbound("reject", "bad_arg:%s" % _label(arg, index))
    key = ER._join_key(value)
    matched = [m for m in envelope.mentions if ER._join_key(m.raw_text) == key]
    # An explicit user negation is not a positive parameter source, even if
    # that entity remains active in the previous turn's context.
    positive = [m for m in matched if not envelope.original_query[:m.start].rstrip().endswith("不是")]
    if matched and not positive:
        return None, _Unbound("reject", "arg_negated:%s" % _label(arg, index))
    matched = positive
    if not matched:
        # 当前问句没有逐字出现该参数：只有「前序唯一已确认实体」才可作为来源。
        if prior_context is None:
            return None, _Unbound("reject", "arg_unmapped:%s" % _label(arg, index))
        prior = prior_context.entities_of_type(expected)
        if not prior:
            return None, _Unbound("reject", "arg_unmapped:%s" % _label(arg, index))
        if len(prior) > 1:
            return None, _Unbound(
                "clarify", "prior_ambiguous:%s" % _label(arg, index),
                {"arg": arg, "index": index, "reason": "prior_ambiguous",
                 "candidate_names": sorted({entity.canonical_name for entity in prior}),
                 "candidate_entity_types": [expected]})
        entity = prior[0]
        if ER._join_key(entity.canonical_name) != key:
            return None, _Unbound(
                "reject", "prior_entity_mismatch:%s" % _label(arg, index),
                {"arg": arg, "index": index, "reason": "prior_entity_mismatch",
                 "prior_canonical_name": entity.canonical_name})
        # 当前问句若已确定另一同类型实体，不得让 prior 覆盖它。
        selected = [m.selected for m in envelope.mentions
                    if m.selected is not None and m.selected.entity_type == expected
                    and not envelope.original_query[:m.start].rstrip().endswith("不是")]
        if selected and all(one.canonical_name != entity.canonical_name
                            for one in selected):
            return None, _Unbound(
                "reject", "prior_current_conflict:%s" % _label(arg, index),
                {"arg": arg, "index": index, "reason": "prior_current_conflict",
                 "prior_canonical_name": entity.canonical_name,
                 "current_canonical_names": sorted(one.canonical_name
                                                   for one in selected)})
        mention = type("_PriorMention", (), {
            "mention_id": "prior:%s" % entity.entity_id})()
        return _binding_of(arg, index, mention, entity.entity_id,
                           (entity.entity_id,), expected, entity.canonical_name,
                           "instance", "prior_context", (entity.source,)), None
    mention = matched[0]              # 同文本必同解（Task 3 已锁定），取最靠前的 span

    if mention.selected is not None:
        if mention.selected.entity_type != expected:
            return None, _clarify(mention, arg, index, "type_mismatch")
        selected = mention.selected
        return _binding_of(arg, index, mention, selected.entity_id,
                           selected.instance_ids, selected.entity_type,
                           selected.canonical_name, selected.kind, None, ()), None

    # ★ 规范实体全局存在、但目标专业作用域内不存在：
    #   只对 query_course 的 course 槽位放行。其余工具/槽位仍按未决澄清，
    #   不把「不在该专业」偷偷升级成其他工具也能用的放行。
    if mention.resolution_status == "resolved_scope_absent":
        info = mention.scope_absent or {}
        if (tool == "query_course" and expected == "course" and arg == "course"
                and info.get("entity_type") == "course"
                and info.get("canonical_name")):
            return _binding_of(
                arg, index, mention, None,
                tuple(info.get("instance_ids") or ()), "course",
                info["canonical_name"], "name", "scope_absent",
                (info.get("reason"),)), None
        return None, _clarify(mention, arg, index, mention.resolution_status)

    # ★ 跨类型并列 → 按**参数期望的类型**收窄（ruling 5）。
    #   ruling 6：只有**确定性层级**（T1/T2/T3）才谈得上收窄；T4–T7 一律不通过。
    if mention.resolution_status == "ambiguous" \
            and mention.support_tier in CERTIFIED_TIERS:
        top = [c for c in mention.candidates if c.tier == mention.support_tier]
        types = tuple(sorted({c.entity_type for c in top}))
        kept = [c for c in top if c.entity_type == expected]
        if len(types) > 1 and len(kept) == 1:
            candidate = kept[0]
            if len(candidate.instance_ids) == 1:
                kind, entity_id = "instance", candidate.instance_ids[0]
            else:
                kind, entity_id = "name", None
            return _binding_of(arg, index, mention, entity_id,
                               candidate.instance_ids, candidate.entity_type,
                               candidate.canonical_name, kind, "arg_type",
                               types), None
    return None, _clarify(mention, arg, index, mention.resolution_status)


def _label(arg, index):
    return arg if index is None else "%s[%d]" % (arg, index)


def _result(tool, decision, reason, raw, canonical, bindings, clarification=None):
    return CallValidation(tool=tool, decision=decision, reason=reason,
                          raw_args=dict(raw), canonical_args=canonical,
                          bindings=tuple(bindings), clarification=clarification,
                          validation_version=VALIDATION_VERSION)


def _bad_argument(tool, args, spec):
    """缺参 / 多参 / 类型错 —— 返回 `None` 或带原因的标签。"""
    allowed = set(spec) | set(PASSTHROUGH_ARGS.get(tool, ()))
    for arg in spec:
        if arg not in args:
            return arg
    for arg in sorted(set(args) - allowed):
        return arg
    for arg, (_, is_list) in spec.items():
        value = args[arg]
        if is_list:
            if not isinstance(value, list) or len(value) < MIN_LIST_ITEMS:
                return arg
            if not all(isinstance(item, str) and item.strip() for item in value):
                return arg
        elif not isinstance(value, str) or not value.strip():
            return arg
    return None


def _search_args(raw, envelope):
    """为规章检索构造双通道查询，不把未决候选当成事实。

    semantic query 只在模型沿用原问句时换成 resolver 已生成的规范问句；
    BM25 keywords 保留调用方原词，并追加**已选中**提及的原词与规范名。
    T4–T7 没有 selected，因此天然不会进入扩展。
    """
    canonical = dict(raw)
    if canonical.get("query") == envelope.original_query:
        canonical["query"] = envelope.normalized_query

    selected_terms = []
    for mention in envelope.mentions:
        if mention.selected is None:
            continue
        selected_terms.extend((mention.raw_text, mention.selected.canonical_name))
    if not selected_terms:
        return canonical

    keywords = list(canonical.get("keywords") or [])
    seen = set(keywords)
    for term in selected_terms:
        if term and term not in seen:
            keywords.append(term)
            seen.add(term)
    canonical["keywords"] = keywords
    return canonical


def validate_call(tool, args, envelope, prior_context=None) -> CallValidation:
    """验证**一次**工具调用。**纯函数**：不改 envelope、不执行任何东西。

    `prior_context` 可为 `entity_prior.PriorContext` 或同形 dict；只用于验证
    **模型已经完整给出**的参数，不补齐缺失参数。
    """
    raw = dict(args or {})
    if prior_context is not None and not isinstance(
            prior_context, entity_prior.PriorContext):
        try:
            prior_context = entity_prior.PriorContext.model_validate(prior_context)
        except Exception as error:                     # noqa: BLE001
            return _result(tool, "reject", "prior_context_invalid", raw, {}, (),
                           {"reason": "prior_context_invalid",
                            "detail": "%s: %s" % (type(error).__name__, error)})
    if tool == "search_regulations":
        canonical = _search_args(raw, envelope)
        reason = "resolved_query" if canonical != raw else "passthrough"
        return _result(tool, "allow", reason, raw, canonical, ())
    if tool in OPAQUE_TOOLS:
        return _result(tool, "allow", "passthrough", raw, dict(raw), ())
    spec = STRUCTURED_ARGS.get(tool)
    if spec is None:
        return _result(tool, "reject", "unknown_tool", raw, {}, ())

    bad = _bad_argument(tool, raw, spec)
    if bad is not None:
        return _result(tool, "reject", "bad_arg:%s" % bad, raw, {}, ())

    bindings, canonical, failures = [], {}, []
    for arg, (expected, is_list) in spec.items():
        if not is_list:
            binding, failure = _bind_arg(envelope, raw[arg], expected, arg, None,
                                         tool=tool, prior_context=prior_context)
            if failure:
                failures.append((arg, failure))
                continue
            bindings.append(binding)
            canonical[arg] = binding.canonical_name
            continue
        names, seen = [], {}
        for index, value in enumerate(raw[arg]):
            binding, failure = _bind_arg(envelope, value, expected, arg, index,
                                         tool=tool, prior_context=prior_context)
            if failure:
                failures.append((arg, failure))
                break
            if binding.canonical_name in seen:
                failures.append((arg, _Unbound(
                    "reject", "duplicate_major",
                    {"arg": arg, "index": index, "duplicate_of": seen[binding.canonical_name],
                     "canonical_name": binding.canonical_name})))
                break
            seen[binding.canonical_name] = index
            bindings.append(binding)
            names.append(binding.canonical_name)
        canonical[arg] = names

    # ★ 全有或全无：任何一项没成，**整次不做**（ruling 7），绝不部分执行
    if failures:
        arg, failure = failures[0]
        if failure.kind == "reject":
            return _result(tool, "reject", failure.reason, raw, {}, (),
                           failure.clarification)
        return _result(tool, "clarify", "unresolved:%s" % arg, raw, {}, (),
                       failure.clarification)

    for arg in PASSTHROUGH_ARGS.get(tool, ()):
        if arg in raw:
            canonical[arg] = raw[arg]
    if any(b.narrowed_by == "scope_absent" for b in bindings):
        reason = "canonical_entity_absent_in_scope"
    elif any(b.narrowed_by == "prior_context" for b in bindings):
        reason = "prior_context_bound"
    else:
        reason = "ok"
    return _result(tool, "allow", reason, raw, canonical, bindings)


def validate_calls(calls, envelope, prior_context=None) -> tuple:
    """验证一批调用（输入形状：`[{"name": ..., "arguments": {...}}]`），保持原顺序。"""
    return tuple(validate_call(call.get("name"), call.get("arguments") or {}, envelope,
                               prior_context=prior_context)
                 for call in calls)


def canonical_call_key(tool, canonical_args):
    """基于 `canonical_args` 的稳定鉴权键（本 Task **不**接入 budget / duplicate）。

    ★ 与 Registry / budgets 的 `call_key` 用**同一个** `args_hash` —— 两处各写一套
      必然漂移，漂移的后果是「重复同参调用率 0%」在没有重复时也报绿。
    """
    return (tool, _args_hash(canonical_args or {}))


_ARGS_HASH = None


def _args_hash(args: dict) -> str:
    global _ARGS_HASH
    if _ARGS_HASH is None:
        from agent.core.evidence import args_hash

        _ARGS_HASH = args_hash
    return _ARGS_HASH(args)


def to_dict(validation) -> dict:
    """验证结果 → 纯数据（无时间戳、无随机量）。"""
    return {
        "tool": validation.tool, "decision": validation.decision,
        "reason": validation.reason,
        "raw_args": validation.raw_args,
        "canonical_args": validation.canonical_args,
        "clarification": validation.clarification,
        "validation_version": validation.validation_version,
        "bindings": [{"arg": b.arg, "index": b.index, "mention_id": b.mention_id,
                      "entity_id": b.entity_id, "instance_ids": list(b.instance_ids),
                      "entity_type": b.entity_type, "canonical_name": b.canonical_name,
                      "kind": b.kind, "narrowed_by": b.narrowed_by,
                      "narrowed_from": list(b.narrowed_from)}
                     for b in validation.bindings],
    }


def dump_validation(validation) -> str:
    return json.dumps(to_dict(validation), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
