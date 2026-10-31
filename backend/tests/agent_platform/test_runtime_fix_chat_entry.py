"""Real chat router, compiled graph, SQLite, course tools and calculator.

Only generation and search are substituted; search content is a recorded source.
"""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from database import db
from conversations import conversation_context_store as store
from agent.core import Source, ToolResult
from agent.runtime import graph, registry
from core.config import settings


@pytest.fixture
def chat_app(monkeypatch, request):
    router = importlib.import_module('routers.chat')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    for table in (db.User.__table__, db.Conversation.__table__, db.Message.__table__):
        table.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    with sessions() as session:
        user = db.User(username='offline-user', password_hash='not-used', name='offline')
        session.add(user)
        session.commit()
        user_id = user.id
    app = FastAPI()
    app.include_router(router.router, prefix='/api')
    def get_session():
        with sessions() as session:
            yield session
    def get_user():
        with sessions() as session:
            return session.get(db.User, user_id)
    app.dependency_overrides[router.get_db] = get_session
    app.dependency_overrides[router.require_auth] = get_user
    async def no_title(*args, **kwargs):
        return ''
    monkeypatch.setattr(router, '_auto_title', no_title)
    class SQLiteLease:
        # MySQL GET_LOCK is exercised only in the isolated server acceptance.
        def __init__(self, conversation_id):
            self.conversation_id = conversation_id
        def close(self):
            pass
    monkeypatch.setattr(router, 'ConversationLease', SQLiteLease)
    monkeypatch.setattr(settings, 'llm_api_config_path', str(ROOT / 'deploy/llm-api.json'))
    recorded = json.loads((Path(__file__).parent / 'fixtures/api_os_indicator_decimal.json').read_text(encoding='utf8'))
    monkeypatch.setattr('documents.catalog.document_availability.snapshot', lambda: {
        'revision': recorded['packet']['source_revision'], 'status': 'ready', 'documents': {}})
    row = json.loads((Path(__file__).parent / 'fixtures/api_conversion_policy.json').read_text(encoding='utf8'))['row']
    source = Source(title=row['source'], text=row['text'], heading_path=row['heading_path'])
    original = registry.call
    calls, drafts = [], []
    app.state.local_evidence = []
    app.state.sessions = sessions
    def call(name, args=None, **kwargs):
        calls.append((name, args))
        if name == 'search_regulations':
            return ToolResult(tool=name, ok=True, status='success', sources=[source], meta={'args': args})
        return original(name, args, **kwargs)
    monkeypatch.setattr(registry, 'call', call)
    async def decide(messages, **kwargs):
        question = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
        if '操作系统' in question:
            return {'type': 'tool_calls', 'calls': [{'name': 'query_course', 'arguments': {'major': '人工智能', 'course': '操作系统'}}]}
        if '研究生' in question:
            return {'type': 'text', 'content': '本科培养方案不能证明你的研究生毕业学分要求。'}
        return {'type': 'tool_calls', 'calls': [{'name': 'search_regulations', 'arguments': {'query': '大学英语成绩认定'}}]}
    async def stream(messages, *args, **kwargs):
        yield ('delta', '准备回答')
    async def compose(messages, packet, **kwargs):
        drafts.append(packet)
        if packet['calculations']:
            record = packet['calculations'][0]
            return {'paragraphs': [{'text': '按学校换算规则，认定成绩为%s分。' % record['final_result'],
                'unit_ids': [u['unit_id'] for u in packet['evidence_units']],
                'calculation_ids': [record['calculation_id']]}]}
        records = [u for u in packet['evidence_units'] if u.get('record_id')]
        units = [u['unit_id'] for u in packet['evidence_units']]
        text = '；'.join('%s %s' % (u['record_id'], u['record']['text']) for u in records)
        if '操作系统' in packet['question']:
            text = '人工智能专业操作系统课程为2.5学分。支撑指标：' + text
        return {'paragraphs': [{'text': text, 'unit_ids': units, 'calculation_ids': []}]}
    app.state.native_decide = graph.model.decide
    monkeypatch.setattr(graph.model, 'decide', decide)
    monkeypatch.setattr(graph.model, 'answer', stream)
    monkeypatch.setattr(graph.model, 'compose', compose)
    yield app, sessions, calls, drafts
    evidence_dir = os.environ.get('GUET_LOCAL_CHAT_EVIDENCE')
    if evidence_dir:
        destination = Path(evidence_dir) / (request.node.name + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {'generation': 'test_double', 'retrieval': 'recorded_public_source',
                   'database': 'SQLite', 'mysql_lease': 'test_double',
                   'business_tools': 'actual_registry', 'turns': app.state.local_evidence,
                   'tool_calls': calls, 'calculations': [r for p in drafts for r in p['calculations']]}
        with destination.open('x', encoding='utf8') as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
    engine.dispose()


async def ask(client, question, conversation_id=None):
    if conversation_id is None:
        created = await client.post('/api/conversations', json={})
        assert created.status_code == 200, created.text
        conversation_id = created.json()['id']
    response = await client.post('/api/chat', json={'message': question, 'conversation_id': conversation_id})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith('data: ') and line[6:] != '[DONE]']
    assert not any('error' in event for event in events), events
    text = next(event['replace'] for event in reversed(events) if 'replace' in event)
    conversation_id = next((event['conversation_id'] for event in events if 'conversation_id' in event), conversation_id)
    assert conversation_id is not None
    messages = (await client.get('/api/conversations/%s/messages' % conversation_id)).json()
    answer = messages[-1]['answer']
    assert messages[-1]['content'] == answer['text'] == text
    app = client._transport.app
    with app.state.sessions() as session:
        context = store.load_context(session, conversation_id).model_dump(mode='json')
    app.state.local_evidence.append({'question': question, 'answer': answer,
        'stream_storage_equal': True, 'conversation_context': context})
    return conversation_id, answer


