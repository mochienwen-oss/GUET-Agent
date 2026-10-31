"""Message-bound citations and positions in the stored knowledge document."""
import hashlib
import re

from agent.core.citations import clean_path, display_heading_path, short_title
from documents.catalog.source_metadata import display_metadata, document_catalog


MARKER = re.compile(r'(?<!\[)\[(S[1-9]\d*)\](?![\](])')


def tag_sources(result, identities):
    """Give real sources stable numbers within this answer, before the next model call."""
    for source in result.get('sources') or []:
        key = (source.get('doc_id'), source.get('chunk_id'), source.get('text_hash'))
        if key not in identities:
            identities[key] = 'S%d' % (len(identities) + 1)
        source['citation_id'] = identities[key]
    return result


def reference(source, revision):
    return {
        'source': source.get('title') or '',
        'title': short_title(source.get('title') or ''),
        'citation_id': source.get('citation_id'),
        'doc_id': source.get('doc_id'), 'chunk_id': source.get('chunk_id'),
        'text': source.get('text') or '', 'text_hash': source.get('text_hash'),
        'heading_path': clean_path(display_heading_path(source.get('heading_path') or [], source.get('text') or ''), source.get('title') or ''),
        'score': source.get('score'), 'source_span': source.get('source_span') or [],
        'source_sha256': source.get('source_sha256') or '',
        'block_type': source.get('block_type') or '', 'row_number': source.get('row_number'),
        'source_revision': revision,
    }


def cited_ids(text):
    # Examples inside code are not claims supported by an inline citation.
    prose = re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`', '', text)
    return set(MARKER.findall(prose))


def metadata_for(document, entry):
    return display_metadata(document, entry)


def prepare_references(contexts, text, db):
    from database.db import Document
    ids = {int(row['doc_id']) for row in contexts if str(row.get('doc_id') or '').isdigit()}
    documents = {str(row.id): row for row in db.query(
        Document.id, Document.title, Document.category, Document.source_metadata
    ).filter(Document.id.in_(ids)).all()} if ids else {}
    catalog = document_catalog()
    cited = cited_ids(text)
    result = []
    for context in contexts:
        row = dict(context, cited=context.get('citation_id') in cited)
        document = documents.get(str(row.get('doc_id')))
        if document:
            row['metadata'] = metadata_for(document, catalog.get(str(document.id), {}))
        result.append(row)
    return result


def read_reference(reference, document, *, full=False):
    """Read by document identity; never accept a browser-supplied filesystem path."""
    from documents.catalog.document_availability import SourceUnavailable, require_ready, require_source
    packet = {
        'citation_id': reference['citation_id'], 'title': reference.get('title') or reference.get('source'),
        'excerpt': reference.get('text') or '', 'metadata': reference.get('metadata') or {},
        'heading_path': reference.get('heading_path') or [],
        'row_number': reference.get('row_number'), 'block_type': reference.get('block_type'),
        'text': '', 'location': None, 'state': 'unavailable', 'view': 'full' if full else 'excerpt',
        'message': '',
    }
    if document is None:
        return dict(packet, message='原文已不可用，以下保留回答时的引用摘录。')
    try:
        revision = require_ready()
        require_source(document.title, reference.get('source_sha256'), document_id=document.id)
    except SourceUnavailable:
        return dict(packet, message='原文版本或可用状态已变化，暂不定位；以下为回答时的引用摘录。')
    if document.status != 'ready' or not document.content:
        return dict(packet, message='原文当前不可读取，以下保留回答时的引用摘录。')
    content = document.content
    # Match the existing indexing identity before using offsets in an updated document.
    expected = reference.get('source_sha256')
    version_matches = bool(expected) and hashlib.sha256(content.encode('utf-8')).hexdigest() == expected
    if expected and not version_matches:
        return dict(packet, message='原文已更新，旧引用不再定位到新版本；以下保留回答时的引用摘录。')
    packet['metadata'] = metadata_for(document, document_catalog().get(str(document.id), {}))
    span = reference.get('source_span') or []
    start = end = None
    if len(span) == 2 and all(isinstance(value, int) and not isinstance(value, bool) for value in span):
        a, b = span
        if 0 <= a < b <= len(content):
            # Paragraph text is reversible; a table source_span covers the original table.
            if ((reference.get('block_type') == 'table' and version_matches)
                    or (reference.get('block_type') != 'table' and content[a:b].strip() == packet['excerpt'].strip())):
                start, end = a, b
    if start is None and reference.get('block_type') != 'table' and packet['excerpt']:
        at = content.find(packet['excerpt'])
        if at >= 0 and content.find(packet['excerpt'], at + 1) < 0:
            start, end = at, at + len(packet['excerpt'])
    if start is None:
        packet.update(text=content if full else '', state='unlocated',
                      message='未能在当前原文中唯一定位。可查看引用摘录或全文。')
    else:
        if full:
            a, b = 0, len(content)
        else:
            a = content.rfind('\n\n', 0, max(0, start - 500))
            a = a + 2 if a >= 0 else 0
            b = content.find('\n\n', end + 500)
            b = b if b >= 0 else len(content)
        packet.update(text=content[a:b], state='located',
                      location={'start': start - a, 'end': end - a,
                                'line': content.count('\n', 0, start) + 1,
                                'row_number': reference.get('row_number')},
                      message='已定位到原表，标出对应行。' if reference.get('block_type') == 'table' else
                              '已定位到知识库保存的原文片段。')
    require_ready(revision)
    return packet
