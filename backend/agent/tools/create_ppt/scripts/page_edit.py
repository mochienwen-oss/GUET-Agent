"""Direct page text edits and page reordering on the real SVG files.

两项操作都只改 svg_output 里的真实页面文件，然后重新导出并走同一条
publish 校验链；页面的 slide_id 不会因为改字或挪动位置而变化。
"""
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from .master_workspace import MasterWorkspace, slide_files

SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)

MAX_TEXT = 400          # 单个文本单元字符上限，避免一次写入整页内容
MAX_EDITS = 60          # 单次提交的文本单元数量上限
_NAME_RE = re.compile(r'^(\d+)_(.+)$')


def _svg_dir(project):
    return Path(project) / 'svg_output'


def _text_nodes(root):
    return [node for node in root.iter('{%s}text' % SVG)]


def _units(node, index):
    """把一个 <text> 拆成可编辑单元：无子元素一处，多 tspan 逐个。"""
    children = [child for child in list(node)
                if child.tag in ('{%s}tspan' % SVG, 'tspan')]
    if not children:
        return [{'id': 't%d' % index, 'node': node,
                 'text': (node.text or ''), 'size': node.get('font-size'),
                 'weight': node.get('font-weight')}]
    return [{'id': 't%d.%d' % (index, j), 'node': child,
             'text': (child.text or ''), 'size': child.get('font-size') or node.get('font-size'),
             'weight': child.get('font-weight') or node.get('font-weight')}
            for j, child in enumerate(children)]


def _all_units(root):
    out = []
    for index, node in enumerate(_text_nodes(root)):
        out.extend(_units(node, index))
    return out


def read_page_texts(project, master_file):
    """返回该页当前可编辑的文本单元，顺序即文件中的绘制顺序。"""
    path = _svg_dir(project) / master_file
    if not path.is_file():
        raise ValueError('页面文件不存在，请刷新后重试')
    root = ET.parse(path).getroot()
    return [{'id': unit['id'], 'text': unit['text'],
             'size': unit['size'] or '', 'weight': unit['weight'] or ''}
            for unit in _all_units(root) if unit['text'].strip()]


def apply_page_texts(project, master_file, edits):
    """按 id 改写文本。edits 里的 original 必须与当前内容一致，否则整批拒绝。"""
    if not edits:
        raise ValueError('没有需要保存的文字修改')
    if len(edits) > MAX_EDITS:
        raise ValueError('一次最多修改 %d 处文字' % MAX_EDITS)
    path = _svg_dir(project) / master_file
    if not path.is_file():
        raise ValueError('页面文件不存在，请刷新后重试')
    tree = ET.parse(path)
    units = {unit['id']: unit for unit in _all_units(tree.getroot())}
    staged = []
    for edit in edits:
        unit_id = str(edit.get('id') or '')
        text = edit.get('text')
        unit = units.get(unit_id)
        if unit is None:
            raise ValueError('这处文字已变化，请刷新后重新编辑')
        if not isinstance(text, str):
            raise ValueError('文字内容无效')
        text = text.strip()
        if not text:
            raise ValueError('文字不能为空；如需删除整块请交给助手调整版面')
        if len(text) > MAX_TEXT:
            raise ValueError('单处文字不能超过 %d 字' % MAX_TEXT)
        original = edit.get('original')
        if original is not None and (original or '').strip() != unit['text'].strip():
            raise ValueError('页面内容已更新，请刷新后重新编辑')
        staged.append((unit['node'], text))
    for node, text in staged:
        node.text = text
    output = ET.tostring(tree.getroot(), encoding='unicode')
    path.write_text(output, encoding='utf-8')
    return [{'id': edit['id'], 'text': edit['text'].strip()} for edit in edits]


def _semantic(name):
    match = _NAME_RE.match(name)
    return match.group(2) if match else name


def _rename_map(order_files):
    """按给定顺序生成新文件名，保留原文件名里的语义部分。"""
    used = set()
    mapping = {}
    for index, name in enumerate(order_files, 1):
        semantic = _semantic(Path(name).stem)
        candidate = '%02d_%s.svg' % (index, semantic)
        suffix = 2
        while candidate in used:
            candidate = '%02d_%s_%d.svg' % (index, semantic, suffix)
            suffix += 1
        used.add(candidate)
        mapping[name] = candidate
    return mapping


