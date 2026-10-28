"""Extract source-bound display information once, outside the question path."""
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from agent.core.scope import document_scope


LABELS = {
    'issued_date': '印发日期', 'published_date': '发布日期',
    'effective_date': '施行日期', 'published_year': '发布年份',
    'revision_year': '修订年份', 'approval_year': '核准年份',
    'document_number_year': '文号年份', 'audience': '适用对象',
    'coverage': '适用范围', 'training_type': '培养类型',
    'category': '资料类别', 'printed_date': '打印日期',
}
YEAR = r'(?:[12１２][0-9０-９]{3}|[一二][一二三四五六七八九〇零○]{3})'
NUMBER = r'[0-9０-９一二三四五六七八九十〇零]+'
DATE = re.compile(
    rf'(?P<year>{YEAR})\s*年\s*(?P<month>{NUMBER})\s*月\s*(?P<day>{NUMBER})\s*[日号]'
    r'|(?P<iso_year>[12１２][0-9０-９]{3})\s*[-/.－／．]\s*(?P<iso_month>[0-9０-９]{1,2})\s*[-/.－／．]\s*(?P<iso_day>[0-9０-９]{1,2})'
)
OWN = r'本(?:办法|规定|细则|要求|条例|规则|制度|方案|标准|条件)'


def document_catalog(path=None):
    if path is None:
        from core.config import settings
        path = settings.active_documents_path
    path = Path(path)
    if not path.is_file():
        return {}
    return {str(row['document_id']): row for row in
            json.loads(path.read_text(encoding='utf-8-sig')).get('documents', [])}


def _number(value):
    value = unicodedata.normalize('NFKC', value)
    digits = str.maketrans('一二三四五六七八九〇零○', '123456789000')
    if '十' in value:
        left, right = value.split('十', 1)
        return int(left.translate(digits) or '1') * 10 + int(right.translate(digits) or '0')
    return int(value.translate(digits))


def _date_value(match):
    try:
        parts = [match[key] or match['iso_' + key] for key in ('year', 'month', 'day')]
        return date(*(_number(value) for value in parts)).isoformat()
    except (ValueError, TypeError):
        return None


def _evidence(source, text, *, content='', start=None, end=None):
    item = {'source': source, 'text': text}
    if start is not None:
        item.update(start=start, end=end, line=content.count('\n', 0, start) + 1)
    return item


def _scope_audience(scope):
    values = []
    if scope.get('cohort'):
        values.append(str(scope['cohort']) + '级')
    if scope.get('college'):
        values.append(scope['college'])
    if scope.get('major'):
        major = scope['major']
        values.append(major if major.endswith('专业') else major + '专业')
    level = {'undergraduate': '本科生', 'graduate': '研究生'}.get(scope.get('level'))
    subject = {'staff': '教职工', 'all': '全体师生', 'student': level or '学生'}.get(scope.get('subject'))
    if subject or level:
        values.append(subject or level)
    return ' · '.join(values)


