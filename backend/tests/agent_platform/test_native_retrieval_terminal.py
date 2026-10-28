"""Prepared offline regressions; real model calls are replaced explicitly."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.runtime import graph, loop
from agent.runtime.retrieval_reply import RETRIEVAL_REPLIES
from llm import llm_api_config


def proposal():
    calls = [{'id': 'first', 'name': 'search_regulations', 'arguments': {'query': '学校规定'}},
             {'id': 'second', 'name': 'search_regulations', 'arguments': {'query': '其他规定'}}]
    return {'text': '', 'calls': calls, 'usage': {}, 'message': {
        'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': row['id'], 'type': 'function', 'function': {
                'name': row['name'], 'arguments': json.dumps(row['arguments'])}} for row in calls]}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    connect = socket.socket.connect

    def forbidden(*args, **kwargs):
        address = args[1] if len(args) > 1 else None
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return connect(*args, **kwargs)
        raise AssertionError('Offline regression must not use the network')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(llm_api_config, 'get_api_config', lambda: SimpleNamespace(
        active_profile='offline', active=SimpleNamespace(model='explicit-test-model')))


@pytest.mark.parametrize('status', ['no_evidence', 'irrelevant_result', 'error'])
def test_unsuccessful_retrieval_ends_before_another_model_or_tool_call(monkeypatch, status):
    model_calls, tool_calls, histories = [], [], []

    def model(messages, **kwargs):
        model_calls.append(kwargs)
        histories.append(messages)
        if len(model_calls) > 1:
            raise AssertionError('The model must not compose after a terminal retrieval result')
        return proposal()

    def tool(name, arguments):
        tool_calls.append(arguments)
        return {'status': status, 'sources': [], 'data': {}}

    monkeypatch.setattr(loop, 'complete_sync', model)
    monkeypatch.setattr(graph.registry, 'call', tool)
    result = graph._execute({'question': '学校具体规定是什么？', 'history': []})
    assert result['final_answer'] == RETRIEVAL_REPLIES[status]
    assert result['terminal'] is True and result['contexts'] == []
    assert len(model_calls) == len(tool_calls) == 1
    observations = [row for row in histories[0] if row['role'] == 'tool']
    assert [row['tool_call_id'] for row in observations] == ['first', 'second']
    assert json.loads(observations[1]['content'])['error']['code'] == 'turn_ended'


def test_direct_chat_does_not_force_retrieval(monkeypatch):
    def forbidden_tool(*args, **kwargs):
        raise AssertionError('Direct chat must not synthesize a retrieval call')

    monkeypatch.setattr(graph.registry, 'call', forbidden_tool)
    monkeypatch.setattr(loop, 'complete_sync', lambda *a, **k: {
        'text': '你好，我是 SAI。', 'calls': [], 'usage': {},
        'message': {'role': 'assistant', 'content': '你好，我是 SAI。'}})
    result = graph._execute({'question': '你好', 'history': []})
    assert result['final_answer'] == '你好，我是 SAI。'
    assert result['calls'] == []


def test_retrieval_timeout_is_not_rewritten_as_no_evidence(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError('offline simulated retrieval timeout')

    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        assert len(calls) == 1
        return proposal()

    monkeypatch.setattr(loop, 'complete_sync', model)
    monkeypatch.setattr(graph.registry, 'call', timeout)
    result = graph._execute({'question': '学校具体流程是什么？', 'history': []})
    assert result['terminal'] is True
    assert result['final_answer'] == RETRIEVAL_REPLIES['error']


@pytest.mark.parametrize('status', ['no_evidence', 'irrelevant_result', 'error'])
def test_terminal_reply_is_stored_without_a_title_model_call(monkeypatch, status):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from database import db as dbmod
    from routers import chat

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    for model in (dbmod.User, dbmod.Conversation, dbmod.Message):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    question = '关于学校学籍管理相关规定的具体办理要求有哪些？'
    with sessions() as session:
        user = dbmod.User(username='offline-terminal', password_hash='unused', name='离线用户')
        session.add(user)
        session.flush()
        user_id = user.id
        conversation = dbmod.Conversation(user_id=user_id, title=chat.titles.placeholder_title(question))
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    text, title_calls = RETRIEVAL_REPLIES[status], []
    async def terminal(*args, **kwargs):
        yield 'delta', text
        yield 'done', {'terminal': True, 'contexts': [], 'calls': [], 'n_refs': 0,
                       'answer': {'status': 'answer', 'text': text, 'contexts': []}}

    async def title(*args, **kwargs):
        title_calls.append(1)
        raise AssertionError('A terminal retrieval reply must not request a title')

    def get_db():
        with sessions() as session:
            yield session

    monkeypatch.setattr(chat.agent_graph, 'run_stream', terminal)
    monkeypatch.setattr(chat.llm_client, 'generate_title', title)
    monkeypatch.setattr(chat.titles, 'needs_model', lambda question: True)
    monkeypatch.setattr(chat, 'ConversationLease', lambda *a: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dbmod, 'SessionLocal', sessions)
    app = FastAPI()
    app.include_router(chat.router, prefix='/api')
    app.dependency_overrides[chat.get_db] = get_db
    app.dependency_overrides[chat.require_auth] = lambda: dbmod.User(id=user_id)
    try:
        with TestClient(app) as client:
            reply = client.post('/api/chat', json={'conversation_id': conversation_id, 'message': question})
        assert reply.status_code == 200 and '[DONE]' in reply.text
        assert json.loads(next(line[6:] for line in reply.text.splitlines() if line.startswith('data: {')))['content'] == text
        with sessions() as session:
            saved = session.query(dbmod.Message).filter_by(conversation_id=conversation_id, role='assistant').one()
            assert saved.content == text and saved.retrieved_docs == []
        assert title_calls == []
    finally:
        engine.dispose()
