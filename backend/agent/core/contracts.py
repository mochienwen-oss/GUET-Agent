# -*- coding: utf-8 -*-
"""Framework-independent tool and evidence contracts."""
from dataclasses import dataclass, field
from typing import Literal

from .errors import ToolError


ToolStatus = Literal[
    "success",
    "no_evidence",
    "partial_evidence",
    "irrelevant_result",
    "tool_failed",
]
TOOL_STATUSES = frozenset({
    "success", "no_evidence", "partial_evidence", "irrelevant_result", "tool_failed"
})


@dataclass(frozen=True)
class Source:
    """一条规范化来源；Evidence 只能从该结构派生。"""

    doc_id: str | None = None
    chunk_id: str | None = None
    title: str = ""
    heading_path: list = field(default_factory=list)
    text: str = ""
    text_hash: str = ""
    line: int | None = None
    score: float | None = None
    source_span: list = field(default_factory=list)
    source_sha256: str = ""
    block_type: str = ""
    row_number: int | None = None


@dataclass(frozen=True)
class ToolResult:
    """工具执行的结构化结果；文本渲染不属于本类型。"""

    tool: str
    ok: bool
    status: ToolStatus = "success"
    data: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    error: ToolError | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in TOOL_STATUSES:
            raise ValueError("unknown tool status: %r" % (self.status,))
        if self.status == "tool_failed" and self.ok:
            raise ValueError("tool_failed result cannot be ok")
        if self.status != "tool_failed" and not self.ok:
            raise ValueError("only tool_failed results may set ok=False")


@dataclass(frozen=True)
class Evidence:
    """证据台账条目，由 ToolResult.sources 派生。"""

    evidence_id: str
    source: Source
    tool: str
    args: dict
    snippet: str
    retrieved_at: str
