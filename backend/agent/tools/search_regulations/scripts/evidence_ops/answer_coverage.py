# -*- coding: utf-8 -*-
"""S1 证据覆盖信号 —— **只产出信号，不改变任何检索决策**。

## 为什么需要它（2026-10-20 实测）

`reg-10b`（国境外交流能否延期）只调了 1 次 `search_regulations` 就进收尾：

    gate.status = supported, gate.reason = supported_high,
    best_rerank = 0.9935, tier = high

但 gate 衡量的是**文档与问题的相关性**，不是**问题是否被完整回答**。
模型看到"检索成功"，没有任何理由再查一次 —— ReAct 循环于是提前终止，
把《教职工进修管理办法》的条款当成答案的一部分。

本模块补的正是这个缺失信号：**问题里的哪些词，证据里根本没出现。**

## 设计约束

- **纯函数**：不读配置、不碰 Qdrant、不调模型。因此可离线单测。
- **不判定对错**：只报"覆盖了什么、缺什么"，由模型自己决定要不要再查。
  （硬拦留到 S3，且要等 S2 的误伤率数据。）
- **复用既有分词**：`bm25.tokenize` 已经把机关简称、文号都处理好了，
  这里不再造第二套切词。
- 观测文本必须短：missing 最多 5 个词，否则 observation 会盖过证据本身。
"""
from __future__ import annotations

from agent.tools.search_regulations.scripts.index_ops import bm25

#: 疑问词、指代词、客套话 —— 它们不是"事实要点"，缺了不代表答不全。
#: 宁可少收：多收会把真实缺口淹掉。
QUESTION_STOPWORDS = frozenset("""
请问 请 问 我想 想知道 知道 麻烦 帮我 帮忙 一下 一些 这个 那个 这些 那些
怎么 怎样 如何 什么 哪些 哪个 多少 多久 几点 是否 能否 可以吗 吗 呢 吧 啊
学校 我校 本校 咱们 我们 你 我 他 她 它 的 了 和 及 与 或 在 是 为 对 按
""".split())

#: 缺词最多列几个 —— 观测文本要能一眼看完。
MAX_MISSING = 5

#: 词太短没有区分度（单字），太长多半是切错了。
_MIN_TERM_LEN = 2
_MAX_TERM_LEN = 12


def _terms(text: str) -> list[str]:
    """问题 → 实词列表。去停用词、去重、保序。"""
    out, seen = [], set()
    for token in bm25.tokenize(text or ""):
        term = token.strip()
        if not (_MIN_TERM_LEN <= len(term) <= _MAX_TERM_LEN):
            continue
        if term in QUESTION_STOPWORDS or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def coverage_signal(question: str, blocks: list) -> dict:
    """查询词的字面匹配比例，不是问题主张覆盖或答案充分度。

    `blocks` 是 `search_regulations_result` 里的 blocks（每项含 text）。
    返回 `{coverage_ratio, covered_terms, missing_terms, question_terms}`，
    可直接放进 `ToolResult.data`。
    """
    terms = _terms(question)
    haystack = "\n".join((b or {}).get("text") or "" for b in blocks or [])
    if not terms:
        return {"coverage_ratio": 0.0, "covered_terms": [],
                "missing_terms": [], "question_terms": []}
    covered = [t for t in terms if t in haystack]
    missing = [t for t in terms if t not in haystack]
    return {
        "coverage_ratio": round(len(covered) / len(terms), 4),
        "covered_terms": covered,
        "missing_terms": missing[:MAX_MISSING],
        "question_terms": terms,
    }
