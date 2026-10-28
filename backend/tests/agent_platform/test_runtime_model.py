# tests/agent_platform/test_runtime_model.py —— 唯一模型口的形状保真
#
# ★ 「门必须先证明自己看得见输入」：这两条断言的是**真正发出去的那份 tools**，
#   不是某个中间副本。阶段2 就是因为没人看发出的是什么，schema 被悄悄改写了两周。
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime import model, registry  # noqa: E402


def test_decide_sends_registry_schemas_verbatim(monkeypatch):
    # This frozen shape is the explicitly selected local/LoRA protocol.
    from core.config import settings
    monkeypatch.setattr(settings, "llm_api_config_path", "")
    seen = {}

    async def fake_chat_with_tools(messages, tools):
        seen["messages"] = messages
        seen["tools"] = tools
        return {"type": "text", "content": "ok"}

    monkeypatch.setattr(model.llm_client, "chat_with_tools", fake_chat_with_tools)
    messages = [{"role": "user", "content": "问"}]
    result = asyncio.run(model.decide(messages))

    assert result == {"type": "text", "content": "ok"}
    assert json.dumps(seen["tools"], ensure_ascii=False) == \
           json.dumps(registry.schemas(), ensure_ascii=False)
    # messages 也必须**原样**送到 —— 加工 messages 就是改训练形状
    assert seen["messages"] is messages


def test_answer_sends_registry_schemas_verbatim(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "llm_api_config_path", "")
    seen = {}

    async def fake_stream_chat(messages, tools=None, tool_choice=None):
        seen["tools"] = tools
        seen["tool_choice"] = tool_choice
        yield ("delta", "你好")

    monkeypatch.setattr(model.llm_client, "stream_chat", fake_stream_chat)

    async def _collect():
        return [item async for item in model.answer([{"role": "user", "content": "问"}])]

    items = asyncio.run(_collect())

    assert items == [("delta", "你好")]
    assert json.dumps(seen["tools"], ensure_ascii=False) == \
           json.dumps(registry.schemas(), ensure_ascii=False)
    assert seen["tool_choice"] == "auto"


def test_answer_passes_tool_choice_through_unchanged(monkeypatch):
    """收尾用的 `"none"` 必须能原样传下去，且**只有调用方**能决定它。"""
    seen = {}

    async def fake_stream_chat(messages, tools=None, tool_choice=None):
        seen["tool_choice"] = tool_choice
        yield ("delta", "收尾")

    monkeypatch.setattr(model.llm_client, "stream_chat", fake_stream_chat)

    async def _collect():
        return [item async for item in
                model.answer([{"role": "user", "content": "问"}], "none")]

    asyncio.run(_collect())
    assert seen["tool_choice"] == "none"
