# -*- coding: utf-8 -*-
"""PPT 会话的上下文预算：把较早的工具结果折叠成摘要，避免每轮重发全部原文。

设计约束（改动前请先读完）：

★ 只改 ``role=tool`` 消息的 ``content``，**不删除消息、不调整顺序**。因此
  ``tool_call`` 与 ``tool_result`` 的配对始终成立，供应商 API 不会因为历史里
  有孤立的调用而被拒收。
★ 前两条（系统提示 + 工程目录）由 ``master_host`` 单独维护，本模块不碰。
★ 最近 ``keep_tail`` 条工具结果原样保留，保证当前这一步能直接用到刚读到的内容。
★ 折叠后的替身保留 ``status``、文件路径 / URL / 退出码和错误前若干字符，模型
  仍能判断"这一步做了什么、成没成"，只是拿不到正文。
"""
from __future__ import annotations

import json

#: 超过这个字符数才启动折叠。按中文与代码混合估算，约 60k tokens。
DEFAULT_BUDGET_CHARS = 180_000
#: 最近这么多条工具结果保持原样。
DEFAULT_KEEP_TAIL = 10
#: 小于这个长度的结果折叠收益太低，直接跳过。
MIN_FOLD_CHARS = 1_500
#: 错误结果保留的正文长度，避免模型重复执行同一条失败命令。
ERROR_KEEP_CHARS = 240


def total_chars(messages) -> int:
    """当前会发给模型的正文字符数（不含 tool_calls 结构本身）。"""
    return sum(len(message.get('content') or '') for message in messages)


def _locate(payload):
    """从工具结果里提取可用于重新定位的信息。"""
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    parts = []
    if data.get('path'):
        parts.append('文件 %s' % data['path'])
    if data.get('final_url') or data.get('url'):
        parts.append('网页 %s' % (data.get('final_url') or data.get('url')))
    if 'exit_code' in data:
        parts.append('命令退出码 %s' % data.get('exit_code'))
    if data.get('written_files'):
        parts.append('写入 %d 个文件' % len(data['written_files']))
    if data.get('sources') or payload.get('sources'):
        parts.append('检索结果 %d 条' % len(data.get('sources') or payload.get('sources') or []))
    return '；'.join(parts)


def _fold(content):
    """把一条工具结果压成短摘要。解析不了就退化成纯长度提示。"""
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return json.dumps({'status': 'folded', 'folded': True,
                           'summary': '较早的工具结果（原文 %s 字符）已折叠' % format(len(content), ',')},
                          ensure_ascii=False)
    if not isinstance(payload, dict):
        return json.dumps({'status': 'folded', 'folded': True,
                           'summary': '较早的工具结果（原文 %s 字符）已折叠' % format(len(content), ',')},
                          ensure_ascii=False)
    status = payload.get('status') or 'unknown'
    located = _locate(payload)
    stub = {'status': status, 'folded': True,
            'summary': ('%s：' % located if located else '') +
                       '原文 %s 字符已折叠以控制上下文，需要细节时按上述位置重新读取'
                       % format(len(content), ',')}
    error = payload.get('error') if isinstance(payload.get('error'), dict) else {}
    if status == 'error' and error.get('detail'):
        stub['detail'] = str(error['detail'])[:ERROR_KEEP_CHARS]
        if error.get('code'):
            stub['code'] = error['code']
    return json.dumps(stub, ensure_ascii=False)


def fold_tool_results(messages, *, budget_chars=DEFAULT_BUDGET_CHARS,
                      keep_tail=DEFAULT_KEEP_TAIL) -> dict:
    """总量超预算时，把较早的工具结果折叠。

    返回 ``{'before', 'after', 'folded'}``，便于在日志里核对实际效果。
    """
    before = total_chars(messages)
    if before <= budget_chars:
        return {'before': before, 'after': before, 'folded': 0}
    indexes = [index for index, message in enumerate(messages) if message.get('role') == 'tool']
    protected = set(indexes[-keep_tail:]) if keep_tail > 0 else set()
    folded = 0
    for index in indexes:
        if index in protected:
            continue
        content = messages[index].get('content') or ''
        if len(content) < MIN_FOLD_CHARS:
            continue
        messages[index]['content'] = _fold(content)
        folded += 1
    return {'before': before, 'after': total_chars(messages), 'folded': folded}