def reorder_pages(project, slide_ids, slides):
    """按 slide_id 顺序重排页面文件；没有出现在列表里的页面会被拒绝。"""
    folder = _svg_dir(project)
    current = [path.name for path in slide_files(folder)]
    by_id = {slide['slide_id']: slide['master_file'] for slide in slides}
    if len(set(slide_ids)) != len(slide_ids) or any(sid not in by_id for sid in slide_ids):
        raise ValueError('页面顺序包含不属于当前作品的页面')
    if len(slide_ids) != len(current):
        raise ValueError('页面顺序必须覆盖全部页面')
    ordered = [by_id[sid] for sid in slide_ids]
    if set(ordered) != set(current):
        raise ValueError('页面文件与当前作品不一致，请刷新后重试')
    if ordered == current:
        return {'changed': False, 'files': current, 'mapping': {}}

    mapping = _rename_map(ordered)
    # 两阶段改名，避免新旧名字互相占用
    temp = {}
    for index, name in enumerate(ordered):
        stage_name = '_reorder_%02d.tmp' % index
        (folder / name).rename(folder / stage_name)
        temp[stage_name] = mapping[name]
    for stage_name, final_name in temp.items():
        (folder / stage_name).rename(folder / final_name)

    # 旁挂文件跟着改名：视觉终稿与逐页备注
    for old, new in mapping.items():
        for other in ((folder.parent / 'svg_final' / old),
                      (folder.parent / 'notes' / (Path(old).stem + '.md'))):
            if other.is_file():
                other.rename(other.with_name(new if other.suffix == '.svg'
                                             else Path(new).stem + other.suffix))

    _rewrite_manifest(folder.parent, mapping)
    return {'changed': True, 'files': [mapping[name] for name in ordered], 'mapping': mapping}


def _rewrite_manifest(project, mapping):
    """把页面清单按新文件名重写，保住 slide_id/title/purpose，避免标识串位。"""
    path = Path(project) / 'page_manifest.json'
    old_by_file = {}
    if path.is_file():
        try:
            old_by_file = {page['file']: page
                           for page in json.loads(path.read_text(encoding='utf-8')).get('pages', [])}
        except (OSError, ValueError, KeyError, TypeError):
            old_by_file = {}
    pages = []
    for old, new in mapping.items():
        page = dict(old_by_file.get(old) or {})
        page['file'] = new
        pages.append(page)
    path.write_text(json.dumps({'pages': pages}, ensure_ascii=False, indent=2), encoding='utf-8')


def undo_reorder(project, mapping):
    """重排后导出失败时把文件名与清单还原，避免顺序停在半途。"""
    folder = _svg_dir(project)
    for old, new in mapping.items():
        if (folder / new).is_file():
            (folder / new).rename(folder / old)
        for other_new, other_old in ((folder.parent / 'svg_final' / new,
                                      folder.parent / 'svg_final' / old),
                                     (folder.parent / 'notes' / (Path(new).stem + '.md'),
                                      folder.parent / 'notes' / (Path(old).stem + '.md'))):
            if other_new.is_file():
                other_new.rename(other_old)
    path = folder.parent / 'page_manifest.json'
    if not path.is_file():
        return
    try:
        pages = json.loads(path.read_text(encoding='utf-8')).get('pages', [])
    except (OSError, ValueError):
        return
    back = {new: old for old, new in mapping.items()}
    for page in pages:
        page['file'] = back.get(page.get('file'), page.get('file'))
    path.write_text(json.dumps({'pages': pages}, ensure_ascii=False, indent=2), encoding='utf-8')


def republish(state, session, *, activity):
    """改完文件后重新导出真实 PPTX，并走与生成时相同的发布校验。"""
    workspace = MasterWorkspace(session['project'])
    check = workspace.run('svg_quality_checker.py',
                          [str(workspace.folder), '--canonical-authoring', '--stage', 'final', '--json'])
    if check['exit_code']:
        raise ValueError('页面改动未通过原版导出前检查：' + (check.get('output') or '').strip()[-600:])

    before = {path: (path.stat().st_mtime_ns, path.stat().st_size)
              for path in workspace.folder.rglob('*.pptx')}
    result = workspace.run('svg_to_pptx.py', [str(workspace.folder)])
    receipt = {'script': 'svg_to_pptx.py', 'arguments': [str(workspace.folder)],
               'exit_code': result['exit_code']}
    if result['exit_code'] == 0:
        outputs = [path for path in workspace.folder.rglob('*.pptx')
                   if before.get(path) != (path.stat().st_mtime_ns, path.stat().st_size)]
        if outputs:
            receipt['output'] = str(max(outputs, key=lambda path: path.stat().st_mtime_ns))
    for command in session.get('commands') or []:
        if command['script'] == 'svg_to_pptx.py':
            command['stale'] = True
    session.setdefault('commands', []).append(receipt)
    if result['exit_code'] or not receipt.get('output'):
        detail = (result.get('output') or '').strip()[-800:] or '原版导出脚本未返回成功的PPTX文件'
        raise ValueError('重新导出未成功：' + detail)

    published = workspace.publish(state, session)
    state.update(published, stage='draft', next_node=None, activity=activity,
                 background_error=None)
    # 内容已变，旧的下载凭证与确认状态不能再指向新文件
    state.pop('download', None)
    state.pop('revision_pending', None)
    return state
