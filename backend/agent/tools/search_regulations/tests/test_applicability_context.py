"""Source-bound B1 links and local fault injection, not model quality scores."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent.core import EvidenceLedger, Source, ToolResult
from agent.core.evidence import chunk_id_of
from agent.core.task import interpret_task
from agent.runtime import answer_service as A, grounded_answer as G, graph, composition as C
from agent.tools.search_regulations.scripts.evidence_ops import evidence_units
from agent.tools.search_regulations.scripts.recall import linked_context


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    data = json.loads((Path(__file__).parent / 'fixtures/policy_applicability.json').read_text(encoding='utf-8'))
    root = Path(__file__).resolve().parents[5]
    for entry in data.values():
        doc = entry['document']
        assert hashlib.sha256((root / doc['source_path']).read_bytes()).hexdigest() == doc['source_sha256']
        assert entry['seed']['source_sha256'] == doc['indexed_source_sha256']
    rules = json.loads(linked_context.RULES_PATH.read_text(encoding='utf-8'))
    rules['documents'].extend(entry['rule'] for entry in data.values())
    path = tmp_path / 'links.json'
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(linked_context, 'RULES_PATH', path)
    return data


def assemble(candidate, key='degree', mode='complete'):
    entry = candidate[key]
    seed, target = deepcopy(entry['seed']), deepcopy(entry['target'])
    rows = [target]
    if mode == 'missing':
        rows = []
    elif mode == 'ambiguous':
        rows = [target, deepcopy(target)]
    elif mode == 'target_version':
        rows = [dict(target, source_sha256='different-version')]
    limit = evidence_units._cost(seed) if mode == 'budget' else 12000
    reader = None if mode == 'no_reader' else lambda _: rows
    return evidence_units.reassemble([seed], lambda *_: [], max_chars=limit, fetch_document=reader)


def state_for(rows, audit, question, *, missing_files=()):
    sources = [Source(title=r['source'], text=r['text'].strip(), heading_path=r['heading_path'],
        doc_id=str(r['document_id']), chunk_id=chunk_id_of(r['document_id'], r['chunk_idx'])) for r in rows]
    result = ToolResult(tool='search_regulations', ok=True,
        status='partial_evidence' if audit['incomplete'] or missing_files else 'success',
        data={'blocks': rows, 'evidence_units': audit, 'missing_files': list(missing_files)}, sources=sources)
    ledger = EvidenceLedger()
    ledger.add_many(result.tool, {'query': question}, result.sources)
    return {'question': question, 'tool_results': [result], 'evidence': {e.evidence_id: e for e in ledger.items()}}


def test_missing_required_link_limits_personal_conclusion_without_discarding_basis(candidate):
    rows, audit = assemble(candidate, mode='missing')
    question = '我是2020级本科生，能按2025年学位细则第二章判断我是否达到学位条件吗？'
    packet = G.evidence_packet(state_for(rows, audit, question))
    action = A.decide_next(interpret_task(question), packet)
    assert action.action == 'compose' and action.status == 'partial'
    assert action.gaps[0]['affects'] == 'personal_applicability'
    assert packet['evidence'] and '未取得' in action.text


@pytest.mark.parametrize('key', ['degree', 'minor'])
def test_complete_source_link_keeps_full_original_and_direction(candidate, key):
    entry = candidate[key]
    before = deepcopy(entry)
    rows, audit = assemble(candidate, key)
    assert len(rows) == 2 and not audit['incomplete'] and entry == before
    assert rows[-1]['text'] == entry['target']['text']
    requirement = rows[0]['evidence_unit']['context_requirements'][0]
    assert requirement['status'] == 'obtained'
    assert requirement['source_sha256'] == entry['document']['indexed_source_sha256']
    assert requirement['from_chunk_id'] == entry['seed']['chunk_id']
    assert requirement['target_chunk_id'] == entry['target']['chunk_id']
    assert requirement['to'] == entry['rule']['links'][0]['to']
    assert requirement['quote'] in rows[-1]['text']
    assert rows[-1]['evidence_unit']['relation'] == 'applicability'


@pytest.mark.parametrize('mode', ['missing', 'ambiguous', 'target_version', 'budget', 'no_reader'])
def test_required_failures_are_observable_in_packet_and_personal_delivery(candidate, mode):
    rows, audit = assemble(candidate, mode=mode)
    assert audit['incomplete'] and audit['groups'][0]['warnings']
    question = '我是2021级本科生，能按这份学位细则第二章判断我的授位条件吗？'
    packet = G.evidence_packet(state_for(rows, audit, question))
    gaps = packet['evidence_state']['gaps']
    assert len(gaps) == 1 and gaps[0]['origin'] == 'linked_context'
    assert A.decide_next(interpret_task(question), packet).status == 'partial'


def test_stale_rule_hash_cannot_attach_new_version(candidate):
    rules = json.loads(linked_context.RULES_PATH.read_text(encoding='utf-8'))
    rules['documents'][-2]['source_sha256'] = 'stale-reviewed-version'
    linked_context.RULES_PATH.write_text(json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    rows, audit = assemble(candidate)
    assert len(rows) == 1 and audit['incomplete']
    assert rows[0]['evidence_unit']['context_requirements'][0]['status'] == 'rule_version_mismatch'


def test_already_selected_target_does_not_require_another_read(candidate):
    entry = candidate['degree']
    rows, audit = evidence_units.reassemble([entry['seed'], entry['target']], lambda *_: [], max_chars=12000,
        fetch_document=lambda _: pytest.fail('Already obtained clause must be reused'))
    assert len(rows) == 2 and not audit['incomplete']


def test_minor_applicability_clause_does_not_link_to_itself(candidate):
    target = candidate['minor']['target']
    rows, audit = evidence_units.reassemble([target], lambda *_: [], max_chars=12000,
        fetch_document=lambda _: pytest.fail('Clause already contains its own scope'))
    assert len(rows) == 1 and not audit['incomplete']


def test_non_chapter_two_procedure_is_not_excluded_by_cohort(candidate):
    seed = candidate['degree']['other_procedure']
    rows, audit = evidence_units.reassemble([seed], lambda *_: [], max_chars=12000,
        fetch_document=lambda _: pytest.fail('Chapter-two applicability cannot control a chapter-four procedure'))
    packet = G.evidence_packet(state_for(rows, audit, '我是2020级本科生，不授学位决定的复核申请可以在多久内提出？'))
    assert '5 个工作日' in rows[0]['text'] and not packet['evidence_state']['gaps']
    assert A.decide_next(interpret_task(packet['question']), packet).status == 'answer'


@pytest.mark.parametrize('key,question', [
    ('degree', '我是2020级本科生，授位条件是否只能按2025年细则执行？'),
    ('degree', '2021级和2022级本科生适用这份学位细则第二章有何区别？'),
    ('minor', '我是2018级本科生，辅修能直接套用这份新办法吗？'),
    ('minor', '我是2019级本科生，辅修适用哪份办法？')])
def test_obtained_clause_keeps_choice_and_boundaries_without_fake_document_scope(candidate, key, question):
    rows, audit = assemble(candidate, key)
    packet = G.evidence_packet(state_for(rows, audit, question, missing_files=['旧文件具体条件未取得']))
    assert len(packet['evidence']) == 2 and not packet['evidence_state']['gaps']
    assert all(not r['scope'].get('cohort') for r in packet['evidence'])
    assert A.decide_next(interpret_task(question), packet).action == 'compose'
    text = rows[-1]['text']
    assert ('也可按' in text and '2015' in text) if key == 'degree' else ('2019 级之前年级' in text)


def test_general_rule_does_not_ask_for_unneeded_cohort(candidate):
    rows, audit = assemble(candidate, mode='missing')
    question = '学士学位的学分绩门槛是多少？'
    packet = G.evidence_packet(state_for(rows, audit, question))
    action = A.decide_next(interpret_task(question), packet)
    assert action.action == 'compose' and action.status != 'needs_input' and not action.gaps


def test_unused_unrelated_link_gap_does_not_restrict_a_supported_personal_answer(candidate, monkeypatch):
    degree, degree_audit = assemble(candidate)
    minor, minor_audit = assemble(candidate, 'minor', 'missing')
    question = '我是2020级本科生，授位条件是否只能按2025年学位细则执行？'
    state = state_for(degree + minor, minor_audit, question)
    packet = G.evidence_packet(state)
    assert len(packet['evidence']) == 3 and len(packet['evidence_state']['gaps']) == 1
    action = A.decide_next(interpret_task(question), packet)
    assert action.status == 'answer'  # Relevance is not proved by retrieving a candidate.
    calls = []

    async def compose(messages, facts, **kwargs):
        calls.append(facts)
        assert not facts.get('delivery_constraints')
        unit = next(u for u in facts['evidence_units'] if '也可按' in u['text'])
        return {'paragraphs': [{'text': unit['text'], 'unit_ids': [unit['unit_id']], 'calculation_ids': []}]}

    monkeypatch.setattr(graph.model, 'compose', compose)
    state['answer_protocol'] = C.PROTOCOL
    done = asyncio.run(graph._grounded_final(state, [], 0, None))
    assert len(calls) == 1 and done['answer']['status'] == 'answer'
    assert not done['answer']['gaps'] and not done['answer']['limitations']
    assert not done['answer_audit']['confirmed_gaps']


def test_partial_delivery_preserves_known_prose_and_exposes_only_dependent_limit(candidate, monkeypatch):
    rows, audit = assemble(candidate, mode='budget')
    question = '我是2020级本科生，请说明已取得条文，再判断能否适用于我。'
    state = state_for(rows, audit, question)
    state['answer_protocol'] = C.PROTOCOL
    calls = []

    async def compose(messages, packet, **kwargs):
        calls.append(deepcopy(packet))
        assert packet['delivery_constraints']['compose_scope'] == 'reference_description'
        uid = next(u['unit_id'] for u in packet['evidence_units'] if '达到70' in u['text'])
        return {'paragraphs': [{'text': '已取得的学位条件条文列有学分绩达到70的要求。', 'unit_ids': [uid], 'calculation_ids': []}]}

    monkeypatch.setattr(graph.model, 'compose', compose)
    done = asyncio.run(graph._grounded_final(state, [], 0, None))
    assert len(calls) == 1 and done['answer']['status'] == 'partial'
    assert '达到70' in done['answer']['text'] and done['answer']['limitations']
    assert done['answer']['gaps'][0]['affects'] == 'personal_applicability'


def test_used_mixed_source_gap_limits_only_its_own_source(candidate, monkeypatch):
    degree, _ = assemble(candidate)
    minor, audit = assemble(candidate, 'minor', 'missing')
    question = '我是2020级本科生，请说明学位条件和辅修办法是否适用于我。'
    state = state_for(degree + minor, audit, question)
    state['answer_protocol'] = C.PROTOCOL

    async def compose(messages, packet, **kwargs):
        assert not packet.get('delivery_constraints')
        units = [u for u in packet['evidence_units'] if '达到70' in u['text'] or '第十条' in u['heading_path']]
        assert len(units) == 2
        return {'paragraphs': [{'text': u['text'], 'unit_ids': [u['unit_id']], 'calculation_ids': []} for u in units]}

    monkeypatch.setattr(graph.model, 'compose', compose)
    done = asyncio.run(graph._grounded_final(state, [], 0, None))
    assert done['answer']['status'] == 'partial'
    limit = done['answer']['limitations'][0]
    from agent.core.citations import short_title
    assert short_title(candidate['minor']['document']['title']) in limit
    assert candidate['degree']['document']['title'] not in limit
    assert len(done['answer']['gaps']) == 1
    assert done['answer_audit']['confirmed_gaps'] == done['answer']['gaps']


@pytest.mark.parametrize('missing', [False, True])
def test_memory_index_to_tool_packet_provider_view_and_delivery(candidate, monkeypatch, missing):
    import httpx
    from pydantic import SecretStr
    from qdrant_client import QdrantClient, models
    from agent.runtime import registry
    from agent.tools.search_regulations.scripts.recall import retrieval
    from agent.tools.search_regulations.scripts.index_ops import indexing
    from agent.tools.search_regulations.scripts.evidence_ops.evidence_gate import GateDecision
    from core.config import settings

    entry = candidate['degree']
    seed = dict(entry['seed'], score=.99)
    payloads = [seed] if missing else [seed, entry['target']]
    client = QdrantClient(':memory:')
    client.create_collection('applicability-b1', vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    client.upsert('applicability-b1', points=[models.PointStruct(id=i, vector=[1., 0.], payload=r) for i, r in enumerate(payloads)])
    monkeypatch.setattr(indexing, '_client', client)
    monkeypatch.setattr(settings, 'qdrant_collection', 'applicability-b1')
    monkeypatch.setattr(retrieval, 'ensure_collection', lambda: None)
    monkeypatch.setattr(retrieval, '_absent_reference', lambda query: '')
    monkeypatch.setattr(retrieval, '_gate_anchors', lambda: None)
    monkeypatch.setattr(retrieval, '_retrieve_chunks', lambda *args, **kwargs: [seed])
    monkeypatch.setattr(retrieval, '_rerank', lambda q, rows: [r.update(rerank_score=.99) for r in rows])
    monkeypatch.setattr(retrieval, '_expand_flow', lambda rows: rows)
    monkeypatch.setattr(retrieval, '_follow_refs', lambda rows: rows)
    monkeypatch.setattr(retrieval, '_missing_files', lambda rows: ['2015年文件具体条文未取得'])
    monkeypatch.setattr(retrieval, 'decide', lambda *args: GateDecision('supported', 'controlled_seed', 'test/1', (), False, True))
    monkeypatch.setattr(settings, 'max_context_chars', 12000)
    monkeypatch.setattr(settings, 'final_answer_profile', 'writer-focus-20261024-1')
    monkeypatch.setattr(settings, 'final_answer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'deepseek_model', 'deepseek-flash')
    monkeypatch.setattr(settings, 'deepseek_api_key', SecretStr('offline-context-test'))
    question = '我是2020级本科生，授位条件是否只能按2025年学位细则执行？'
    sent = []

    async def send(client, request, *args, **kwargs):
        assert str(request.url) == 'https://api.deepseek.com/responses'
        body = json.loads(request.content)
        packed = json.loads(body['input'][-1]['content'])
        sent.append(packed)
        requirements = packed['evidence'][0]['evidence_unit']['context_requirements']
        assert requirements[0]['status'] == ('missing_or_ambiguous' if missing else 'obtained')
        assert 'source_revision' not in packed  # Original packet still authorizes version checks.
        if missing:
            assert packed['delivery_constraints']['compose_scope'] == 'reference_description'
            units = [u for u in packed['evidence_units'] if '达到70' in u['text']]
            text = '已取得条文列有学分绩达到70的要求。'
        else:
            assert any(r['text'] == entry['target']['text'].strip() for r in packed['evidence'])
            units = [u for u in packed['evidence_units'] if '也可按' in u['text']]
            text = units[0]['text']
        draft = {'paragraphs': [{'text': text, 'unit_ids': [u['unit_id'] for u in units], 'calculation_ids': []}]}
        return httpx.Response(200, request=request, json={'status': 'completed', 'model': 'offline-only', 'output_text': json.dumps(draft)})

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    try:
        result = registry.call('search_regulations', {'query': question})
        assert result.status == ('partial_evidence' if missing else 'success'), result.error
        ledger = EvidenceLedger()
        ledger.add_many(result.tool, {'query': question}, result.sources)
        state = {'question': question, 'tool_results': [result], 'evidence': {r.evidence_id: r for r in ledger.items()}}
        packet = G.evidence_packet(state)
        assert packet['source_revision'] is not None
        done = asyncio.run(graph._grounded_final(state, [], 0, None))
        assert len(sent) == 1 and done['answer']['status'] == ('partial' if missing else 'answer')
        assert '也可按' in done['answer']['text'] if not missing else bool(done['answer']['limitations'])
    finally:
        client.close()
