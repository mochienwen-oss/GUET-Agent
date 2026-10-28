# -*- coding: utf-8 -*-
"""PPT Master skill 模块。

目录即模块，没有额外层级::

    ppt_master/
      __init__.py      本文件，包标记与对外 API
      registry.py      发现与校验同级 skill
      conftest.py      把上游 scripts/tests 排除在 pytest 之外
      manifest.json    身份、版本、入口、来源
      SKILL.md scripts/ references/ templates/ workflows/   ← 上游原样内容

宿主用法::

    from agent.skills.ppt_master import ROOT, get, skill_dir, verify
"""
from pathlib import Path

from .registry import SKILLS_ROOT, Skill, discover, get, skill_dir, verify

#: 本 skill 的根目录，等价于 skill_dir('ppt_master')。
ROOT = Path(__file__).resolve().parent

__all__ = ['ROOT', 'SKILLS_ROOT', 'Skill', 'discover', 'get', 'skill_dir', 'verify']