def test_new17_context_survives_chat_storage_reload_and_actual_course_query(chat_app):
    app, sessions, calls, drafts = chat_app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://offline') as client:
            cid, first = await ask(client, '人工智能专业的计算机网络课程支撑哪些毕业要求分解指标？')
            assert first['status'] == 'answer'
            with sessions() as session:
                prior = store.load_context(session, cid).prior_context
                assert any(e.canonical_name == '人工智能' and e.active for e in prior.entities)
            _, second = await ask(client, '同一个专业，操作系统课程支撑哪些指标？', cid)
            assert second['status'] == 'answer'
            assert '2.2' in second['text'] and '5.2' in second['text'] and '2.5学分' in second['text']
            assert calls[-1] == ('query_course', {'major': '人工智能', 'course': '操作系统'})
            assert second['audit']['routing']['layer'] == 'agent'
    asyncio.run(scenario())


def test_new19_persist_ask_complete_compute_and_clear(chat_app):
    app, sessions, calls, drafts = chat_app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://offline') as client:
            cid, first = await ask(client, '帮我把英语考试成绩换成大学英语认定分数。')
            assert first['status'] == 'needs_input' and calls == [] and drafts == []
            with sessions() as session:
                pending = store.load_context(session, cid).pending_task
                assert set(pending.missing_fields) == {'exam', 'score'}
            _, second = await ask(client, '四级', cid)
            assert second['status'] == 'needs_input'
            assert [g['field'] for g in second['gaps']] == ['score']
            with sessions() as session:
                pending = store.load_context(session, cid).pending_task
                assert [(i.name, i.value, i.source_turn) for i in pending.inputs] == [('exam', 'CET4', 2)]
            _, third = await ask(client, '450分', cid)
            assert third['status'] == 'answer' and '79分' in third['text']
            assert [name for name, _ in calls] == ['search_regulations', 'calculate']
            assert drafts[-1]['calculations'][0]['expression'] == '450×0.1764'
            with sessions() as session:
                assert store.load_context(session, cid).pending_task is None
    asyncio.run(scenario())


def test_new20_no_repeated_identity_question_or_undergraduate_personal_result(chat_app):
    app, sessions, calls, drafts = chat_app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://offline') as client:
            _, answer = await ask(client, '我是研究生，能按2025级人工智能本科培养方案判断我毕业需要多少学分吗？')
            assert answer['status'] == 'missing_policy' and answer['questions'] == []
            assert '148' not in answer['text'] and '研究生' in answer['text']
            assert all(name == 'search_regulations' for name, _ in calls)
            assert drafts == []
    asyncio.run(scenario())


def test_conversion_pending_cancel_clears_saved_state_without_generation(chat_app):
    app, sessions, calls, drafts = chat_app
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://offline') as client:
            cid, first = await ask(client, '帮我把英语考试成绩换成大学英语认定分数。')
            _, answer = await ask(client, '取消', cid)
            assert '取消' in answer['text'] and calls == [] and drafts == []
            with sessions() as session:
                assert store.load_context(session, cid).pending_task is None
    asyncio.run(scenario())


def test_model_interpreted_free_wording_roundtrips_through_chat_pending_and_calculator(chat_app, monkeypatch):
    app, sessions, calls, drafts = chat_app
    monkeypatch.setattr(graph.model, 'decide', app.state.native_decide)
    async def planning(messages, tools):
        assert tools[-1]['function']['name'] == 'request_input'
        question = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
        if '算一算' in question:
            return {'type': 'tool_calls', 'calls': [{'name': 'request_input', 'arguments': {
                'questions': ['你参加的是四级还是六级？', '具体考试成绩是多少？'], 'operation': 'score_conversion'}}]}
        return {'type': 'tool_calls', 'calls': [{'name': 'search_regulations', 'arguments': {'query': '大学英语成绩认定'}}]}
    monkeypatch.setattr(graph.llm_client, 'chat_with_tools', planning)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://offline') as client:
            cid, first = await ask(client, '把英语考试结果折成大学英语认定分，帮我算一算。')
            assert first['status'] == 'needs_input' and calls == []
            assert first['audit']['application_decision'] == 'model_clarification'
            with sessions() as session:
                assert store.load_context(session, cid).pending_task.task_type == 'score_conversion'
            _, second = await ask(client, '四级', cid)
            assert second['status'] == 'needs_input' and [g['field'] for g in second['gaps']] == ['score']
            _, third = await ask(client, '450分', cid)
            assert third['status'] == 'answer' and '79分' in third['text']
            assert [name for name, _ in calls] == ['search_regulations', 'calculate']
            with sessions() as session:
                assert store.load_context(session, cid).pending_task is None
    asyncio.run(scenario())
