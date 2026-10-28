"""Chat API keeps trusted prefixes and tool result bindings intact."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llm import llm_client


@pytest.mark.parametrize('stream', [False, True])
def test_planner_preserves_trusted_prefix_and_history_in_chat_api(monkeypatch, stream, api_config):
    api_config()
    messages = [{'role': 'system', 'content': '主指令'},
                {'role': 'system', 'content': 'Skill权限'},
                {'role': 'system', 'content': '【会话可信上下文】\nmajor | 人工智能 | active'},
                {'role': 'user', 'content': '计算机网络的指标'},
                {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_0', 'type': 'function',
                    'function': {'name': 'query_course', 'arguments': '{"major":"人工智能"}'}}]},
                {'role': 'tool', 'tool_call_id': 'call_0', 'content': '原始工具证据'},
                {'role': 'assistant', 'content': '此前有据回答'},
                {'role': 'user', 'content': '同一专业，操作系统的指标？'}]
    original = deepcopy(messages)
    sent = []

    async def send(client, request, *args, **kwargs):
        body = json.loads(request.content)
        sent.append(body)
        if any(m['role'] == 'system' for m in body['messages'][1:]):
            return httpx.Response(400, request=request, json={'error': {'message': 'System message must be at the beginning.'}})
        if stream:
            return httpx.Response(200, request=request, content='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, request=request, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]})

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    if stream:
        async def collect():
            return [e async for e in llm_client.stream_chat(messages)]
        assert asyncio.run(collect()) == [('delta', 'ok')]
    else:
        assert asyncio.run(llm_client.chat_with_tools(messages, [])) == {'type': 'text', 'content': 'ok'}
    assert len(sent) == 1 and messages == original
    assert sent[0]['messages'][0] == {'role': 'system', 'content': '\n\n'.join(m['content'] for m in original[:3])}
    assert sent[0]['messages'][1:] == original[3:]


@pytest.mark.parametrize('messages', [[], [{'role': 'user', 'content': 'hello'}],
    [{'role': 'system', 'content': 'one'}, {'role': 'user', 'content': 'question'}],
    [{'role': 'system', 'content': 'one'}, {'role': 'user', 'content': 'question'}, {'role': 'system', 'content': 'misplaced'}]])
def test_normalization_only_joins_the_leading_run(messages, api_config):
    api_config()
    before = deepcopy(messages)
    from llm.llm_api_client import build_request
    assert build_request(messages, task="planning")[2]["messages"] == before
    assert messages == before
