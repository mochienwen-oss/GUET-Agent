"""Offline wire contracts and caller integration; never a model-quality score."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from core.config import Settings, settings
from llm.llm_api_config import _load, get_api_config
from llm import llm_api_client as api
from llm import llm_client

TOOLS = [{'type': 'function', 'function': {'name': 'query_course', 'description': '课程',
    'parameters': {'type': 'object', 'properties': {'major': {'type': 'string'}}, 'required': ['major']}}}]


@pytest.fixture
def configure(tmp_path, monkeypatch):
    path = tmp_path / 'api.json'
    monkeypatch.setattr(settings, 'llm_api_config_path', str(path))
    monkeypatch.setenv('TEST_LLM_KEY', 'offline-key-do-not-log')

    def write(protocol='responses', **overrides):
        data = json.loads((ROOT / 'deploy/llm-api.json').read_text(encoding='utf-8'))
        data['active_profile'] = 'platform'
        data['profiles'] = {'platform': {'protocol': protocol, 'base_url': 'https://provider.invalid/v1',
            'model': 'model-a', 'api_key_env': 'TEST_LLM_KEY', 'parameters': {'temperature': 0}, **overrides}}
        path.write_text(json.dumps(data), encoding='utf-8')
        _load.cache_clear()
        return data, path
    write()
    yield write
    _load.cache_clear()


def response(request, protocol, text='ok', calls=False, **overrides):
    if protocol == 'responses':
        data = {'status': 'completed', 'model': 'actual-revision', 'output_text': text, 'output':
            [{'type': 'function_call', 'call_id': 'provider-call', 'name': 'query_course', 'arguments': '{"major":"人工智能"}'}] if calls else [],
            'usage': {'input_tokens': 1, 'output_tokens': 2}}
    else:
        message = {'content': text}
        if calls:
            message['tool_calls'] = [{'id': 'provider-call', 'type': 'function',
                'function': {'name': 'query_course', 'arguments': '{"major":"人工智能"}'}}]
        data = {'model': 'actual-revision', 'choices': [{'finish_reason': 'tool_calls' if calls else 'stop', 'message': message}]}
    data.update(overrides)
    return httpx.Response(200, request=request, json=data)


def test_default_configuration_and_empty_path_is_rejected(monkeypatch):
    monkeypatch.delenv('LLM_API_CONFIG_PATH', raising=False)
    cfg = Settings(_env_file=None)
    assert Path(cfg.llm_api_config_path) == ROOT / 'deploy/llm-api.json'
    monkeypatch.setattr(settings, 'llm_api_config_path', '')
    with pytest.raises(ValueError, match='不能为空'):
        get_api_config()


@pytest.mark.parametrize('change', ['inline_key', 'reserved_parameter', 'secret_url', 'unknown_profile', 'thinking'])
def test_invalid_config_never_falls_back_or_echoes_secret(configure, change):
    data, path = configure()
    profile = data['profiles']['platform']
    if change == 'inline_key': profile['api_key'] = 'private-value'
    elif change == 'reserved_parameter': profile['parameters']['model'] = 'override'
    elif change == 'secret_url': profile['base_url'] = 'https://secret:private-value@provider.invalid'
    elif change == 'unknown_profile': data['active_profile'] = 'missing'
    else: profile['parameters']['thinking'] = {'type': 'enabled'}
    path.write_text(json.dumps(data), encoding='utf-8')
    _load.cache_clear()
    with pytest.raises(ValueError) as error:
        get_api_config()
    assert 'private-value' not in str(error.value)


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_tool_wire_roundtrip_is_lossless_and_platform_parameters_are_explicit(configure, monkeypatch, protocol):
    configure(protocol)
    messages = [{'role': 'system', 'content': '主指令'}, {'role': 'system', 'content': '可信上下文'},
        {'role': 'user', 'content': '课程'}, {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'call_0', 'type': 'function', 'function': {'name': 'query_course', 'arguments': '{"major":"人工智能"}'}}]},
        {'role': 'tool', 'tool_call_id': 'call_0', 'content': '真实工具结果'}]
    before = deepcopy(messages)
    sent, trace = [], {}

    async def send(client, request, **kwargs):
        body = json.loads(request.content)
        sent.append(body)
        assert str(request.url) == 'https://provider.invalid/v1/' + ('responses' if protocol == 'responses' else 'chat/completions')
        assert request.headers['Authorization'] == 'Bearer offline-key-do-not-log'
        assert not {'top_k', 'repetition_penalty', 'chat_template_kwargs'} & body.keys()
        if protocol == 'responses':
            assert body['tools'][0]['name'] == 'query_course'
            assert body['input'][-2]['call_id'] == body['input'][-1]['call_id'] == 'call_0'
            assert body['input'][-1]['output'] == '真实工具结果'
        else:
            assert body['messages'][0]['content'] == '主指令\n\n可信上下文'
            assert body['messages'][-2:] == before[-2:]
        return response(request, protocol, text='', calls=True)
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    result = asyncio.run(api.complete(messages, task='planning', tools=TOOLS, trace=trace))
    assert result['calls'] == [{'name': 'query_course', 'arguments': {'major': '人工智能'}}]
    assert messages == before and len(sent) == 1
    assert 'offline-key-do-not-log' not in json.dumps(trace)


def test_platform_switch_changes_url_model_key_and_output_field(configure, monkeypatch):
    configure('chat_completions', base_url='https://second.invalid/api', model='model-b',
              api_key_env='SECOND_KEY', chat_max_tokens_field='max_completion_tokens')
    monkeypatch.setenv('SECOND_KEY', 'second-offline-key')
    async def send(client, request, **kwargs):
        body = json.loads(request.content)
        assert str(request.url) == 'https://second.invalid/api/chat/completions'
        assert body['model'] == 'model-b' and request.headers['Authorization'] == 'Bearer second-offline-key'
        assert body['max_completion_tokens'] == 2048 and 'max_tokens' not in body
        return response(request, 'chat_completions')
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    assert asyncio.run(llm_client.chat_with_tools([{'role': 'user', 'content': 'hi'}], []))['content'] == 'ok'


def test_missing_key_and_http_errors_are_explicit_no_model_fallback(configure, monkeypatch):
    configure()
    monkeypatch.delenv('TEST_LLM_KEY')
    sent = []
    async def send(client, request, **kwargs):
        sent.append(request)
        return httpx.Response(401, request=request, json={'error': {'message': 'secret-key-and-user-prompt'}})
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    with pytest.raises(ValueError, match='TEST_LLM_KEY'):
        asyncio.run(api.complete([], task='planning'))
    assert not sent
    monkeypatch.setenv('TEST_LLM_KEY', 'offline')
    with pytest.raises(RuntimeError) as error:
        asyncio.run(api.complete([], task='planning'))
    assert len(sent) == 1 and '401' in str(error.value) and 'secret-key' not in str(error.value)


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_incomplete_output_is_not_success(configure, monkeypatch, protocol):
    configure(protocol)
    async def send(client, request, **kwargs):
        if protocol == 'responses': return response(request, protocol, status='incomplete')
        return response(request, protocol, choices=[{'finish_reason': 'length', 'message': {'content': 'partial'}}])
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    with pytest.raises(ValueError): asyncio.run(api.complete([], task='composition'))


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
@pytest.mark.parametrize('terminal', [True, False])
def test_stream_assembles_calls_only_after_completion(configure, monkeypatch, protocol, terminal):
    configure(protocol)
    if protocol == 'responses':
        events = [{'type': 'response.output_text.delta', 'delta': '内容'},
            {'type': 'response.function_call_arguments.delta', 'delta': '{"major":'}]
        if terminal: events.append({'type': 'response.completed', 'response': {'status': 'completed', 'output': [
            {'type': 'function_call', 'name': 'query_course', 'call_id': 'r1', 'arguments': '{"major":"人工智能"}'}]}})
    else:
        events = [{'choices': [{'delta': {'content': '内容'}}]},
                  {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'query_', 'arguments': '{"major":'}}]}}]}]
        if terminal: events.append({'choices': [{'finish_reason': 'tool_calls', 'delta': {'tool_calls': [
            {'index': 0, 'function': {'name': 'course', 'arguments': '"人工智能"}'}}]}}]})
    body = ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events)
    async def send(client, request, **kwargs): return httpx.Response(200, request=request, content=body)
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    async def collect(): return [e async for e in llm_client.stream_chat([], TOOLS)]
    if terminal:
        # The graph stops on prose; a legal preamble must not hide a tool call.
        assert asyncio.run(collect()) == [('calls', [{'name': 'query_course', 'arguments': {'major': '人工智能'}}])]
    else:
        with pytest.raises(ValueError, match='终止事件'): asyncio.run(collect())


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_writer_keeps_protocol_evidence_and_schema_across_models(configure, monkeypatch, protocol):
    configure(protocol, model='a-different-model')
    fixture = json.loads((Path(__file__).parent / 'fixtures/api_body_enumeration_repeat.json').read_text(encoding='utf-8'))
    packet, draft = fixture['packet'], fixture['draft']
    before, trace = deepcopy(packet), {}
    async def send(client, request, **kwargs):
        body = json.loads(request.content)
        messages = body.get('input', body.get('messages'))
        packed = json.loads(messages[-1]['content'])
        assert 'source_revision' not in packed
        assert packed['calculations'] == packet['calculations']
        assert packed['evidence_units'] == llm_client._final_input(packet)['evidence_units']
        assert body['model'] == 'a-different-model'
        if protocol == 'responses': assert body['text']['format']['type'] == 'json_schema'
        else: assert body['response_format']['type'] == 'json_schema'
        return response(request, protocol, text=json.dumps(draft))
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    assert asyncio.run(llm_client.answer_with_evidence([], packet, trace=trace)) == draft
    assert packet == before and trace['composition']['requested_model'] == 'a-different-model'
    assert trace['composition']['response_model'] == 'actual-revision'
    assert trace['composition']['protocol'] == 'grounded-body/1'


def test_explicit_json_object_compatibility_keeps_local_validation(configure):
    configure('chat_completions', structured_output='json_object')
    _, _, body = api.build_request([], task='composition', schema={'type': 'object'})
    assert body['response_format'] == {'type': 'json_object'}


def test_title_grading_and_ppt_share_the_same_config(configure, monkeypatch):
    configure()
    requests = []
    def send(client, request, **kwargs):
        requests.append(json.loads(request.content))
        content = '{"research_tasks":[]}' if requests[-1].get('text', {}).get('format', {}).get('type') == 'json_object' else '是'
        return response(request, 'responses', text=content)
    async def send_async(client, request, **kwargs):
        requests.append(json.loads(request.content))
        return response(request, 'responses', text='校园制度查询')
    monkeypatch.setattr(httpx.Client, 'send', send)
    monkeypatch.setattr(httpx.AsyncClient, 'send', send_async)
    from agent.tools.create_ppt.scripts.content.generate import _json_reply
    assert llm_client.grade_relevance('问题', '依据')
    assert _json_reply('只输出JSON', {}) == {'research_tasks': []}
    assert asyncio.run(llm_client.generate_title('校园政策')) == '校园制度查询'
    assert [r['max_output_tokens'] for r in requests] == [8, get_api_config().tasks['ppt'].max_output_tokens, 40]
    assert {r['model'] for r in requests} == {'model-a'}


def test_invalid_tool_arguments_never_become_an_executable_call(configure, monkeypatch):
    configure()
    async def send(client, request, **kwargs):
        return response(request, 'responses', output=[{'type': 'function_call', 'name': 'query_course',
            'call_id': 'invalid', 'arguments': '{not json'}])
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    with pytest.raises(ValueError, match='完整JSON对象'):
        asyncio.run(llm_client.chat_with_tools([], TOOLS))


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_real_chat_graph_plans_executes_composes_and_persists_using_only_api(configure, monkeypatch, protocol):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from database import db as dbmod
    from routers import chat

    configure(protocol)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine)
    for model in (dbmod.User, dbmod.Conversation, dbmod.Message):
        model.__table__.create(engine)
    with sessions() as session:
        user = dbmod.User(username='api-local-test', password_hash='unused', name='test')
        session.add(user)
        session.flush()
        conv = dbmod.Conversation(user_id=user.id, title='local')
        session.add(conv)
        session.commit()
        uid, cid = user.id, conv.id
    sent = []

    async def send(client, request, **kwargs):
        assert str(request.url) == 'https://provider.invalid/v1/' + ('responses' if protocol == 'responses' else 'chat/completions')
        body = json.loads(request.content)
        sent.append(body)
        if body.get('stream'):
            terminal = ({'type': 'response.completed', 'response': {'status': 'completed', 'output_text': '已有依据'}}
                        if protocol == 'responses' else {'choices': [{'finish_reason': 'stop', 'delta': {'content': '已有依据'}}]})
            return httpx.Response(200, request=request, content='data: ' + json.dumps(terminal) + '\n\n')
        if body.get('tools'):
            function = {'name': 'query_course', 'arguments': '{"major":"人工智能","course":"计算机网络"}'}
            if protocol == 'responses':
                return response(request, protocol, text='', output=[{'type': 'function_call', 'call_id': 'p1', **function}])
            return response(request, protocol, choices=[{'finish_reason': 'tool_calls', 'message': {'content': '',
                'tool_calls': [{'id': 'p1', 'type': 'function', 'function': function}]}}])
        packet = json.loads(body.get('input', body.get('messages'))[-1]['content'])
        units = [u for u in packet['evidence_units'] if u.get('record_id')]
        assert {u['record_id'] for u in units} == {'2.1', '3.1', '5.1'}
        draft = {'paragraphs': [{'text': '支撑指标' + u['record_id'] + '：' + u['record']['text'],
            'unit_ids': [u['unit_id']], 'calculation_ids': []} for u in units]}
        return response(request, protocol, text=json.dumps(draft))

    async def no_title(*args, **kwargs): return ''
    def get_db():
        with sessions() as session: yield session
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    monkeypatch.setattr(chat, '_auto_title', no_title)
    monkeypatch.setattr(chat, 'ConversationLease', lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dbmod, 'SessionLocal', sessions)
    app = FastAPI()
    app.include_router(chat.router, prefix='/api')
    app.dependency_overrides[chat.get_db] = get_db
    app.dependency_overrides[chat.require_auth] = lambda: dbmod.User(id=uid)
    try:
        with TestClient(app) as client:
            answer = client.post('/api/chat', json={'message': '请帮我查人工智能专业的计算机网络支撑哪些毕业要求分解指标', 'conversation_id': cid})
            saved = client.get(f'/api/conversations/{cid}/messages').json()
        events = [json.loads(line[6:]) for line in answer.text.splitlines() if line.startswith('data: ') and line != 'data: [DONE]']
        assert not any('error' in event for event in events), events
        final = next(e for e in events if 'replace' in e)
        assert final['answer']['status'] == 'answer', final
        assert final['replace'] == saved[-1]['content'] and final['answer'] == saved[-1]['answer']
        assert any(r.get('tools') for r in sent) and any(r.get('stream') for r in sent)
        assert any(r.get('text') or r.get('response_format') for r in sent) and {r['model'] for r in sent} == {'model-a'}
        assert final['answer']['audit']['composition']['provider'] == 'platform'
    finally:
        engine.dispose()


@pytest.mark.parametrize('points', [1, 2])
def test_api_startup_skips_local_generation_but_keeps_knowledge_identity(configure, monkeypatch, tmp_path, points):
    from types import SimpleNamespace
    from database import db
    from documents.catalog import document_availability
    import qdrant_client
    from application import runtime_identity
    from agent.tools.search_regulations.scripts.index_ops import bm25
    configure()
    profile = tmp_path / 'runtime-profile.json'
    profile.write_text(json.dumps({'collection': 'api-test', 'api_config': 'test-api.json'}), encoding='utf-8')
    active, vocab, prompt = [tmp_path / name for name in ['active.json', 'vocab.json', 'prompt.txt']]
    active.write_text(json.dumps({'documents': [{'document_id': 1, 'title': 'test', 'chunks': 1}]}), encoding='utf-8')
    vocab.write_text('{}', encoding='utf-8')
    prompt.write_text('prompt', encoding='utf-8')
    monkeypatch.setenv('SAI_RUNTIME_PROFILE', str(profile))
    for name, value in {'qdrant_collection': 'api-test', 'bm25_vocab_path': str(vocab),
        'active_documents_path': str(active), 'system_prompt_path': str(prompt)}.items():
        monkeypatch.setattr(settings, name, value)
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query(self, *args): return SimpleNamespace(all=lambda: [SimpleNamespace(id=1, title='test', chunk_count=1)])
    monkeypatch.setattr(db, 'SessionLocal', Session)
    monkeypatch.setattr(document_availability, 'snapshot', lambda: {'status': 'ready'})
    monkeypatch.setattr(bm25, 'load_vocab', lambda: {'terms': ['test']})
    monkeypatch.setattr(qdrant_client, 'QdrantClient', lambda **kwargs: SimpleNamespace(get_collection=lambda _: SimpleNamespace(points_count=points)))
    def no_probe(*args, **kwargs): raise AssertionError('API startup must not probe a local or paid model')
    monkeypatch.setattr(httpx.Client, 'post', no_probe)
    monkeypatch.setattr(httpx.AsyncClient, 'post', no_probe)
    if points == 1: runtime_identity.verify_runtime()
    else:
        with pytest.raises(RuntimeError, match='向量点数'): runtime_identity.verify_runtime()


@pytest.mark.parametrize('protocol', ['responses', 'chat_completions'])
def test_api_chat_and_persistence_with_explicit_source_double(configure, monkeypatch, protocol):
    """The same HTTP/graph/wire path, with a declared source double, not real KB acceptance."""
    from agent.core import Source, ToolResult
    from agent.runtime import registry
    from documents.catalog import document_availability
    facts = {'major': '人工智能', 'college': '测试学院',
             'course': {'name': '计算机网络', 'credits': 3, 'hours': 48},
             'indicators': [{'id': key, 'text': '本地协议测试指标 ' + key}
                            for key in ('2.1', '3.1', '5.1')]}
    original = registry.call
    def call(name, args, **kwargs):
        if name != 'query_course':
            return original(name, args, **kwargs)
        assert args == {'major': '人工智能', 'course': '计算机网络'}
        return ToolResult(tool=name, ok=True, status='success', data=facts,
                          sources=[Source(title='人工智能专业培养方案（测试替身）',
                                          text=json.dumps(facts, ensure_ascii=False))],
                          meta={'args': args, 'source_revision': 0})
    monkeypatch.setattr(registry, 'call', call)
    monkeypatch.setattr(document_availability, 'snapshot', lambda: {
        'revision': 0, 'status': 'ready', 'documents': {}})
    test_real_chat_graph_plans_executes_composes_and_persists_using_only_api(configure, monkeypatch, protocol)
