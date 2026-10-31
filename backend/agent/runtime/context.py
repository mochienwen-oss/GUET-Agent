# -*- coding: utf-8 -*-
"""Run-time context passed to tools that need the authenticated owner."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeContext:
    """一次工具调用可用的身份与文件上下文。

    ★ 工具不得自行从请求体推断归属；只允许用这里的 user_id / conversation_id
      去数据库校验文件，从而保证用户之间不可交叉读取。
    """

    user_id: int
    conversation_id: int
    attachments: tuple[str, ...] = ()

    def knows(self, file_id: str) -> bool:
        return str(file_id) in self.attachments
