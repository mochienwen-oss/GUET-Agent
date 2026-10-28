"""Static Registry: validation, execution, schema, and rendering boundaries."""
import json
import sys
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import ToolResult  # noqa: E402
from agent.runtime.registry import (  # noqa: E402
    REGISTRY, ToolSpec, call, render, schemas, training_schemas,
)

# 冻结参照：`train_v7.jsonl` 每行 `tools` 列的取值（1756 行唯一值只有一种）。
_GOLDEN = json.loads(
    Path(__file__).with_name("golden_tool_schemas.json").read_text(encoding="utf-8"))

# 当前运行时工具名称契约：只记有序名称，不复制第二份大型 schema 快照。
_TOOL_NAMES = Path(__file__).with_name("runtime_tool_names.json")


class FakeArgs(BaseModel):
    major: str = Field(min_length=1)
    course: str = Field(min_length=1)


def _spec(name, fn):
    wrapped = tool(name, args_schema=FakeArgs, description="fake test tool")(fn)
    return ToolSpec(wrapped, lambda result: str(result.data),
                    {"type": "object", "properties": {}, "required": []})


def test_runtime_tools_are_registered():
    assert list(REGISTRY) == [
        "search_regulations", "query_course", "compare_course",
        "query_empty_classroom", "create_ppt", "list_courses",
        "query_major_requirements", "calculate",
    ]
    assert [item["function"]["name"] for item in schemas()] == list(REGISTRY)


def test_runtime_tool_names_contract_lists_tools_in_order():
    assert json.loads(_TOOL_NAMES.read_text(encoding="utf-8")) == [
        "search_regulations", "query_course", "compare_course",
        "query_empty_classroom", "create_ppt", "list_courses",
        "query_major_requirements", "calculate",
    ]


def test_runtime_tool_names_contract_matches_the_registry_itemwise():
    """唯一允许导入运行时 Registry 的一致性门。

    `runtime_tool_names.json` 是 Task 2 inventory 构建器的**工具集合输入**，
    构建器自己**不得**导入 Registry（否则那份离线产物会被 torch / qdrant 拖住）。
    代价是契约文件可能悄悄与真实 Registry 脱节 —— 这条就是补上的门：
    逐项、逐序等于 `list(REGISTRY)`。原 4 工具 frozen schema 保持不动。
    """
    assert json.loads(_TOOL_NAMES.read_text(encoding="utf-8")) == list(REGISTRY)


def test_shared_tool_schemas_are_byte_identical_to_the_frozen_training_column():
    """门必须先证明自己看得见输入 —— 金标准读到 0 条要报错，不是报绿。

    这条守的是**训练分布**：`schemas()` 是运行时发给 vLLM 的 `tools`，它一旦与
    LoRA 训练时见过的那一列不再逐字相同，模型就在没训过的分布上跑，而且不报错。
    2026-10-13 实测过一次真的漂移（描述被改写、键序重排、多出 minLength），
    当时没有任何测试看得见 —— 这条就是补上的门。
    """
    assert len(_GOLDEN) == 4, "冻结金标准条数变了 —— 先确认提取脚本读的是不是 tools 列"
    frozen = training_schemas()
    assert json.dumps(frozen, ensure_ascii=False) == json.dumps(_GOLDEN, ensure_ascii=False)
    # 顺序也钉住：顺序变了，发给 vLLM 的 token 序列就变
    assert [item["function"]["name"] for item in frozen] == [
        item["function"]["name"] for item in _GOLDEN]
    # 后加工具不在冻结列里，但必须在全量 schemas() 里
    assert [item["function"]["name"] for item in schemas()][4:] == [
        "create_ppt", "list_courses", "query_major_requirements", "calculate"]


def test_runtime_search_schema_can_evolve_without_rewriting_v7_snapshot():
    runtime = schemas()[0]["function"]["parameters"]
    frozen = training_schemas()[0]["function"]["parameters"]
    assert "keywords" in runtime["properties"]
    assert "keywords" not in frozen["properties"]


def test_spec_without_frozen_parameters_is_rejected():
    import pytest
    from agent.runtime.registry import register

    wrapped = tool("no_params", args_schema=FakeArgs, description="x")(lambda **kwargs: None)
    with pytest.raises(ValueError, match="frozen parameters"):
        register(ToolSpec(wrapped, lambda result: "", {}))


def test_unknown_tool_returns_structured_failure():
    result = call("绝对不存在的工具", {"a": 1})
    assert result.ok is False and result.status == "tool_failed"
    assert result.error.code == "unknown_tool"
    assert result.sources == []


def test_invalid_args_does_not_execute(monkeypatch):
    seen = []

    def fake(major: str, course: str) -> ToolResult:
        seen.append((major, course))
        return ToolResult(tool="fake_card", ok=True)

    monkeypatch.setitem(REGISTRY, "fake_card", _spec("fake_card", fake))
    result = call("fake_card", {"major": "计算机科学与技术"})
    assert result.error.code == "invalid_args"
    assert result.status == "tool_failed"
    assert seen == []


def test_empty_required_value_is_rejected_without_execution(monkeypatch):
    seen = []

    def fake(major: str, course: str) -> ToolResult:
        seen.append((major, course))
        return ToolResult(tool="fake_blank", ok=True)

    monkeypatch.setitem(REGISTRY, "fake_blank", _spec("fake_blank", fake))
    result = call("fake_blank", {"major": "", "course": "数据库系统原理"})
    assert result.error.code == "invalid_args"
    assert seen == []


def test_zero_arg_tool_accepts_none():
    result = call("query_empty_classroom", None)
    assert result.ok is True and result.status == "success"
    assert result.data["kind"] == "pointer"
    assert result.sources == []
    assert result.meta["latency_ms"] is not None


def test_business_miss_is_no_evidence_not_tool_failed():
    result = call("query_course", {"major": "绝对不存在的专业XYZ", "course": "任意课"})
    assert result.ok is True and result.status == "no_evidence"
    assert result.error is None
    assert result.data["error"] == "major_not_found"
    assert isinstance(result.data["candidates"], list)


def test_tool_exception_becomes_tool_failed(monkeypatch):
    def boom(major: str, course: str) -> ToolResult:
        raise RuntimeError("连接超时")

    monkeypatch.setitem(REGISTRY, "boom_tool", _spec("boom_tool", boom))
    result = call("boom_tool", {"major": "x", "course": "y"})
    assert result.error.code == "tool_failed"
    assert result.status == "tool_failed"
    assert "连接超时" in result.error.detail


def test_invalid_args_rendering_keeps_failure_boundary():
    result = call("query_course", {"major": "计算机科学与技术"})
    text = render(result)
    assert "未检索到" not in text
    assert "不代表学校没有相关规定" in text
    assert "服务" not in text
    assert "course" in text or "参数" in text


def test_duplicate_registration_is_rejected():
    import pytest
    from agent.runtime.registry import register

    with pytest.raises(ValueError, match="duplicate tool name"):
        register(REGISTRY["query_course"])
