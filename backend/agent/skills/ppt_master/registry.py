# -*- coding: utf-8 -*-
"""发现并校验 ``backend/agent/skills/*/manifest.json``。

设计取舍：

★ 只用 manifest，不靠文件名猜。新增 skill 落地一个目录 + 一份 manifest 即可。
★ manifest 内的路径必须落在该 skill 目录内部，防止误配到仓库别处。
★ 发现结果会缓存，``refresh=True`` 强制重读（升级 skill 后无需重启进程）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: 同级 skill 的父目录，即 backend/agent/skills/。
SKILLS_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = 'manifest.json'
SCHEMA = 'agent-skill/1'

_CACHE: dict[str, 'Skill'] | None = None


@dataclass(frozen=True)
class Skill:
    """一个已安装 skill 的只读视图；路径都已解析为绝对路径。"""

    id: str
    name: str
    version: str
    folder: Path
    entry: Path
    skill_dir: Path
    license: str = ''
    source: dict = field(default_factory=dict)
    capabilities: tuple = ()
    slug: str = ''
    description: str = ''

    def read_entry(self) -> str:
        return self.entry.read_text(encoding='utf-8-sig')


def _frontmatter(text: str) -> dict:
    """从 SKILL.md 的 YAML 头取 name/description；解析不了就返回空。

    manifest 负责"装了什么"，frontmatter 负责"Skill 自称是谁"，两者都不强求。
    """
    if not text.startswith('---'):
        return {}
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}
    block = parts[1]
    found = {}
    slug = re.search(r'(?m)^name:\s*(.+)$', block)
    if slug:
        found['slug'] = slug.group(1).strip()
    description = re.search(r'(?ms)^description:\s*>\s*\n((?:[ \t]+[^\n]*\n?)+)', block)
    if description:
        found['description'] = ' '.join(description.group(1).split())
    return found


def _inside(folder: Path, raw, key: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('skill %s 的 %s 未填写' % (folder.name, key))
    target = (folder / raw).resolve()
    if not target.is_relative_to(folder.resolve()):
        raise ValueError('skill %s 的 %s 指向目录外部：%s' % (folder.name, key, raw))
    return target


def _load(folder: Path) -> Skill:
    path = folder / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as error:
        raise ValueError('skill %s 的 manifest 不是合法 JSON：%s' % (folder.name, error)) from None
    if not isinstance(manifest, dict):
        raise ValueError('skill %s 的 manifest 必须是对象' % folder.name)
    if manifest.get('schema') != SCHEMA:
        raise ValueError('skill %s 的 manifest schema 应为 %s' % (folder.name, SCHEMA))
    skill_id = str(manifest.get('id') or '').strip()
    if not skill_id:
        raise ValueError('skill %s 缺少 id' % folder.name)
    entry = _inside(folder, manifest.get('entry'), 'entry')
    directory = _inside(folder, manifest.get('skill_dir') or '.', 'skill_dir')
    if not entry.is_file():
        raise ValueError('skill %s 的入口不存在：%s' % (skill_id, entry))
    if not directory.is_dir():
        raise ValueError('skill %s 的内容目录不存在：%s' % (skill_id, directory))
    source = manifest.get('source') if isinstance(manifest.get('source'), dict) else {}
    try:
        meta = _frontmatter(entry.read_text(encoding='utf-8-sig'))
    except OSError:
        meta = {}
    return Skill(id=skill_id, name=str(manifest.get('name') or skill_id),
                 version=str(manifest.get('version') or ''), folder=folder.resolve(),
                 entry=entry, skill_dir=directory, license=str(manifest.get('license') or ''),
                 source=source, capabilities=tuple(manifest.get('capabilities') or ()),
                 slug=meta.get('slug', ''), description=meta.get('description', ''))


def discover(*, refresh: bool = False) -> dict[str, Skill]:
    """返回 id -> Skill；同一 id 重复会被拒绝（避免装了同名两份）。"""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    found: dict[str, Skill] = {}
    for folder in sorted(SKILLS_ROOT.iterdir()) if SKILLS_ROOT.is_dir() else []:
        if not folder.is_dir() or not (folder / MANIFEST).is_file():
            continue
        skill = _load(folder)
        if skill.id in found:
            raise ValueError('skill id 重复：%s（%s 与 %s）'
                             % (skill.id, found[skill.id].folder.name, folder.name))
        found[skill.id] = skill
    _CACHE = found
    return found


def get(skill_id: str = 'ppt_master') -> Skill:
    skills = discover()
    if skill_id not in skills:
        raise ValueError('未安装 skill：%s（已安装：%s）'
                         % (skill_id, '、'.join(sorted(skills)) or '无'))
    return skills[skill_id]


def skill_dir(skill_id: str = 'ppt_master') -> Path:
    return get(skill_id).skill_dir


def verify(skill_id: str = 'ppt_master') -> list[str]:
    """返回问题清单；空列表表示可用。用于启动自检，不抛异常。"""
    try:
        skill = get(skill_id)
    except (ValueError, OSError) as error:
        return [str(error)]
    problems = []
    if not skill.entry.is_file():
        problems.append('入口缺失：%s' % skill.entry)
    if not (skill.skill_dir / 'SKILL.md').is_file():
        problems.append('内容目录缺少 SKILL.md：%s' % skill.skill_dir)
    if not (skill.skill_dir / 'scripts').is_dir():
        problems.append('内容目录缺少 scripts：%s' % skill.skill_dir)
    return problems
