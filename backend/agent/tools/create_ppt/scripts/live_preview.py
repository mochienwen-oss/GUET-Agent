"""Read actual authoring files for the workspace, independently of PPTX delivery."""
import base64
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

from .subgraph import _workdir

SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
RASTER_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
STYLE_PROPERTIES = set(('fill stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin '
    'fill-opacity stroke-opacity vector-effect font-family font-size font-weight font-style '
    'text-anchor letter-spacing text-decoration opacity stop-color stop-opacity flood-color '
    'flood-opacity x y width height rx ry cx cy r shape-rendering').split())


def _raster(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp'
    raise ValueError('页面引用的图片不是支持的光栅格式')


def _project(state):
    value = (state.get('master') or {}).get('project')
    if not value:
        return None
    root = Path(_workdir()).resolve() / state['task_id']
    folder = Path(value).resolve()
    return folder if folder.is_relative_to(root) and folder.is_dir() else None


def _text(path, limit=MAX_SVG_BYTES):
    if not path.is_file() or path.stat().st_size > limit:
        return ''
    return path.read_text(encoding='utf-8-sig')


def _tree(path):
    text = _text(path)
    if not text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('页面尚未保存完整，或使用了不支持的外部文档声明')
    tree = ET.fromstring(text)
    if tree.tag not in {'svg', '{%s}svg' % SVG_NS}:
        raise ValueError('页面不是SVG文档')
    return tree


def _outline(project):
    path = (project / 'outline.md').resolve()
    if path.is_relative_to(project):
        text = _text(path, 200000)
        if text.strip():
            return text
    path = (project / 'design_spec.md').resolve()
    if not path.is_relative_to(project):
        return ''
    text = _text(path, 500000)
    match = re.search(r'(?m)^##\s+IX[.\s].*$', text)
    if not match:
        return ''
    following = re.search(r'(?m)^##\s+', text[match.end():])
    end = match.end() + following.start() if following else len(text)
    return text[match.start():end].strip()


def snapshot(state):
    result = {'live_pages': [], 'live_outline': ''}
    project = _project(state)
    if project is None:
        return result
    try:
        result['live_outline'] = _outline(project)
        # Once published, the canvas uses previews of the actual exported PPTX.
        if state.get('artifacts') and (state.get('master') or {}).get('revision') == state.get('revision'):
            return result
        folder = (project / 'svg_output').resolve()
        if not folder.is_relative_to(project) or not folder.is_dir():
            return result
        manifest_path = (project / 'page_manifest.json').resolve()
        manifest = {}
        if manifest_path.is_relative_to(project):
            try:
                manifest = {page['file']: page for page in json.loads(_text(manifest_path) or '{}').get('pages', [])}
            except (ValueError, KeyError, TypeError):
                pass  # A tool may currently be writing the manifest.
        for path in sorted(folder.glob('*.svg')):
            if not path.resolve().is_relative_to(folder):
                continue
            try:
                tree = _tree(path)
                stat = path.stat()
            except (OSError, UnicodeError, ValueError, ET.ParseError):
                continue  # Incomplete pages appear on the next saved version.
            title = manifest.get(path.name, {}).get('title')
            if not title:
                title = next((''.join(node.itertext()).strip() for node in tree.iter()
                    if node.tag.rsplit('}', 1)[-1] in {'title', 'text'} and ''.join(node.itertext()).strip()), path.stem)
            version = '%s-%s' % (stat.st_mtime_ns, stat.st_size)
            result['live_pages'].append({'slide_id': 'live:' + path.name, 'file': path.name,
                'title': str(title)[:200], 'version': version,
                'preview_url': '/api/ppt/task/%s/live-preview/%s?version=%s' % (
                    state['task_id'], quote(path.name, safe=''), version)})
    except (OSError, UnicodeError, ValueError):
        pass  # Concurrent writes must not prevent restoring the task conversation.
    return result


def svg(state, filename):
    project = _project(state)
    if project is None or Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('制作中的页面不存在')
    folder = (project / 'svg_output').resolve()
    path = (folder / filename).resolve()
    if not folder.is_relative_to(project) or path.parent != folder or path.suffix.lower() != '.svg':
        raise ValueError('制作中的页面不属于当前任务')
    tree = _tree(path)
    inline_bytes = 0
    # Generated SVG is an image preview, never an executable document. These
    # elements are also outside the installed Skill's generated SVG surface.
    forbidden = {'script', 'foreignobject', 'style', 'link', 'iframe', 'object', 'embed',
                 'audio', 'video', 'animate', 'animatetransform', 'animatemotion', 'set'}
    for parent in list(tree.iter()):
        for child in list(parent):
            if child.tag.rsplit('}', 1)[-1].lower() in forbidden:
                parent.remove(child)
    for node in tree.iter():
        for key, value in list(node.attrib.items()):
            local = key.rsplit('}', 1)[-1].lower()
            if local.startswith('on') or local in {'src', 'base'}:
                del node.attrib[key]
            elif local == 'style':
                declarations = []
                if not re.search(r'[\\@<>]', value):
                    for declaration in value.split(';'):
                        name, separator, paint = declaration.partition(':')
                        if separator and name.strip().lower() in STYLE_PROPERTIES:
                            declarations.append(name.strip() + ':' + paint)
                value = ';'.join(declarations)
                if re.search(r'url\s*\(', re.sub(r'url\(\s*[\'"]?#[\w.:-]+[\'"]?\s*\)', '', value, flags=re.I), re.I):
                    value = ''
                node.set(key, value)
            elif local == 'href':
                if value.startswith('#'):
                    continue
                kind = node.tag.rsplit('}', 1)[-1].lower()
                if kind != 'image':
                    del node.attrib[key]
                    continue
                if value.startswith('data:'):
                    if not re.match(r'^data:image/(?:png|jpeg|webp);base64,', value, re.I):
                        raise ValueError('制作预览仅支持任务内光栅图片')
                    image_bytes = base64.b64decode(value.split(',', 1)[1], validate=True)
                    _raster(image_bytes)
                    inline_bytes += len(image_bytes)
                    if inline_bytes > MAX_IMAGE_BYTES:
                        raise ValueError('当前页图片超过20MiB，暂未提供制作预览')
                    continue
                address = urlsplit(value)
                image = (path.parent / unquote(address.path)).resolve()
                if address.scheme or address.netloc or not image.is_relative_to(project) or image.suffix.lower() not in RASTER_TYPES:
                    raise ValueError('页面图片须先保存到当前任务中')
                image_bytes = image.read_bytes()
                inline_bytes += len(image_bytes)
                if inline_bytes > MAX_IMAGE_BYTES:
                    raise ValueError('当前页图片超过20MiB，暂未提供制作预览')
                node.set(key, 'data:%s;base64,%s' % (
                    _raster(image_bytes), base64.b64encode(image_bytes).decode('ascii')))
            elif re.search(r'url\s*\(', value, re.I):
                # Only local gradient, filter, mask and marker references survive.
                if re.sub(r'url\(\s*[\'"]?#[\w.:-]+[\'"]?\s*\)', '', value, flags=re.I).lower().find('url') >= 0:
                    del node.attrib[key]
            elif local in {'fill', 'stroke', 'filter', 'clip-path', 'mask', 'marker-start', 'marker-end'} and '\\' in value:
                del node.attrib[key]
    return ET.tostring(tree, encoding='utf-8', xml_declaration=True)