def extract_metadata(title, content, *, source_filename='', scope=None):
    """Use explicit roles, never the largest year or a date in a cited rule."""
    candidates = {}

    def add(key, value, evidence):
        if value:
            candidates.setdefault(key, {}).setdefault(str(value), []).append(evidence)

    # Blank examples without moving the offsets used to locate real body evidence.
    prose = re.sub(r'(?m)^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^ {0,3}\1[ \t]*$|\Z)',
                   lambda match: re.sub(r'[^\n]', ' ', match[0]), content)
    # Original offsets are retained; normalization is only used for interpreting values.
    for match in DATE.finditer(prose):
        value = _date_value(match)
        if not value:
            continue
        a = content.rfind('\n', 0, match.start()) + 1
        if a > 0 and not content[a:match.start()].strip():
            previous_end = a - 1
            previous_start = content.rfind('\n', 0, previous_end) + 1
            if re.search(r'(?:办公室|办公厅|大学|学院|打印日期\s*[:：]?|发布日期\s*[:：]?)\s*$',
                         content[previous_start:previous_end]):
                a = previous_start
        b = content.find('\n', match.end())
        b = b if b >= 0 else len(content)
        line = content[a:b]
        before, after = content[a:match.start()], content[match.end():b]
        if '<td' in line or line.lstrip().startswith(('>', '```')):
            continue
        # Quoted policy dates are not the current document's own date.
        if '《' in line or '》' in line:
            continue
        key = None
        if re.search(r'打印(?:日期|时间)\s*[:：]?\s*$', before):
            key = 'printed_date'
        elif re.match(r'\s*印发\s*[。.;；]?\s*$', after) and re.search(
                r'(?:办公室|办公厅|人民政府|教育部|教育厅|委员会|大学|学院)\s*$', before):
            key = 'issued_date'
        elif re.search(r'^(?:\s*#{1,6})?\s*发布(?:日期|时间)\s*[:：]?\s*$', before):
            key = 'published_date'
        elif re.search(OWN + r'(?:自|于)\s*$', before) and re.match(
                r'\s*(?:起|开始)?(?:施行|实施|生效)', after):
            key = 'effective_date'
        if key:
            add(key, value, _evidence('content', line.strip(), content=content, start=a, end=b))

    # Scope statements in attachments describe their own form/rules, not always the parent.
    attachment = re.search(r'(?m)^\s*(?:#{1,6}\s*)?附件\s*(?:[0-9一二三四五六七八九十]+\s*)?[:：]?\s*$', prose)
    main_end = attachment.start() if attachment else len(content)
    main = prose[:main_end]
    statements = list(re.finditer(
        OWN + r'(?:的)?(?:适用于|适用范围为|适用范围包括|适用对象为)[^。\n]{2,400}(?:。|(?=\n|$))', main))
    body_audience = []
    for match in statements:
        prefix = main[max(0, main.rfind('。', 0, match.start()) + 1):match.start()]
        line_prefix = main[main.rfind('\n', 0, match.start()) + 1:match.start()]
        if prefix.count('《') > prefix.count('》') or '<td' in prefix or line_prefix.lstrip().startswith('>'):
            continue
        evidence = _evidence('content', match[0], content=content, start=match.start(), end=match.end())
        target = re.split(r'适用于|适用对象为', match[0], maxsplit=1)
        if (len(target) == 2 and re.search(r'学生|本科生|研究生|新生|教职工|教师|职工|人员|师生', target[1])
                and not re.search(r'(?:管理|工作|业务|事项|活动|课程|项目)[。]?$', target[1])):
            body_audience.append((target[1].rstrip('。'), evidence))
        else:
            add('coverage', match[0], evidence)

    title_scope = document_scope(title)
    scope = document_scope(title, metadata=scope)
    audience = _scope_audience(scope)
    registered = any(scope.get(key) != title_scope.get(key)
                     for key in ('cohort', 'college', 'major', 'level', 'subject'))
    audience_evidence = _evidence('catalog', '已登记的适用信息：' + audience) if registered else _evidence('title', title)
    cohort = re.search(OWN + r'自\s*(20\d{2})\s*级([^。\n,，;；]{0,35}?)(?:开始)?(?:施行|实施)', main)
    if body_audience:
        for value, evidence in body_audience:
            add('audience', value, evidence)
    elif cohort:
        add('audience', cohort[1] + '级起' + cohort[2], _evidence(
            'content', cohort[0], content=content, start=cohort.start(), end=cohort.end()))
    elif audience:
        add('audience', audience, audience_evidence)
    for source, value in (('title', title), ('filename', source_filename)):
        normalized = unicodedata.normalize('NFKC', value)
        for match in re.finditer(r'(20\d{2})\s*年\s*(修订|修正|核准)', normalized):
            key = 'approval_year' if match[2] == '核准' else 'revision_year'
            add(key, match[1] + '年', _evidence(source, value))
        for match in re.finditer(r'[〔\[(](20\d{2})[〕\])]\s*\d+\s*号', normalized):
            add('document_number_year', match[1] + '年', _evidence(source, value))
    training = re.search(r'[（(](主修|辅修)[）)]', title)
    if training:
        add('training_type', training[1], _evidence('title', title))

    items = []
    for key in LABELS:
        values = candidates.get(key, {})
        if not values:
            continue
        conflict = len(values) > 1 and key not in {'audience', 'coverage'}
        evidence = []
        for rows in values.values():
            for row in rows:
                if row not in evidence:
                    evidence.append(row)
        items.append({'key': key, 'label': LABELS[key],
                      'value': (' / ' if conflict else '；').join(values),
                      'state': 'conflict' if conflict else 'confirmed',
                      'detail': key == 'printed_date', 'evidence': evidence})
    return {'source_filename': source_filename, 'items': items}


def display_metadata(document, entry):
    """Project saved extraction plus manual overrides; never scan body on a question."""
    stored = document.source_metadata or {}
    extracted = stored.get('extracted') or {}
    items = [dict(item) for item in extracted.get('items', [])]
    if not extracted:
        # Legacy documents can still show explicit title/catalog scope without a body read.
        items = extract_metadata(document.title, '', scope=entry.get('scope'))['items']
    for key in ('published_year', 'audience', 'coverage'):
        value = stored.get(key)
        if value is None or value == '':
            continue
        replaced = {key, 'published_date'} if key == 'published_year' else {key}
        items = [item for item in items if item['key'] not in replaced]
        items.append({'key': key, 'label': LABELS[key],
                      'value': str(value) + '年' if key == 'published_year' else value,
                      'state': 'confirmed', 'detail': False,
                      'evidence': [_evidence('manual', stored.get('basis') or '管理员维护的信息')]})
    category = document.category or ''
    if category:
        items.append({'key': 'category', 'label': LABELS['category'], 'value': category,
                      'state': 'confirmed', 'detail': False,
                      'evidence': [_evidence('catalog', '知识库登记的资料类别：' + category)]})
    # A document-number year adds no information when an own dated/revised version agrees.
    years = {re.match(r'(\d{4})', item['value'])[1] for item in items
             if item['key'] in {'issued_date', 'published_date', 'revision_year', 'approval_year'}
             and item['state'] == 'confirmed' and re.match(r'\d{4}', item['value'])}
    items = [item for item in items if not (item['key'] == 'document_number_year'
             and item['state'] == 'confirmed' and item['value'][:4] in years)]
    return {'items': sorted(items, key=lambda item: list(LABELS).index(item['key'])),
            'maintained': any(stored.get(key) not in (None, '')
                              for key in ('published_year', 'audience', 'coverage', 'basis'))}
