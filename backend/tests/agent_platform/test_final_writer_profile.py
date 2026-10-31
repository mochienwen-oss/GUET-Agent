"""Versioned final writer selection; planning and production defaults stay separate."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llm import llm_client
from agent.runtime import composition as C, graph, grounded_answer as G

PROFILE = 'writer-focus-20261024-1'


@pytest.fixture
def writer(monkeypatch, api_config):
    api_config('responses', writer=PROFILE, model='deepseek-flash', base_url='https://api.deepseek.com', profile='deepseek')
    data = json.loads((Path(__file__).parent / 'fixtures/api_body_enumeration_repeat.json').read_text(encoding='utf-8'))
    packet = data['packet']
    monkeypatch.setattr('documents.catalog.document_availability.snapshot', lambda: {'revision': packet['source_revision'], 'status': 'ready', 'documents': {}})
    return packet, data['draft']


def test_writer_and_model_are_selected_from_api_config(api_config):
    from llm.final_answer_profile import selected_profile, resolve_protocol
    config = api_config(writer=PROFILE, model='another-api-model')
    assert selected_profile() == PROFILE
    assert resolve_protocol() == C.PROTOCOL
    assert config.active.model == 'another-api-model'


def test_selected_writer_messages_and_audit_do_not_mutate_packet(writer, monkeypatch):
    packet, draft = writer
    original = deepcopy(packet)
    planner_prompt = llm_client.SYSTEM_PROMPT
    sent, audit = [], {}

    async def send(client, request, *args, **kwargs):
        assert str(request.url) == 'https://api.deepseek.com/responses'
        body = json.loads(request.content)
        sent.append(body)
        system = body['input'][0]['content'].split('\n\n', 1)[0]
        from llm.final_answer_profile import WRITER_SYSTEM
        assert system == WRITER_SYSTEM
        assert body['input'][0]['content'] == system + '\n\n' + C.body_contract(packet)[0]
        packed = json.loads(body['input'][-1]['content'])
        assert not {'source_revision', 'scope_filtered', 'retrieval_notes'} & packed.keys()
        assert packed['evidence_units'] == llm_client._final_input(packet)['evidence_units']
        assert packed['calculations'] == packet['calculations']
        assert body['temperature'] == 0 and body['max_output_tokens'] == 2048
        assert body['reasoning'] == {'effort': 'none'} and body['model'] == 'deepseek-flash'
        return httpx.Response(200, request=request, json={'status': 'completed', 'model': 'provider-reported-revision',
            'id': 'test-response', 'output_text': json.dumps(draft), 'usage': {'input_tokens': 1, 'output_tokens': 2}})

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    result = asyncio.run(llm_client.answer_with_evidence([], packet, trace=audit))
    assert result == draft and len(sent) == 1 and packet == original
    assert llm_client.SYSTEM_PROMPT == planner_prompt
    assert audit['composition']['profile'] == PROFILE
    assert audit['composition']['provider'] == 'deepseek'
    assert audit['composition']['protocol'] == C.PROTOCOL
    assert audit['composition']['requested_model'] == 'deepseek-flash'
    assert audit['composition']['response_model'] == 'provider-reported-revision'
    assert audit['composition']['source_revision'] == packet['source_revision']
    assert audit['final_requests'][0]['model'] == 'deepseek-flash'
    assert audit['final_requests'][0]['payload_kind'] == 'internal_composition'
    wire = audit['final_wire_requests'][0]
    assert set(wire['request_fields']) == set(sent[0])
    assert wire['generation'] == {k: sent[0][k] for k in ('temperature', 'max_output_tokens', 'reasoning', 'model', 'stream')}
    assert not {'top_p', 'top_k', 'repetition_penalty', 'input', 'messages'} & wire['generation'].keys()
    assert 'input' not in wire and 'messages' not in wire


def test_profile_rejects_conflicting_protocol_before_network(writer, monkeypatch):
    packet, _ = writer
    async def forbidden(*args, **kwargs):
        raise AssertionError('Conflicting protocol must not contact model services')
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    with pytest.raises(ValueError, match='候选协议'):
        asyncio.run(llm_client.answer_with_evidence([], packet, protocol='grounded-draft/5.1'))


def test_normal_final_service_selects_body_without_explicit_protocol(writer, monkeypatch):
    packet, draft = writer
    monkeypatch.setattr(G, 'evidence_packet', lambda state: deepcopy(packet))
    sent = []

    async def send(client, request, *args, **kwargs):
        sent.append(json.loads(request.content))
        assert str(request.url) == 'https://api.deepseek.com/responses'
        return httpx.Response(200, request=request, json={'status': 'completed', 'model': 'deepseek-flash', 'output_text': json.dumps(draft)})

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    result = asyncio.run(graph._grounded_final({'question': packet['question']}, [], 0, None))
    assert len(sent) == 1 and result['answer']['status'] == 'answer'
    assert result['answer_audit']['composition_protocol'] == C.PROTOCOL
    assert result['answer']['audit']['composition']['profile'] == PROFILE
    assert not result['answer_audit'].get('validation_retry')


def test_chat_entry_uses_profile_with_real_graph_and_local_course_tool(writer, monkeypatch):
    from types import SimpleNamespace
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
    with sessions() as session:
        user = dbmod.User(username='writer-profile', password_hash='unused', name='测试')
        session.add(user)
        session.flush()
        user_id = user.id
        conv = dbmod.Conversation(user_id=user_id, title='成文接入测试')
        session.add(conv)
        session.commit()
        conversation_id = conv.id
    sent = []

    async def send(client, request, *args, **kwargs):
        assert str(request.url) == 'https://api.deepseek.com/responses', 'This fixed course query must not need a planner'
        body = json.loads(request.content)
        sent.append(body)
        packed = json.loads(body['input'][-1]['content'])
        records = [u for u in packed['evidence_units'] if u.get('record_id')]
        assert {u['record_id'] for u in records} == {'2.1', '3.1', '5.1'}
        draft = {'paragraphs': [{'text': '该课程支撑指标：' + '、'.join(u['record_id'] for u in records) + '。',
            'unit_ids': [u['unit_id'] for u in records], 'calculation_ids': []}] + [
            {'text': '指标' + u['record_id'] + '：' + u['record']['text'],
             'unit_ids': [u['unit_id']], 'calculation_ids': []} for u in records]}
        return httpx.Response(200, request=request, json={'status': 'completed', 'model': 'offline-response-revision',
            'output_text': json.dumps(draft)})

    async def no_title(*args, **kwargs):
        return ''

    def get_db():
        with sessions() as session:
            yield session

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    monkeypatch.setattr(chat, 'ConversationLease', lambda cid: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(chat, '_auto_title', no_title)
    monkeypatch.setattr(dbmod, 'SessionLocal', sessions)
    app = FastAPI()
    app.include_router(chat.router, prefix='/api')
    app.dependency_overrides[chat.get_db] = get_db
    app.dependency_overrides[chat.require_auth] = lambda: dbmod.User(id=user_id)
    try:
        with TestClient(app) as client:
            response = client.post('/api/chat', json={'message': '人工智能专业的计算机网络课程支撑哪些毕业要求分解指标？',
                'conversation_id': conversation_id})
            saved = client.get(f'/api/conversations/{conversation_id}/messages').json()
        assert response.status_code == 200 and len(sent) == 1
        payloads = [json.loads(line[6:]) for line in response.text.splitlines()
                    if line.startswith('data: ') and line != 'data: [DONE]']
        assert not any('error' in row for row in payloads)
        final = next(row for row in payloads if 'replace' in row)
        assert final['answer']['status'] == 'answer', final
        assert final['replace'] == saved[-1]['content'] == final['answer']['text']
        assert final['answer'] == saved[-1]['answer']
        assert final['answer']['audit']['composition']['profile'] == PROFILE
        assert final['answer']['audit']['composition']['response_model'] == 'offline-response-revision'
        with sessions() as session:
            message = session.query(dbmod.Message).filter_by(role='assistant').one()
            assert message.answer_json == final['answer'] and message.retrieved_docs
    finally:
        engine.dispose()
