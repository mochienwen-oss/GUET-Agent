"""Current API-native registry: retrieval only, with execution validation."""
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from agent.runtime.registry import REGISTRY, ToolSpec, call, schemas


class FakeArgs(BaseModel):
    value: str = Field(min_length=1)


def test_runtime_only_registers_supported_chat_tools():
    assert list(REGISTRY) == ["search_regulations"]
    assert [item["function"]["name"] for item in schemas()] == list(REGISTRY)
    names = json.loads(Path(__file__).with_name("runtime_tool_names.json").read_text(encoding="utf-8"))
    assert names == list(REGISTRY)


@pytest.mark.parametrize("name", ["query_course", "compare_course", "list_courses",
    "query_major_requirements", "query_empty_classroom", "calculate"])
def test_retired_tools_cannot_execute(name):
    result = call(name, {})
    assert result["status"] == "error"
    assert result["error"]["code"] == "unknown_tool"


@pytest.mark.parametrize("arguments", [{}, {"value": ""}])
def test_invalid_arguments_do_not_execute(monkeypatch, arguments):
    seen = []
    def handler(**values):
        seen.append(values)
        return {"status": "success", "data": values}
    monkeypatch.setitem(REGISTRY, "fake", ToolSpec("fake", "test", FakeArgs, handler))
    result = call("fake", arguments)
    assert result["error"]["code"] == "invalid_args"
    assert seen == []


def test_validated_arguments_reach_the_handler(monkeypatch):
    def handler(**values):
        return {"status": "success", "data": values}
    monkeypatch.setitem(REGISTRY, "fake", ToolSpec("fake", "test", FakeArgs, handler))
    assert call("fake", {"value": "内容"}) == {"status": "success", "data": {"value": "内容"}}


def test_handler_failure_is_a_tool_error(monkeypatch):
    def handler(**values):
        raise RuntimeError("offline failure")
    monkeypatch.setitem(REGISTRY, "fake", ToolSpec("fake", "test", FakeArgs, handler))
    result = call("fake", {"value": "内容"})
    assert result["error"]["code"] == "tool_failed"


def test_cancelled_execution_is_not_returned_as_success(monkeypatch):
    def handler(**values):
        raise InterruptedError("cancelled")
    monkeypatch.setitem(REGISTRY, "fake", ToolSpec("fake", "test", FakeArgs, handler))
    with pytest.raises(InterruptedError):
        call("fake", {"value": "内容"})
