"""Offline checks for document metadata; no model, index or application startup."""
import hashlib
from types import SimpleNamespace

from documents.source_metadata import display_metadata, extract_metadata
from conversations import source_references


def by_key(extracted):
    return {item['key']: item for item in extracted['items']}


def test_issued_date_is_not_a_cited_policy_date():
    content = ('根据《其他规定》（2026年1月2日印发）制定本细则。\n\n'
               '学校校长办公室 2025 年7 月26 日印发')
    items = by_key(extract_metadata('本科生管理细则', content))
    assert items['issued_date']['value'] == '2025-07-26'
    assert items['issued_date']['state'] == 'confirmed'
    assert not {'published_date', 'published_year'} & items.keys()


def test_print_date_does_not_replace_applicable_cohort():
    items = by_key(extract_metadata('2025级人工智能专业培养方案(主修)',
                                   '打印日期:2026-07-09', scope={'level': 'undergraduate'}))
    assert items['printed_date']['value'] == '2026-07-09'
    assert items['printed_date']['detail'] is True
    assert '2025级' in items['audience']['value']
    assert not {'published_date', 'published_year', 'issued_date'} & items.keys()


def test_year_roles_and_original_filename_are_preserved():
    items = by_key(extract_metadata('教师条件（2022年修订）', '历史依据中提到2026年。',
                                   source_filename='通知（桂电人〔2021〕6号）.md'))
    assert items['revision_year']['value'] == '2022年'
    assert items['document_number_year']['value'] == '2021年'
    assert items['document_number_year']['evidence'][0]['source'] == 'filename'
    assert 'published_year' not in items
    assert by_key(extract_metadata('章程（2023年核准稿）', ''))['approval_year']['value'] == '2023年'


def test_date_formats_and_impossible_dates():
    items = by_key(extract_metadata('管理细则',
        '学校办公室\n二〇二五年七月二十六日印发\n打印日期：２０２６－０５－１０\n本细则自2025年8月1日起施行。'))
    assert items['issued_date']['value'] == '2025-07-26'
    assert items['printed_date']['value'] == '2026-05-10'
    assert items['effective_date']['value'] == '2025-08-01'
    assert not by_key(extract_metadata('管理细则', '学校办公室 2025年2月30日印发'))


def test_conflicting_dates_keep_both_evidence_locations():
    items = by_key(extract_metadata('管理细则',
        '学校办公室 2025年7月26日印发\n\n学校办公室 2025年8月1日印发'))
    assert items['issued_date']['state'] == 'conflict'
    assert '2025-07-26' in items['issued_date']['value'] and '2025-08-01' in items['issued_date']['value']
    assert len(items['issued_date']['evidence']) == 2


def test_audience_exceptions_and_cohort_ranges_keep_body_evidence():
    text = '本细则适用于学校普通本科学生,国际学生不在本细则适用范围。'
    item = by_key(extract_metadata('本科生管理细则', text))['audience']
    assert '国际学生不在' in item['value']
    assert item['evidence'][0]['text'] == text
    item = by_key(extract_metadata('硕士论文要求', '本要求自2023级硕士研究生开始施行。'))['audience']
    assert item['value'] == '2023级起硕士研究生'
    assert item['evidence'][0]['source'] == 'content'


def test_code_example_cannot_supply_current_document_metadata():
    content = ('```text\n学校办公室 2020年1月1日印发\n本办法适用于其他学院学生。\n```\n\n'
               '学校办公室 2025年7月26日印发')
    items = by_key(extract_metadata('管理细则', content))
    assert items['issued_date']['value'] == '2025-07-26'
    assert 'audience' not in items


