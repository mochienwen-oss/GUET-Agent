"""Correct repeated record mentions preserve completeness and source binding."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.core.structured_records import check_enumerations
from agent.runtime import composition as C, graph, grounded_answer as G


@pytest.fixture
def recorded(monkeypatch):
    data = json.loads((Path(__file__).parent / 'fixtures/api_body_enumeration_repeat.json').read_text(encoding='utf-8'))
    packet = data['packet']
    digest = hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert digest == data['provenance']['packet_sha256']
    monkeypatch.setattr('documents.catalog.document_availability.snapshot', lambda: {
        'revision': packet['source_revision'], 'status': 'ready', 'documents': {}})
    return packet, data['draft']


def test_recorded_summary_and_details_are_delivered_unchanged(recorded):
    packet, draft = recorded
    before = deepcopy(draft)
    audit = {}
    text, contexts = C.render_body(draft, packet, audit=audit)
    assert draft == before
    assert text == '\n\n'.join(p['text'] for p in draft['paragraphs'])
    assert contexts and audit['semantic_verification'] == 'not_performed'


@pytest.mark.parametrize('kind', ['missing_record', 'missing_id', 'unknown_id', 'wrong_record', 'non_record', 'foreign_unit'])
def test_repeated_summary_does_not_hide_invalid_detail(recorded, kind):
    packet, draft = recorded
    records = packet['enumerations'][0]['records']
    last_id, last_unit = records[-1]['record_id'], records[-1]['unit_id']
    if kind == 'missing_record':
        draft['paragraphs'] = [p for p in draft['paragraphs'][1:] if last_unit not in p['unit_ids']]
    elif kind == 'missing_id':
        for paragraph in draft['paragraphs']:
            paragraph['text'] = paragraph['text'].replace(last_id, '第三项')
    elif kind == 'unknown_id':
        draft['paragraphs'][1]['text'] += '另支持9.9。'
    elif kind == 'wrong_record':
        draft['paragraphs'][1]['unit_ids'] = [last_unit]
    elif kind == 'non_record':
        draft['paragraphs'][1]['unit_ids'] = [next(u['unit_id'] for u in packet['evidence_units'] if u.get('unit_type') == 'course_field')]
    else:
        draft['paragraphs'][1]['unit_ids'] = ['foreign-record']
    with pytest.raises(ValueError, match='完整枚举|证据单元'):
        C.render_body(draft, packet)


def test_legacy_unique_delivery_contract_stays_strict(recorded):
    packet, draft = recorded
    with pytest.raises(ValueError, match='完整枚举'):
        check_enumerations(packet, draft['paragraphs'])


def test_actual_final_service_accepts_recorded_first_draft_without_repair(recorded, monkeypatch):
    packet, draft = recorded
    calls = []
    monkeypatch.setattr(G, 'evidence_packet', lambda state: deepcopy(packet))

    async def compose(messages, facts, **kwargs):
        calls.append(kwargs)
        return deepcopy(draft)

    monkeypatch.setattr(graph.model, 'compose', compose)
    result = asyncio.run(graph._grounded_final({'question': packet['question'], 'answer_protocol': C.PROTOCOL}, [], 0, None))
    assert result['answer']['status'] == 'answer'
    assert len(calls) == 1 and not result['answer_audit'].get('validation_retry')
    assert not result['answer_audit'].get('validation_error')
    assert all(p['text'] in result['answer']['text'] for p in draft['paragraphs'])
