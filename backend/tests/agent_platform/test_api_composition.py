"""Composition and planning use the same configured API, never a local model."""
import asyncio
import json

import httpx

from llm import llm_client, llm_api_client


def test_planning_and_final_composition_use_one_model(api_config, monkeypatch):
    api_config('responses', writer='writer-focus-20261024-1', model='same-model')
    sent = []
    async def send(client, request, **kwargs):
        body = json.loads(request.content)
        sent.append(body)
        text = json.dumps({'paragraphs': []}) if 'text' in body else '直接回答'
        return httpx.Response(200, request=request, json={
            'status': 'completed', 'model': 'same-model', 'output_text': text})
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    assert asyncio.run(llm_client.chat_with_tools([], []))['content'] == '直接回答'
    assert asyncio.run(llm_client.answer_with_evidence([], {'evidence': []})) == {'paragraphs': []}
    assert len(sent) == 2 and {row['model'] for row in sent} == {'same-model'}
    assert all(not {'top_k', 'repetition_penalty', 'chat_template_kwargs'} & row.keys() for row in sent)


def test_strict_optional_schema_round_trip_stays_transport_only(api_config, monkeypatch):
    api_config('responses')
    schema = {'type': 'object', 'properties': {'slot': {'type': 'string'}, 'reason': {'type': 'string'}},
              'required': ['slot'], 'additionalProperties': False}
    original = json.dumps(schema)
    async def send(client, request, **kwargs):
        wire = json.loads(request.content)['text']['format']['schema']
        assert wire['required'] == ['slot', 'reason']
        assert wire['properties']['reason'] == {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
        return httpx.Response(200, request=request, json={
            'status': 'completed', 'output_text': '{"slot":"a","reason":null}'})
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    result = asyncio.run(llm_api_client.complete([], task='composition', schema=schema))
    restored = llm_api_client.restore_optional_fields(json.loads(result['text']), schema)
    assert restored == {'slot': 'a'} and json.dumps(schema) == original
