# -*- coding: utf-8 -*-
"""较早历史的摘要提供者：独立调用面，摘要无事实权限。

★ 主 Agent 不在回答过程中更新摘要；本模块只接收**已被窗口裁掉**的完整消息。
★ 不在这里发起网络请求：真实生成函数由调用方注入，测试用替身。
"""
from __future__ import annotations

import hashlib
import json

from conversations import conversation_context as CC


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


class ConversationSummaryProvider:
    """`summarize_fn(previous, messages) -> str` 是唯一的模型调用边界。"""

    def __init__(self, summarize_fn=None):
        self._summarize_fn = summarize_fn

    def summarize(self, previous: CC.CompressedHistory | None,
                  messages) -> CC.CompressedHistory:
        messages = tuple(messages)
        if not messages:
            raise ValueError("没有可摘要的消息")
        if self._summarize_fn is None:
            raise RuntimeError("摘要提供者未配置生成函数")
        text = self._summarize_fn(previous, messages)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("摘要生成函数返回空文本")
        turns = [one.turn_index for one in messages]
        start = previous.covered_turns[0] if previous is not None else min(turns)
        source_hash = hashlib.sha256(
            _canonical([one.model_dump(mode="json") for one in messages])
            .encode("utf-8")).hexdigest()
        return CC.CompressedHistory(
            summary=text.strip(), covered_turns=(start, max(turns)),
            source_hash=source_hash,
            summary_sha256=hashlib.sha256(
                text.strip().encode("utf-8")).hexdigest())


def summarize_if_needed(context: CC.ConversationContext, window,
                        provider: ConversationSummaryProvider):
    """窗口没有丢消息时**不调用**摘要提供者（零调用是契约）。"""
    if not window.dropped_messages:
        return context.compressed_history
    return provider.summarize(context.compressed_history, window.dropped_messages)