def test_manual_overrides_and_category_do_not_rescan_body():
    extraction = extract_metadata('本科生细则', '发布日期：2025年1月1日\n本细则适用于本科学生。')
    document = SimpleNamespace(title='本科生细则', category='规章制度', source_metadata={
        'extracted': extraction, 'published_year': 2024, 'audience': '经核验的特定学生',
        'coverage': '学籍管理事项', 'basis': '管理员核验原文',
    })  # No content attribute: display must use saved metadata only.
    items = by_key(display_metadata(document, {}))
    assert items['published_year']['value'] == '2024年' and 'published_date' not in items
    assert items['audience']['value'] == '经核验的特定学生'
    assert items['coverage']['value'] == '学籍管理事项'
    assert items['category']['label'] == '资料类别'
    assert items['audience']['evidence'][0]['source'] == 'manual'
    document.category = ''
    assert 'category' not in by_key(display_metadata(document, {'category': '旧类别'}))
    assert display_metadata(document, {})['maintained'] is True


def test_legacy_document_and_empty_metadata_have_no_placeholders():
    document = SimpleNamespace(title='2025级人工智能专业培养方案(主修)', category='', source_metadata=None)
    items = by_key(display_metadata(document, {}))
    assert '2025级' in items['audience']['value'] and 'published_year' not in items
    document.title = '未注明信息的材料'
    assert display_metadata(document, {})['items'] == []


def test_read_reference_preserves_metadata_evidence_offsets_and_stale_guard(monkeypatch):
    from documents import document_availability
    monkeypatch.setattr(document_availability, 'require_ready', lambda *args: 1)
    monkeypatch.setattr(document_availability, 'require_source', lambda *args, **kwargs: None)
    monkeypatch.setattr(source_references, 'document_catalog', lambda: {})
    text = '🙂引用段落。\n\n' + '其他正文。' * 240 + '\n\n学校办公室 2025年7月26日印发'
    document = SimpleNamespace(id=1, title='管理细则', content=text, status='ready', category='',
                               source_metadata={'extracted': extract_metadata('管理细则', text)})
    reference = {'citation_id': 'S1', 'doc_id': '1', 'title': document.title,
                 'text': '🙂引用段落。', 'source_span': [0, 6],
                 'source_sha256': hashlib.sha256(text.encode()).hexdigest(), 'block_type': 'paragraph'}
    packet = source_references.read_reference(reference, document, full=True)
    assert packet['state'] == 'located'
    evidence = by_key(packet['metadata'])['issued_date']['evidence'][0]
    assert packet['text'][evidence['start']:evidence['end']].strip() == evidence['text']
    document.content += '\n原文已更新'
    packet = source_references.read_reference(reference, document, full=True)
    assert packet['state'] == 'unavailable' and packet['text'] == '' and packet['location'] is None


def test_admin_manual_save_retains_extraction_in_temporary_database(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from types import ModuleType
    from sqlalchemy import Text, create_engine
    from sqlalchemy.orm import Session
    from database.db import Document
    from routers.schemas import SourceMetadataBody

    # This endpoint does not use retrieval; keep its import from loading inference libraries.
    retrieval = ModuleType('offline_retrieval')
    retrieval.search_regulations_tool = lambda *args, **kwargs: None
    package = ModuleType('agent.tools.search_regulations')
    package.tool = retrieval
    monkeypatch.setitem(sys.modules, 'agent.tools.search_regulations', package)
    monkeypatch.setitem(sys.modules, 'agent.tools.search_regulations.tool', retrieval)
    spec = importlib.util.spec_from_file_location('_offline_admin_metadata',
        Path(__file__).resolve().parents[1] / 'routers/admin.py')
    admin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, admin)
    spec.loader.exec_module(admin)
    engine = create_engine('sqlite:///:memory:')
    monkeypatch.setattr(Document.__table__.c.content, 'type', Text())
    Document.__table__.create(engine)
    extraction = extract_metadata('本科细则', '学校办公室 2025年7月26日印发')
    try:
        with Session(engine) as session:
            document = Document(title='本科细则', source_metadata={'extracted': extraction})
            session.add(document)
            session.commit()
            admin.update_source_metadata(document.id, SourceMetadataBody(audience='核验对象'),
                                         user=SimpleNamespace(role='admin'), db=session)
            session.refresh(document)
            assert document.source_metadata['extracted'] == extraction
            assert document.source_metadata['audience'] == '核验对象'
    finally:
        engine.dispose()
