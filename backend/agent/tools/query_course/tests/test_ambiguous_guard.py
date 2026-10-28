# -*- coding: utf-8 -*-
"""Task 4.2：结构化工具路径的歧义守卫。

原来的 `ms[0]` / `hits[0]` / `matches[0]` 会在命中多个候选时**静默取第一项** ——
返回一个「看似成功、实则选错对象」的结果，机械门不会报警。本轮改成：
0 个 → 现有 not_found；**恰 1 个** → 照旧；**多个** → 判歧义，**绝不取第一项**。
"""
import re
from pathlib import Path

import pytest

from agent.runtime import registry
from agent.tools.query_course.scripts import repository


BACKEND = Path(__file__).resolve().parents[4]
PRODUCTION_FILES = [
    BACKEND / "agent" / "tools" / "query_course" / "scripts" / "repository.py",
    BACKEND / "agent" / "tools" / "query_course" / "tool.py",
    BACKEND / "agent" / "tools" / "compare_course" / "tool.py",
    BACKEND / "agent" / "tools" / "list_courses" / "scripts" / "query.py",
    BACKEND / "agent" / "tools" / "query_major_requirements" / "scripts" / "query.py",
]

#: 真实存在的歧义样例（实测取自当前 plan_db）
AMBIGUOUS_MAJOR = "工程"          # 子串命中 24 个专业
DUPLICATE_COURSE = "大学英语"      # 每个专业里前缀命中 4 门
#: 三个**同一专业内同名课程**的真实样例
DUPLICATE_NAMES = [("汉语国际教育", "跨文化交际"),
                   ("国际经贸规则", "国际投资法"),
                   ("表演", "形体塑造")]
UNIQUE_MAJOR = "计算机科学与技术"
UNIQUE_COURSE = "数据库系统原理A"

CARD_KEYS = {"major", "college", "duration", "degree", "total_credits",
             "module_credits", "course", "indicators", "match", "alternatives",
             "sources"}


# ================================================================ 0 / 1 / 多

def test_zero_match_keeps_the_existing_not_found_behaviour():
    assert repository.query_course("不存在的专业", "随便")["error"] == "major_not_found"
    assert repository.query_course(UNIQUE_MAJOR, "不存在的课")["error"] == "course_not_found"
    assert registry.call("query_course", {"major": "不存在的专业",
                                          "course": "随便"}).status == "no_evidence"


def test_unique_match_output_is_unchanged():
    card = repository.query_course(UNIQUE_MAJOR, UNIQUE_COURSE)
    assert set(card) == CARD_KEYS
    assert card["match"] == "exact"
    assert card["alternatives"] == []
    assert card["course"]["name"] == UNIQUE_COURSE
    result = registry.call("query_course", {"major": UNIQUE_MAJOR,
                                            "course": UNIQUE_COURSE})
    assert result.status == "success"
    assert result.data == card


def test_ambiguous_major_is_refused_not_silently_picked():
    with pytest.raises(repository.AmbiguousLookupError) as excinfo:
        repository.query_course(AMBIGUOUS_MAJOR, UNIQUE_COURSE)
    error = excinfo.value
    assert error.field == "major"
    assert error.query == AMBIGUOUS_MAJOR
    assert len(error.candidates) > 1
    assert AMBIGUOUS_MAJOR not in error.candidates


def test_ambiguous_course_is_refused_not_silently_picked():
    with pytest.raises(repository.AmbiguousLookupError) as excinfo:
        repository.query_course(UNIQUE_MAJOR, DUPLICATE_COURSE)
    error = excinfo.value
    assert error.field == "course"
    assert len(error.candidates) > 1
    assert all(name.startswith(DUPLICATE_COURSE) for name in error.candidates)


@pytest.mark.parametrize("major,name", DUPLICATE_NAMES)
def test_same_major_duplicate_course_name_is_refused(major, name):
    """同一专业内**同名多实例** —— 不许静默挑一条。"""
    with pytest.raises(repository.AmbiguousLookupError) as excinfo:
        repository.query_course(major, name)
    assert excinfo.value.field == "course"
    assert excinfo.value.candidates == [name, name]


# ================================================================ resolve_major

def test_resolve_major_refuses_ambiguity():
    from agent.tools.query_course import tool

    with pytest.raises(repository.AmbiguousLookupError):
        tool.resolve_major(AMBIGUOUS_MAJOR)


def test_resolve_major_unique_still_returns_the_record():
    from agent.tools.query_course import tool

    record = tool.resolve_major(UNIQUE_MAJOR)
    assert record.get("error") is None
    assert record.get("major") == UNIQUE_MAJOR


