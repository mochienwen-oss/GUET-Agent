from copy import deepcopy
import pytest

from agent.core.scope import business_scope_conflict
from agent.tools.search_regulations.scripts.evidence_ops import evidence_units
from agent.tools.search_regulations.scripts.recall import linked_context


def row(idx, title='样本规定', path=None, text='原始正文', **extra):
    return dict(document_id=1, chunk_idx=idx, chunk_id=f'chunk-{idx}', source=title,
                source_sha256='same-source', chunking_version='structure-evidence/1',
                parent_id=f'parent-{idx}', section_parent_id=f'section-{idx}',
                heading_path=[title] + (path or []), text=text, snippet=text,
                block_type='paragraph', **extra)


def test_literal_table_headers_restore_rank_without_inventing_credits():
    headers = ['竞赛类别', '获奖等级', '第1 名', '第2 名']
    values = ['A 类竞赛', '二等奖', '95', '86']
    def fields(items):
        return [dict(header=f'列{i}', text=value, columns=[i], rowspan=1)
                for i, value in enumerate(items, 1)]
    seed = row(2, text='\n'.join(f'列{i}：{v}' for i, v in enumerate(values, 1)),
               row_number=3, table_fields=fields(values))
    seed.update(block_type='table', parent_id='table', section_parent_id='section')
    first = dict(seed, chunk_id='header', chunk_idx=0, row_number=1, table_fields=fields(headers))
    original = deepcopy(seed)
    restored = linked_context.restore_headers(seed, lambda *_: [first])
    assert '第1 名：95' in restored['text'] and '竞赛类别：A 类竞赛' in restored['text']
    assert '学分' not in restored['text']
    assert restored['table_context']['numeric_unit_inferred'] is False
    assert seed == original and restored['raw_text'] == original['text']
    wrong = dict(first, source_sha256='another-source')
    assert linked_context.restore_headers(seed, lambda *_: [wrong]) == seed
    assert linked_context.restore_headers(seed, lambda *_: [first, first]) == seed


@pytest.mark.parametrize('headers', [['A类竞赛', '一等奖'], ['张三', '95']])
def test_data_first_rows_do_not_become_guessed_headers(headers):
    seed = row(2, row_number=2, table_fields=[{'header': '列1', 'text': '值', 'columns': [1]}])
    seed['block_type'] = 'table'
    first = dict(seed, table_fields=[{'text': value, 'columns': [i], 'rowspan': 1}
                                    for i, value in enumerate(headers, 1)])
    assert linked_context.restore_headers(seed, lambda *_: [first]) == seed


LINKS = [
    ('桂林电子科技大学本科生学业预警及学业退学实施细则', ['第二章学业预警', '第五条'],
     ['第四章附则', '第十一条'], '2024 年及之前入学的普通本科学生不执行退学预警和学业退学条款。', 'applicability'),
    ('桂林电子科技大学“大学英语”课程替换及成绩认定办法(试行)', ['二、成绩认定', '(三)认定程序'],
     ['二、成绩认定', '(一) 认定条件'], '达到以下要求者可申请认定课程。', 'prerequisite'),
    ('桂林电子科技大学创新创业拓展课程学分认定管理办法', ['第三章认定标准', '第四条', '(三)学科竞赛类'],
     ['第三章认定标准', '第五条'], '各学院组织认定、统计成绩(百分制),并在教务系统录入。', 'measurement_basis'),
]


@pytest.mark.parametrize('title,path,target,text,relation', LINKS)
def test_explicit_links_keep_complete_originals_and_independent_ids(title, path, target, text, relation):
    seed, linked = row(1, title, path), row(9, title, target, text)
    if relation == 'measurement_basis':
        seed['block_type'] = 'table'
        seed['table_fields'] = [{'header': '分值', 'text': '95'}]
    result, audit = evidence_units.reassemble([seed], lambda *_: [], max_chars=8000,
                                              fetch_document=lambda _: [linked])
    assert [r['chunk_id'] for r in result] == ['chunk-1', 'chunk-9']
    assert result[1]['text'] == text and result[1].get('rerank_score') is None
    assert result[1]['evidence_unit']['relation'] == relation
    assert result[1]['evidence_unit']['related_to_chunk_id'] == 'chunk-1'
    assert not audit['incomplete']


def test_link_missing_wrong_scope_and_budget_are_observable_not_top_one():
    title, path, target, text, _ = LINKS[0]
    seed, linked = row(1, title, path), row(9, title, target, text)
    for rows in ([], [linked, linked], [dict(linked, document_id=2)], [dict(linked, source_sha256='other')]):
        out, audit = evidence_units.reassemble([seed], lambda *_: [], max_chars=8000,
                                               fetch_document=lambda _: rows)
        assert len(out) == 1 and audit['incomplete']
    out, audit = evidence_units.reassemble([seed], lambda *_: [], max_chars=evidence_units._cost(seed),
                                           fetch_document=lambda _: [linked])
    assert len(out) == 1 and audit['groups'][0]['warnings'] == ['budget_omitted:applicability']


def test_unlisted_document_does_not_expand_by_shared_topic():
    seed = row(1, '另一份预警规定', ['第二章学业预警'])
    def forbidden(_):
        pytest.fail('No generic same-document or topic expansion')
    out, audit = evidence_units.reassemble([seed], lambda *_: [], max_chars=8000, fetch_document=forbidden)
    assert len(out) == 1 and not audit['incomplete']


@pytest.mark.parametrize('query,title,conflict', [
    ('国境外交流期间能自行延期吗？', '桂林电子科技大学全日制普通本科生国内交换培养管理办法', True),
    ('国内交换学习能延期吗？', '桂林电子科技大学赴国(境)外交流学习成绩管理办法', True),
    ('国内和国外交流期限有什么不同？', '桂林电子科技大学国内交换培养管理办法', False),
    ('国境外交流期间能延期吗？', '桂林电子科技大学学生学籍管理规定', False),
    ('交流期间能延期吗？', '桂林电子科技大学国内交换培养管理办法', False),
])
def test_exchange_scope_only_rejects_explicit_conflicts(query, title, conflict):
    assert bool(business_scope_conflict(query, title)) is conflict
