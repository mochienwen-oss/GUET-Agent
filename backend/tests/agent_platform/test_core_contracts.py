# tests/agent_platform/test_core_contracts.py —— 契约本身 + 框架无关性
#
# ★ `test_core_sources_do_not_import_frameworks` 直接执行阶段0.5 §1.3 的**硬禁令**：
#   LangChain / LangGraph 的内部消息格式**不得反向绑架训练侧** ——
#   一旦契约层依赖框架，这条线就守不住了。
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core.contracts import Evidence, Source, ToolResult  # noqa: E402

_CORE_DIR = Path(__file__).resolve().parents[2] / "agent" / "core"
_FRAMEWORK_IMPORT = re.compile(r"^\s*(?:from|import)\s+(langchain|langgraph|langsmith)\b", re.M)


def test_core_sources_do_not_import_frameworks():
    """契约与适配层**必须框架无关**。"""
    offenders = []
    for p in sorted(_CORE_DIR.glob("*.py")):
        if _FRAMEWORK_IMPORT.search(p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert not offenders, f"agent/core 引入了框架依赖：{offenders}"


def test_source_defaults_are_all_optional():
    s = Source()
    assert s.chunk_id is None and s.doc_id is None and s.text == ""
    assert s.heading_path == [] and s.text_hash == ""
    assert s.line is None and s.score is None


def test_tool_result_success_shape():
    r = ToolResult(tool="t", ok=True)
    assert r.data == {} and r.sources == [] and r.error is None and r.status == "success"


def test_contracts_are_frozen():
    """冻结值对象 —— 防止下游就地改 Evidence 的字段。"""
    import dataclasses
    for cls in (Source, ToolResult, Evidence):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True


def test_evidence_carries_source_and_attribution():
    e = Evidence(evidence_id="abc", source=Source(title="甲文件"), tool="search_regulations",
                 args={"query": "q"}, snippet="片段", retrieved_at="2026-10-12T00:00:00")
    assert e.source.title == "甲文件"
    assert e.tool == "search_regulations"
    assert e.args == {"query": "q"}