def test_sibling_tools_refuse_ambiguity_instead_of_picking_one():
    for name, args in (("list_courses", {"major": AMBIGUOUS_MAJOR}),
                       ("query_major_requirements", {"major": AMBIGUOUS_MAJOR})):
        result = registry.call(name, args)
        assert result.status == "tool_failed", name
        assert result.ok is False, name
        assert result.status not in ("no_evidence", "irrelevant_result"), name


# ================================================================ compare_course

def test_compare_course_does_not_execute_any_part_of_an_ambiguous_call():
    from agent.tools.compare_course.tool import compare_course_result

    with pytest.raises(repository.AmbiguousLookupError):
        repository.compare_course(DUPLICATE_COURSE, [UNIQUE_MAJOR, "环境工程"])
    with pytest.raises(repository.AmbiguousLookupError):
        compare_course_result(DUPLICATE_COURSE, [UNIQUE_MAJOR, "环境工程"])


def test_compare_course_ambiguous_major_also_aborts_wholesale():
    from agent.tools.compare_course.tool import compare_course_result

    with pytest.raises(repository.AmbiguousLookupError):
        compare_course_result(UNIQUE_COURSE, [UNIQUE_MAJOR, AMBIGUOUS_MAJOR])


def test_compare_course_unique_call_is_untouched():
    compared = repository.compare_course("大学英语1", ["光电信息科学与工程", "环境工程"])
    assert len(compared["rows"]) == 2
    assert all(row["found"] for row in compared["rows"])
    result = registry.call("compare_course", {"course": "大学英语1",
                                              "majors": ["光电信息科学与工程",
                                                         "环境工程"]})
    assert result.status == "success"


# ================================================================ 不伪装状态

def test_ambiguity_becomes_tool_failed_not_no_evidence():
    result = registry.call("query_course", {"major": AMBIGUOUS_MAJOR,
                                            "course": UNIQUE_COURSE})
    assert result.status == "tool_failed"
    assert result.ok is False
    assert result.error.code == "tool_failed"
    assert result.status not in ("no_evidence", "irrelevant_result")
    assert "歧义" in result.error.detail or "多个候选" in result.error.detail


def test_ambiguity_error_is_not_a_tool_contract_type():
    import ast

    assert issubclass(repository.AmbiguousLookupError, LookupError)
    assert not hasattr(repository.AmbiguousLookupError("major", "x", ["a", "b"]), "status")
    source = (BACKEND / "agent" / "tools" / "query_course" / "scripts" /
              "repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imports = {alias.name for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))
               for alias in node.names}
    # 歧义**不构造**任何工具契约对象，也不引入 agent.core（那是 Registry 的事）
    assert not names & {"ToolResult", "ToolStatus", "ToolError", "render_observation"}
    assert not {module for module in imports if module.startswith("agent.")}


# ================================================================ 静态：首项选择清零

_FIRST_ITEM_NAMES = ("ms", "hits", "matches")
_GUARD_FUNCTIONS = ("pick_major", "pick_course")


def _unguarded_first_item(path):
    """`ms[0]` / `hits[0]` / `matches[0]` —— **只允许**出现在唯一性守卫函数内部。"""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    guards = {node.name: node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name in _GUARD_FUNCTIONS}
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in _FIRST_ITEM_NAMES
                and isinstance(node.slice, ast.Constant) and node.slice.value == 0):
            continue
        if not any(node in ast.walk(guard) for guard in guards.values()):
            offenders.append("%s[0] @line %d" % (node.value.id, node.lineno))
    return offenders, guards


@pytest.mark.parametrize("path", PRODUCTION_FILES, ids=lambda p: p.name)
def test_no_first_item_selection_remains(path):
    """生产代码里**不再**出现未加守卫的 `ms[0]` / `hits[0]` / `matches[0]`。"""
    offenders, _guards = _unguarded_first_item(path)
    assert offenders == [], "%s 仍有未加守卫的首项选择：%s" % (path.name, offenders)


@pytest.mark.parametrize("path", PRODUCTION_FILES, ids=lambda p: p.name)
def test_every_first_item_access_sits_behind_a_uniqueness_guard(path):
    """允许存在的那两处必须**先判 `len(...) == 1`**，否则仍是静默取首项。"""
    import ast

    _offenders, guards = _unguarded_first_item(path)
    for name, guard in guards.items():
        body = ast.unparse(guard)
        assert "len(" in body and "== 1" in body, "%s 的守卫没有唯一性判断" % name
        assert "raise" in body and "Ambiguous" in body, "%s 的守卫没有抛歧义" % name


def test_repository_only_returns_a_match_through_the_guard():
    source = (BACKEND / "agent" / "tools" / "query_course" / "scripts" /
              "repository.py").read_text(encoding="utf-8")
    assert "def pick_major(" in source and "def pick_course(" in source
    assert "AmbiguousLookupError(" in source