"""Literal table fields and explicit document links, not answer judgments."""
import json
from pathlib import Path
from core.paths import BACKEND_ROOT
import re
import unicodedata

from agent.core.citations import clean_path, short_title
from documents.catalog.document_chunks import HEADER_WORDS

RULES_PATH = BACKEND_ROOT / 'data/regulation_context_links.json'
HEADER_LABELS = HEADER_WORDS | {'标准', '分值', '项目类型', '竞赛类别', '获奖等级', '负责人'}


def _norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def restore_headers(seed, fetch_related):
    """Project known row cells with the literal headers of the same table.

    This is read-time enrichment: vector payloads/indices are not rewritten.
    Unknown headers remain unknown, and numeric units are never guessed.
    """
    fields = seed.get('table_fields') or []
    if seed.get('block_type') != 'table' or not fields:
        return seed
    if not any(re.fullmatch(r'列[\d、]+', f.get('header', '')) for f in fields):
        return seed
    rows = fetch_related(seed, {'row_number': 1})
    if len(rows) != 1:
        return seed
    first = rows[0]
    if any(not seed.get(k) or first.get(k) != seed[k] for k in
           ('source_sha256', 'chunking_version', 'parent_id', 'section_parent_id', 'source')):
        return seed
    if first.get('document_id') != seed.get('document_id'):
        return seed
    headers = first.get('table_fields') or []
    if (not headers or sum(_norm(f.get('text')) in HEADER_LABELS for f in headers) < 2
            or any(f.get('rowspan', 1) > 1 for f in headers)):
        return seed  # Multirow/unrecognized headers need more than a first-row projection.
    if seed.get('row_number') == 1:
        return dict(seed, evidence_relation='table_header',
                    table_context={'basis': 'source_first_row', 'header_only': True})
    body, sep, suffix = seed['text'].partition('\n\n同节正文／表外说明：')
    expected = '\n'.join(f"{f['header']}：{f.get('text') or '〔空白，原表未填写〕'}" for f in fields)
    if body != expected:
        return seed
    labels = {col: h['text'].replace('\n', ' / ') for h in headers
              for col in h.get('columns', []) if h.get('text')}
    projected = []
    for field in fields:
        names = list(dict.fromkeys(labels[c] for c in field.get('columns', []) if c in labels))
        projected.append(dict(field, header=' / '.join(names) or field['header']))
    text = '\n'.join(f"{f['header']}：{f.get('text') or '〔空白，原表未填写〕'}" for f in projected) + sep + suffix
    return dict(seed, text=text, snippet=text, raw_text=seed['text'], table_fields=projected,
                review_flags=[f for f in seed.get('review_flags', []) if '表头未确认' not in f],
                table_context={'basis': 'same_source_table_first_row',
                               'header_chunk_id': first.get('chunk_id'), 'numeric_unit_inferred': False})


def links_for(seed):
    if not RULES_PATH.exists():
        return []
    rules = json.loads(RULES_PATH.read_text(encoding='utf-8'))
    if rules.get('version') != 'regulation-context-links/1':
        raise ValueError('Unsupported regulation context link version')
    path = [_norm(p) for p in clean_path(seed.get('heading_path') or [], seed.get('source') or '')]
    title = _norm(short_title(seed.get('source') or ''))
    return [dict(link, source_sha256=document['source_sha256']) if document.get('source_sha256') else link
            for document in rules['documents'] if _norm(short_title(document['title'])) == title
            for link in document['links']
            if path[:len(link['from'])] == [_norm(p) for p in link['from']]
            and (not link.get('required_for') or path != [_norm(p) for p in link['to']])
            and (not link.get('block_type') or seed.get('block_type') == link['block_type'])]


def resolve_link(seed, link, rows):
    wanted = [_norm(p) for p in link['to']]
    return [r for r in rows if r.get('document_id') == seed.get('document_id')
            and all(r.get(k) == seed.get(k) and seed.get(k) for k in
                    ('source', 'source_sha256', 'chunking_version'))
            and [_norm(p) for p in clean_path(r.get('heading_path') or [], r.get('source') or '')] == wanted
            and link['quote'] in r.get('text', '')]
