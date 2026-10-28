"""Prepared streaming regressions: native frames and explicit replacement models."""
import asyncio
import json
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.runtime import graph, loop
from agent.runtime.retrieval_reply import RETRIEVAL_REPLIES
from llm import llm_api_client as transport
from llm import llm_api_config


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    connect = socket.socket.connect
    def forbidden(sock, address):
        # Windows asyncio may use a loopback socket pair for its event loop.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return connect(sock, address)
        raise AssertionError('Streaming regression must not access external networks')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(llm_api_config, 'get_api_config', lambda: SimpleNamespace(
        active_profile='offline', active=SimpleNamespace(model='explicit-test-model')))


def frame(value):
    return ('data: ' + (value if isinstance(value, str) else json.dumps(value)) + '\n\n').encode()


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_transport_delivers_text_before_response_completion(monkeypatch, protocol):
    seen = []
    config = SimpleNamespace(active_profile='offline', active=SimpleNamespace(
        protocol=protocol, api_key=lambda: 'offline-only', timeout_seconds=5, connect_timeout_seconds=1))
    def request(*args, **kwargs):
        assert kwargs['stream'] is True
        return config, 'https://offline.invalid/stream', {'stream': True}

    class Tokens(httpx.SyncByteStream):
        def __iter__(self):
            if protocol == 'responses':
                yield frame({'type': 'response.output_text.delta', 'delta': '你'})
                assert seen == ['你']
                yield frame({'type': 'response.output_text.delta', 'delta': '好'})
                assert seen == ['你', '好']
                yield frame({'type': 'response.completed', 'response': {'status': 'completed',
                    'output': [{'type': 'message', 'role': 'assistant', 'content': [
                        {'type': 'output_text', 'text': '你好'}]}]}})
            else:
                for token in ('你', '好'):
                    yield frame({'choices': [{'index': 0, 'delta': {'content': token}}]})
                assert seen == ['你', '好']
                yield frame({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
                yield frame('[DONE]')

    original = httpx.Client
    fake = httpx.MockTransport(lambda request: httpx.Response(200, stream=Tokens()))
    monkeypatch.setattr(transport, 'build_request', request)
    monkeypatch.setattr(transport.httpx, 'Client', lambda **kwargs: original(transport=fake, **kwargs))
    result = transport.complete_sync([], task='planning', on_delta=seen.append)
    assert result['text'] == '你好' and seen == ['你', '好']


def test_chat_stream_retains_call_id_arguments_and_reasoning():
    collector = transport._StreamResult('chat_completions')
    collector.feed(json.dumps({'choices': [{'delta': {'reasoning_content': '工具规划', 'tool_calls': [
        {'index': 0, 'id': 'native-call', 'function': {'name': 'search_regulations', 'arguments': '{"query":'}}]}}]}))
    collector.feed(json.dumps({'choices': [{'delta': {'tool_calls': [
        {'index': 0, 'function': {'arguments': '"学校规定"}'}}]}, 'finish_reason': 'tool_calls'}]}))
    result = collector.finish()
    assert result['calls'] == [{'id': 'native-call', 'name': 'search_regulations', 'arguments': {'query': '学校规定'}}]
    assert result['message']['reasoning_content'] == '工具规划'


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_incomplete_stream_does_not_become_a_final_answer(protocol):
    collector = transport._StreamResult(protocol)
    if protocol == 'responses':
        collector.feed(json.dumps({'type': 'response.output_text.delta', 'delta': '未完成'}))
    else:
        collector.feed(json.dumps({'choices': [{'delta': {'content': '未完成'}}]}))
        collector.feed('[DONE]')
    with pytest.raises(ValueError, match='终止事件'):
        collector.finish()


def test_graph_forwards_delta_while_model_is_still_running(monkeypatch):
    release, completed = Event(), Event()
    def model(messages, *, on_delta, **kwargs):
        on_delta('你好')
        assert release.wait(5)
        completed.set()
        return {'text': '你好', 'calls': [], 'usage': {},
                'message': {'role': 'assistant', 'content': '你好'}}
    monkeypatch.setattr(loop, 'complete_sync', model)

    async def consume():
        stream = graph.run_stream([], '你好')
        try:
            kind, text = await asyncio.wait_for(anext(stream), 3)
            assert (kind, text) == ('delta', '你好') and not completed.is_set()
            release.set()
            rest = [event async for event in stream]
            assert rest[-1][0] == 'done'
        finally:
            release.set()
            await stream.aclose()
    asyncio.run(consume())


def test_closing_chat_stream_cancels_the_model_worker(monkeypatch):
    from agent.core.request_control import check_cancelled
    release, stopped = Event(), Event()
    def model(messages, *, on_delta, **kwargs):
        on_delta('正在生成')
        assert release.wait(3)
        try:
            check_cancelled()
        except InterruptedError:
            stopped.set()
            raise
        raise AssertionError('The closed stream must cancel its worker')
    monkeypatch.setattr(loop, 'complete_sync', model)

    async def consume():
        stream = graph.run_stream([], '你好')
        try:
            assert await asyncio.wait_for(anext(stream), 3) == ('delta', '正在生成')
            await stream.aclose()
        finally:
            release.set()
        assert await asyncio.to_thread(stopped.wait, 3)
    asyncio.run(consume())


@pytest.mark.parametrize('status', ['no_evidence', 'irrelevant_result', 'error'])
def test_tool_preface_is_cleared_and_empty_evidence_ends_the_stream(monkeypatch, status):
    called = []
    def model(messages, *, on_delta, **kwargs):
        called.append(1)
        assert len(called) == 1
        on_delta('我先查询资料。')
        call = {'id': 'native-call', 'name': 'search_regulations', 'arguments': {'query': '学校规定'}}
        return {'text': '我先查询资料。', 'calls': [call], 'usage': {}, 'message': {
            'role': 'assistant', 'content': '我先查询资料。', 'tool_calls': [{
                'id': 'native-call', 'type': 'function', 'function': {
                    'name': call['name'], 'arguments': json.dumps(call['arguments'])}}]}}
    monkeypatch.setattr(loop, 'complete_sync', model)
    monkeypatch.setattr(graph.registry, 'call', lambda *a: {'status': status, 'sources': []})

    async def consume():
        text, events = '', []
        async for kind, payload in graph.run_stream([], '学校规定是什么？'):
            events.append((kind, payload))
            if kind == 'delta':
                text += payload
            elif kind == 'replace':
                text = payload
        assert text == RETRIEVAL_REPLIES[status]
        assert ('replace', '') in events and events[-1][1]['terminal'] is True
    asyncio.run(consume())
