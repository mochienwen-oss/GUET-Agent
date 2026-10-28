"""Recorded failures and compiled graph checks; no live model requests."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from conversations import conversation_context as CC
from entities import entity_prior
from agent.runtime import composition, graph


@pytest.fixture
def os_recorded(monkeypatch):
    data = json.loads((Path(__file__).parent / 'fixtures/api_os_indicator_decimal.json').read_text(encoding='utf8'))
    assert hashlib.sha256(json.dumps(data['packet'], ensure_ascii=False, sort_keys=True).encode()).hexdigest() == data['provenance']['packet_sha256']
    monkeypatch.setattr('documents.document_availability.snapshot', lambda: {
        'revision': data['packet']['source_revision'], 'status': 'ready', 'documents': {}})
    return data


def test_both_recorded_os_drafts_pass_unchanged(os_recorded):
    for draft in os_recorded['drafts']:
        original = deepcopy(draft)
        text, contexts = composition.render_body(draft, os_recorded['packet'])
        assert draft == original
        assert text == '\n\n'.join(p['text'] for p in draft['paragraphs'])
        assert contexts and '2.5学分' in text and '2.2' in text and '5.2' in text


@pytest.mark.parametrize('change', ['unknown', 'missing', 'wrong_binding', 'fake_credit', 'unbound_credit', 'quantity_as_id', 'extra_id'])
def test_quantity_exclusion_does_not_allow_invalid_indicators(os_recorded, change):
    draft = deepcopy(os_recorded['drafts'][0])
    p = draft['paragraphs'][0]
    if change == 'unknown':
        p['text'] += '另支持指标9.9。'
    elif change == 'missing':
        p['text'] = p['text'].split('；5.2')[0] + '。'
    elif change == 'wrong_binding':
        p['unit_ids'].remove('3979a35b1ae2:r2')
    elif change == 'fake_credit':
        p['text'] = p['text'].replace('2.5学分', '9.9学分')
    elif change == 'unbound_credit':
        p['unit_ids'].remove('3979a35b1ae2:course')
    elif change == 'quantity_as_id':
        p['text'] = '2.2学分；5.2 能够正确使用计算机及其各种先进语言及工具。'
    else:
        p['text'] += '另支持指标2.5。'
    with pytest.raises(ValueError):
        composition.render_body(draft, os_recorded['packet'])


def test_compiled_graph_preserves_context_for_real_entity_validation(monkeypatch):
    catalog = json.loads((Path(__file__).resolve().parents[2] / 'data/entity_catalog.json').read_text(encoding='utf8'))
    major_id = next(r['entity_id'] for r in catalog['entities']['major'] if r['canonical_name'] == '人工智能')
    prior = entity_prior.PriorContext(conversation_id='1', entities=(
        entity_prior.PriorEntity(entity_type='major', canonical_name='人工智能',
            entity_id=major_id, source='resolver_selected', source_turn=1),))
    pending = CC.PendingTask(task_type='query_course', missing_fields=('course',), created_turn=1, last_updated_turn=1)
    context = CC.ConversationContext(conversation_id='1', revision=2, prior_context=prior, pending_task=pending)
    seen = []

    async def decide(state, *, emit=None):
        seen.append(state)
        return {'tool_calls': [{'name': 'query_course', 'arguments': {'major': '人工智能', 'course': '操作系统'}}]}

    async def execute(state):
        assert state['validation_decision'] == 'allow'
        return {'final_answer': 'executed'}

    monkeypatch.setattr(graph, 'agent_decide', decide)
    monkeypatch.setattr(graph, 'execute_tools', execute)
    monkeypatch.setattr(graph, 'append_evidence', lambda state: {})
    monkeypatch.setattr(graph, 'route_after_tools', lambda state: 'wrap_up')
    async def finish(state, *, emit=None):
        return {'final_answer': state['final_answer']}
    monkeypatch.setattr(graph, 'wrap_up', finish)
    async def drive():
        return [x async for x in graph.run_stream([], '同一个专业，操作系统课程支撑哪些指标？',
            conversation_id=1, conversation_context=context)]
    done = next(v for k, v in asyncio.run(drive()) if k == 'done')
    assert seen[0]['prior_context'] == prior
    assert seen[0]['pending_task'] == pending
    assert done['call_validations'][0]['decision'] == 'allow'
    assert done['call_validations'][0]['bindings'][0]['narrowed_by'] == 'prior_context'


@pytest.mark.parametrize('kind', ['inactive', 'invalid_catalog', 'multiple', 'assistant_only', 'current_correction', 'model_invented'])
def test_compiled_context_fix_does_not_authorize_wrong_entities(monkeypatch, kind):
    catalog = json.loads((Path(__file__).resolve().parents[2] / 'data/entity_catalog.json').read_text(encoding='utf8'))
    ids = {r['canonical_name']: r['entity_id'] for r in catalog['entities']['major']}
    ai = entity_prior.PriorEntity(entity_type='major', canonical_name='人工智能',
        entity_id=ids['人工智能'], source='resolver_selected', source_turn=1,
        active=kind != 'inactive', catalog_valid=kind != 'invalid_catalog')
    entities = () if kind == 'assistant_only' else (ai,)
    if kind == 'multiple':
        entities += (entity_prior.PriorEntity(entity_type='major', canonical_name='软件工程',
            entity_id=ids['软件工程'], source='resolver_selected', source_turn=1),)
    context = CC.ConversationContext(conversation_id='1', revision=2,
        prior_context=entity_prior.PriorContext(conversation_id='1', entities=entities))
    question = ('不是人工智能，改查软件工程专业的操作系统课程支撑哪些指标？'
                if kind == 'current_correction' else '同一个专业，操作系统课程支撑哪些指标？')
    async def decide(state, **kwargs):
        return {'tool_calls': [{'name': 'query_course', 'arguments': {
            'major': '软件工程' if kind == 'model_invented' else '人工智能', 'course': '操作系统'}}]}
    async def forbidden(*args, **kwargs):
        pytest.fail('untrusted parameters must not execute')
    monkeypatch.setattr(graph, 'agent_decide', decide)
    monkeypatch.setattr(graph, 'execute_tools', forbidden)
    async def drive():
        return [e async for e in graph.run_stream([{'role': 'assistant', 'content': '你是人工智能专业'}],
            question, conversation_id=1, conversation_context=context)]
    done = next(v for k, v in asyncio.run(drive()) if k == 'done')
    assert done['call_validations'][0]['decision'] in {'reject', 'clarify'}
    assert done['calls'] == []


@pytest.mark.parametrize('kind', ['conversation_id', 'prior_snapshot', 'pending_snapshot'])
def test_mismatched_snapshot_is_stopped_before_generation(monkeypatch, kind):
    context = CC.ConversationContext(conversation_id='1', revision=1,
        prior_context=entity_prior.PriorContext(conversation_id='1'))
    kwargs = {'conversation_id': 2 if kind == 'conversation_id' else 1, 'conversation_context': context}
    if kind == 'prior_snapshot':
        kwargs['prior_context'] = entity_prior.PriorContext(conversation_id='other')
    if kind == 'pending_snapshot':
        kwargs['pending_task'] = CC.PendingTask(task_type='query_course', missing_fields=('major',), created_turn=1, last_updated_turn=1)
    async def forbidden(*args, **kwargs):
        pytest.fail('mismatched snapshot must stop before planning')
    monkeypatch.setattr(graph.model, 'decide', forbidden)
    async def drive():
        return [e async for e in graph.run_stream([], '查询课程', **kwargs)]
    with pytest.raises(ValueError):
        asyncio.run(drive())


def test_explicit_replacement_entity_remains_allowed():
    from entities import entity_resolver
    envelope = entity_resolver.resolve_question(entity_resolver.build_production_resolver(),
        '不是人工智能，改查软件工程专业的操作系统课程支撑哪些指标？')
    result = graph.validate_entity_calls({'resolution': envelope, 'tool_calls': [
        {'name': 'query_course', 'arguments': {'major': '软件工程', 'course': '操作系统'}}]})
    assert result['validation_decision'] == 'allow'
    assert result['validated_calls'][0]['arguments']['major'] == '软件工程'
