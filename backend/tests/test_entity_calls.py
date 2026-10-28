# -*- coding: utf-8 -*-
"""Task 4.1：结构化工具调用验证与参数规范化。

纯函数：不接运行图、不执行工具、不碰 Registry / budget / duplicate 流程。
"""
import json
from copy import deepcopy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from entities import entity_catalog as EC  # noqa: E402
from entities import entity_aliases as EA  # noqa: E402
from entities import entity_calls as ECALL  # noqa: E402
from entities import entity_resolver as ER  # noqa: E402

FORMAL = ROOT / "backend" / "data"
MODULE = ROOT / "backend" / "entities/entity_calls.py"


# ------------------------------------------------------------------ 夹具

@pytest.fixture(scope="session")
def catalog():
    return EC.load(FORMAL / "entity_catalog.json",
                   FORMAL / "entity_catalog.manifest.json")


@pytest.fixture(scope="session")
def facts(catalog):
    courses = catalog["entities"]["course"]
    majors = catalog["entities"]["major"]
    course_names = {c["canonical_name"] for c in courses}
    major_names = {m["canonical_name"] for m in majors}
    grouped = {}
    for course in courses:
        grouped.setdefault(course["canonical_name"], []).append(course)
    collisions = {}
    for name in grouped:
        collisions.setdefault(ER._join_key(name), set()).add(name)
    flat = {name: len(names) for names in collisions.values() for name in names}
    major = next(m for m in majors if m["canonical_name"] not in course_names)
    other = next(m for m in majors
                 if m["canonical_name"] not in course_names
                 and m["canonical_name"] != major["canonical_name"])
    return {
        "catalog": catalog, "courses": courses, "majors": majors,
        "course_names": course_names, "major_names": major_names,
        "grouped": grouped, "major": major, "other": other,
        "one_course": next(c for c in courses
                           if len(grouped[c["canonical_name"]]) == 1
                           and c["canonical_name"] not in major_names
                           and flat[c["canonical_name"]] == 1),
        "many_name": next(n for n, rows in sorted(grouped.items())
                          if 2 <= len(rows) <= 4 and n not in major_names
                          and flat[n] == 1),
        "cross_name": "软件工程",
    }


@pytest.fixture(scope="session")
def resolver(catalog):
    return ER.Resolver(catalog,
                       catalog_sha256=EC.sha256_file(FORMAL / "entity_catalog.json"),
                       aliases_sha256=EC.sha256_file(FORMAL / EA.ALIASES_NAME))


def _check(tool, args, query, resolver, context=None):
    return ECALL.validate_call(tool, args, resolver.resolve(query, context))


def _binding(validation, arg):
    return next((b for b in validation.bindings if b.arg == arg), None)


# ================================================================ A 基本

def test_unknown_tool_is_rejected(resolver, facts):
    result = _check("delete_everything", {}, facts["major"]["canonical_name"], resolver)
    assert result.decision == "reject"
    assert result.reason == "unknown_tool"


def test_missing_required_arg_is_rejected(resolver, facts):
    name = facts["major"]["canonical_name"]
    result = _check("query_course", {"major": name}, name, resolver)
    assert result.decision == "reject"
    assert "course" in result.reason


def test_extra_arg_is_rejected(resolver, facts):
    name = facts["major"]["canonical_name"]
    result = _check("query_major_requirements", {"major": name, "semester": "3"},
                    name, resolver)
    assert result.decision == "reject"
    assert "semester" in result.reason


@pytest.mark.parametrize("tool,args", [
    ("search_regulations", {"query": "转专业怎么弄", "keywords": ["转专业"]}),
    ("query_empty_classroom", {}),
    ("create_ppt", {"goal": "讲培养方案", "audience": "新生"}),
])
def test_opaque_tools_pass_through_untouched(resolver, tool, args):
    result = _check(tool, args, "随便问点什么", resolver)
    assert result.decision == "allow"
    assert result.canonical_args == args
    assert result.raw_args == args
    assert result.bindings == ()
    assert result.reason == "passthrough"


def test_raw_and_canonical_args_are_both_kept(resolver, facts):
    major = facts["major"]["canonical_name"]
    course = facts["one_course"]["canonical_name"]
    raw = {"major": major, "course": course}
    result = _check("query_course", raw, "%s的%s" % (major, course), resolver)
    assert result.decision == "allow"
    assert result.raw_args == raw
    assert result.canonical_args == raw
    assert result.raw_args is not raw


def test_refused_calls_expose_raw_but_no_canonical_args(resolver, facts):
    """拒绝的调用**不产出** canonical_args —— 免得下游误当可执行。"""
    result = _check("query_course", {"major": "不存在的专业", "course": "不存在"},
                    facts["major"]["canonical_name"], resolver)
    assert result.decision != "allow"
    assert result.raw_args == {"major": "不存在的专业", "course": "不存在"}
    assert result.canonical_args == {}
    assert result.bindings == ()


def test_decision_is_never_a_tool_status(resolver, facts):
    result = _check("query_course", {"major": facts["major"]["canonical_name"],
                                     "course": "x"}, facts["major"]["canonical_name"],
                    resolver)
    assert result.decision in ECALL.DECISIONS
    assert not hasattr(result, "status")
    assert not hasattr(result, "ok")
    assert result.decision != "tool_failed"


def test_two_validations_are_byte_identical(resolver, facts):
    query = "%s的%s" % (facts["major"]["canonical_name"],
                        facts["one_course"]["canonical_name"])
    args = {"major": facts["major"]["canonical_name"],
            "course": facts["one_course"]["canonical_name"]}
    first = ECALL.dump_validation(_check("query_course", args, query, resolver))
    second = ECALL.dump_validation(_check("query_course", args, query, resolver))
    assert first == second


# ================================================================ B query_course

def test_query_course_binds_both_args(resolver, facts):
    major, course = facts["major"], facts["one_course"]
    query = "%s专业里的%s" % (major["canonical_name"], course["canonical_name"])
    result = _check("query_course", {"major": major["canonical_name"],
                                     "course": course["canonical_name"]}, query, resolver)
    assert result.decision == "allow"
    assert _binding(result, "major").entity_id == major["entity_id"]
    assert _binding(result, "major").mention_id.startswith("m:")
    assert _binding(result, "course").entity_id == course["entity_id"]
    assert _binding(result, "course").instance_ids == (course["entity_id"],)
    assert json.dumps(result.canonical_args) == json.dumps(
        {"major": major["canonical_name"], "course": course["canonical_name"]})


def test_kind_name_arg_keeps_entity_id_null(resolver, facts):
    major, name = facts["major"], facts["many_name"]
    query = "%s专业里的%s" % (major["canonical_name"], name)
    result = _check("query_course", {"major": major["canonical_name"], "course": name},
                    query, resolver)
    assert result.decision == "allow"
    binding = _binding(result, "course")
    assert binding.kind == "name" and binding.entity_id is None
    assert len(binding.instance_ids) == len(facts["grouped"][name])
    assert binding.canonical_name == name
    assert "course:" not in json.dumps(result.canonical_args)


def test_cross_type_tie_is_narrowed_by_arg_type(resolver, facts):
    major, cross = facts["major"], facts["cross_name"]
    query = "%s 和 %s" % (major["canonical_name"], cross)
    result = _check("query_course", {"major": major["canonical_name"], "course": cross},
                    query, resolver)
    assert result.decision == "allow"
    binding = _binding(result, "course")
    assert binding.narrowed_by == "arg_type"
    assert binding.entity_type == "course"
    assert binding.narrowed_from == ("course", "major")
    assert binding.canonical_name == cross


def test_cross_type_tie_narrowed_to_major_on_the_other_arg(resolver, facts):
    course, cross = facts["one_course"]["canonical_name"], facts["cross_name"]
    query = "%s 和 %s 的区别" % (cross, course)
    result = _check("query_course", {"major": cross, "course": course}, query, resolver)
    assert result.decision == "allow"
    binding = _binding(result, "major")
    assert binding.narrowed_by == "arg_type"
    assert binding.entity_type == "major"
    assert binding.canonical_name == cross
    assert binding.instance_ids == (binding.entity_id,)
    assert binding.entity_id in {m["entity_id"] for m in facts["majors"]}
    assert binding.kind == "instance"


def test_narrowing_does_not_mutate_the_mention(resolver, facts):
    cross = facts["cross_name"]
    envelope = resolver.resolve(cross)
    ECALL.validate_call("query_course", {"major": cross, "course": "x"}, envelope)
    mention = envelope.mentions[0]
    assert mention.selected is None
    assert mention.resolution_status == "ambiguous"
    assert mention.entity_type is None


@pytest.mark.parametrize("weak", ["高数", "大物", "计科", "软工"])
def test_weak_evidence_is_not_upgraded_by_arg_type(resolver, weak):
    """rule 6：T4–T7 **不得**因参数类型收窄而升级为 selected。"""
    course = "数据结构"
    query = "%s 和 %s" % (weak, course)
    result = _check("query_course", {"major": weak, "course": course}, query, resolver)
    assert result.decision == "clarify"
    assert result.clarification["arg"] == "major"
    assert result.clarification["raw_text"] == weak
    assert result.bindings == ()


def test_arg_missing_from_the_query_is_rejected(resolver, facts):
    major, course = facts["major"], facts["one_course"]
    query = "%s里的%s" % (major["canonical_name"], course["canonical_name"])
    result = _check("query_course", {"major": "软件工程", "course": course["canonical_name"]},
                    query, resolver)
    assert result.decision == "reject"
    assert result.reason == "arg_unmapped:major"


def test_arg_type_mismatch_is_a_clarification(resolver, facts):
    major = facts["major"]["canonical_name"]
    result = _check("query_course", {"major": major, "course": major}, major, resolver)
    assert result.decision == "clarify"
    assert result.clarification["arg"] == "course"
    assert result.clarification["resolution_status"] == "exact_canonical"


def test_unresolved_mention_is_a_clarification_not_a_fake_status(resolver, facts):
    """未决 → **明确澄清**，不得伪装成 no_evidence / irrelevant_result / tool_failed。"""
    result = _check("query_major_requirements", {"major": "计科"}, "计科的课", resolver)
    assert result.decision == "clarify"
    assert result.reason == "unresolved:major"
    assert result.clarification["resolution_status"] == "ambiguous"
    assert result.clarification["raw_text"] == "计科"
    assert result.clarification["candidate_names"]
    assert result.canonical_args == {}
    assert "no_evidence" not in json.dumps(ECALL.to_dict(result))
    assert "tool_failed" not in json.dumps(ECALL.to_dict(result))


def test_same_type_tie_survives_narrowing(catalog):
    """收窄只在**跨类型**并列时有用 —— 同类型并列收窄完仍然不是一个。

    当前目录已无旧的空格碰撞。仅在夹具副本中构造同类型碰撞，仍走真实
    Resolver和validate_call；不再让一个已消失的样本使全部基础校验无法初始化。
    """
    synthetic = deepcopy(catalog)
    source = synthetic['entities']['course'][0]
    first = source['canonical_name']
    second = first[0] + ' ' + first[1:]
    synthetic['entities']['course'].append({**source,
        'canonical_name': second, 'entity_id': source['entity_id'] + ':fixture-collision'})
    resolver = ER.Resolver(synthetic, catalog_sha256='a' * 64, aliases_sha256='b' * 64)
    result = _check("query_major_requirements", {"major": first}, first, resolver)
    assert result.decision == "clarify"
    assert result.clarification["candidate_entity_types"] == ["course"]
    assert len(result.clarification["candidate_names"]) >= 2
    assert first in result.clarification["candidate_names"]


# ================================================================ B2 query_course
# 规范实体存在、但目标专业作用域内不存在：allow → Registry no_evidence。
# 这里不是放宽唯一性门：major 唯一、course 是规范实体、其他工具仍按原语义澄清。

ABSENT_MAJOR = "交通工程"
ABSENT_COURSE = "3D打印技术及实践"


def _check_production(tool, args, query, resolver, context=None):
    """走 `resolve_question`（两趟，含 query_explicit 专业上下文）。"""
    return ECALL.validate_call(tool, args,
                               ER.resolve_question(resolver, query, context))


def test_query_course_scope_absent_is_allowed_with_canonical_args(resolver):
    query = "%s专业的《%s》是多少学分？" % (ABSENT_MAJOR, ABSENT_COURSE)
    result = _check_production(
        "query_course", {"major": ABSENT_MAJOR, "course": ABSENT_COURSE},
        query, resolver)
    assert result.decision == "allow"
    assert result.reason == "canonical_entity_absent_in_scope"
    assert result.canonical_args == {"major": ABSENT_MAJOR,
                                     "course": ABSENT_COURSE}
    binding = _binding(result, "course")
    assert binding.entity_type == "course"
    assert binding.canonical_name == ABSENT_COURSE
    assert binding.narrowed_by == "scope_absent"
    assert binding.entity_id is None
    assert binding.instance_ids


def test_query_course_scope_absent_registry_returns_no_evidence(resolver):
    from agent.runtime import registry

    query = "%s专业的《%s》是多少学分？" % (ABSENT_MAJOR, ABSENT_COURSE)
    result = _check_production(
        "query_course", {"major": ABSENT_MAJOR, "course": ABSENT_COURSE},
        query, resolver)
    assert result.decision == "allow"
    observed = registry.call("query_course", dict(result.canonical_args))
    assert observed.status == "no_evidence"


def test_query_course_unknown_course_is_still_not_allowed(resolver):
    query = "%s专业的《星际航行技术导论》是多少学分？" % ABSENT_MAJOR
    result = _check_production(
        "query_course", {"major": ABSENT_MAJOR, "course": "星际航行技术导论"},
        query, resolver)
    assert result.decision != "allow"
    assert result.canonical_args == {}


def test_query_course_one_to_many_alias_still_clarifies():
    # ★ 一对多别名来自 reviewed alias 包；用生产 resolver，确保测的是真实别名契约。
    resolver = ER.build_production_resolver()
    query = "%s专业的《高数》是多少学分？" % ABSENT_MAJOR
    result = _check_production(
        "query_course", {"major": ABSENT_MAJOR, "course": "高数"}, query, resolver)
    assert result.decision == "clarify"
    assert result.canonical_args == {}


def test_compare_course_scope_absent_is_not_allowed(resolver, facts):
    """新状态只对 query_course 的 course 槽位放行；compare_course 原语义不变。"""
    query = ("《%s》在%s和土木工程专业分别是多少学分？"
             % (ABSENT_COURSE, ABSENT_MAJOR))
    major_row = next(m for m in facts["majors"]
                     if m["canonical_name"] == ABSENT_MAJOR)
    context = ER.ResolutionContext(major_code=major_row["major_code"])
    result = _check_production(
        "compare_course",
        {"course": ABSENT_COURSE, "majors": [ABSENT_MAJOR, "土木工程"]},
        query, resolver, context)
    assert result.decision != "allow"
    assert result.canonical_args == {}


# ================================================================ C list_courses

def test_list_courses_passes_filters_through(resolver, facts):
    major = facts["major"]["canonical_name"]
    args = {"major": major, "semester": "3", "core_only": True}
    result = _check("list_courses", args, major, resolver)
    assert result.decision == "allow"
    assert result.canonical_args == args
    assert _binding(result, "major").entity_id == facts["major"]["entity_id"]
    assert len(result.bindings) == 1


def test_list_courses_unresolved_major_clarifies(resolver, facts):
    result = _check("list_courses", {"major": "计科"}, "计科的课有哪些", resolver)
    assert result.decision == "clarify"
    assert result.clarification["raw_text"] == "计科"


# ================================================================ D compare_course

def test_compare_course_keeps_the_requested_order(resolver, facts):
    first, second = facts["major"]["canonical_name"], facts["other"]["canonical_name"]
    course = facts["many_name"]
    query = "%s 和 %s 的%s" % (first, second, course)
    result = _check("compare_course",
                    {"course": course, "majors": [second, first]}, query, resolver)
    assert result.decision == "allow"
    assert result.canonical_args["majors"] == [second, first]
    majors = [b for b in result.bindings if b.arg == "majors"]
    assert [b.index for b in majors] == [0, 1]
    assert [b.entity_id for b in majors] == [facts["other"]["entity_id"],
                                             facts["major"]["entity_id"]]


def test_compare_course_is_all_or_nothing(resolver, facts):
    """rule 7：任一参数未决 → **整次调用**拒绝，不得部分执行。"""
    good = facts["major"]["canonical_name"]
    query = "%s 和 计科 的%s" % (good, facts["many_name"])
    result = _check("compare_course",
                    {"course": facts["many_name"], "majors": [good, "计科"]},
                    query, resolver)
    assert result.decision == "clarify"
    assert result.canonical_args == {}
    assert result.bindings == ()
    assert result.clarification["arg"] == "majors"
    assert result.clarification["index"] == 1


def test_compare_course_rejects_a_major_missing_from_the_query(resolver, facts):
    good = facts["major"]["canonical_name"]
    query = "%s 和 %s 的%s" % (good, facts["other"]["canonical_name"], facts["many_name"])
    result = _check("compare_course",
                    {"course": facts["many_name"], "majors": [good, "软件工程"]},
                    query, resolver)
    assert result.decision == "reject"
    assert result.reason == "arg_unmapped:majors[1]"


def test_compare_course_rejects_duplicate_majors(resolver, facts):
    good = facts["major"]["canonical_name"]
    query = "%s 和 %s 的%s" % (good, facts["other"]["canonical_name"], facts["many_name"])
    result = _check("compare_course",
                    {"course": facts["many_name"], "majors": [good, good]},
                    query, resolver)
    assert result.decision == "reject"
    assert result.reason == "duplicate_major"


def test_compare_course_rejects_two_spellings_of_the_same_major(resolver, facts):
    """两个**不同写法**绑到同一个专业 → 也是重复（按 canonical 判，不按原文判）。"""
    cross = facts["cross_name"]
    spaced = cross[:2] + " " + cross[2:]
    query = "%s 和 %s 的%s" % (cross, spaced, facts["many_name"])
    result = _check("compare_course",
                    {"course": facts["many_name"], "majors": [cross, spaced]},
                    query, resolver)
    assert result.decision == "reject"
    assert result.reason == "duplicate_major"


@pytest.mark.parametrize("majors", [[], "计算机科学与技术", [None], [1],
                                    ["计算机科学与技术"]])
def test_compare_course_rejects_a_bad_majors_argument(resolver, facts, majors):
    good = facts["major"]["canonical_name"]
    query = "%s 的%s" % (good, facts["many_name"])
    result = _check("compare_course", {"course": facts["many_name"], "majors": majors},
                    query, resolver)
    assert result.decision == "reject"
    assert result.reason.startswith("bad_arg:majors")


# ================================================================ E Registry spy

def test_validation_never_executes_anything(resolver, facts, monkeypatch):
    """本 Task **不执行工具** —— 无论通过还是拒绝，Registry 调用次数都是 0。"""
    import agent.runtime.registry as registry

    calls = []

    def spy(name, args=None):
        calls.append((name, args))
        raise AssertionError("验证层不得执行工具")

    monkeypatch.setattr(registry, "call", spy)
    major, course = facts["major"], facts["one_course"]
    query = "%s里的%s" % (major["canonical_name"], course["canonical_name"])
    _check("query_course", {"major": major["canonical_name"],
                            "course": course["canonical_name"]}, query, resolver)
    _check("query_course", {"major": "计科", "course": "高数"}, "计科的高数", resolver)
    _check("compare_course", {"course": facts["many_name"], "majors": []}, "x", resolver)
    assert calls == []


def test_validate_calls_preserves_order_and_count(resolver, facts):
    major = facts["major"]["canonical_name"]
    calls = [{"name": "query_major_requirements", "arguments": {"major": major}},
             {"name": "search_regulations", "arguments": {"query": "转专业"}},
             {"name": "nope", "arguments": {}}]
    results = ECALL.validate_calls(calls, resolver.resolve(major))
    assert [r.tool for r in results] == ["query_major_requirements",
                                         "search_regulations", "nope"]
    assert [r.decision for r in results] == ["allow", "allow", "reject"]


# ================================================================ F call key

def test_canonical_call_key_is_stable(resolver, facts):
    args = {"major": facts["major"]["canonical_name"], "course": facts["many_name"]}
    reordered = {"course": facts["many_name"], "major": facts["major"]["canonical_name"]}
    assert ECALL.canonical_call_key("query_course", args) == \
        ECALL.canonical_call_key("query_course", reordered)
    assert ECALL.canonical_call_key("query_course", args) != \
        ECALL.canonical_call_key("query_course", {"major": "别的", "course": "别的"})
    assert ECALL.canonical_call_key("query_course", args)[0] == "query_course"


def test_canonical_call_key_ignores_raw_spelling(resolver, facts):
    """同一实体不同写法 → 同一个 key（不会被当成两次调用）。"""
    major, course = facts["major"]["canonical_name"], facts["one_course"]["canonical_name"]
    query = "%s专业里的%s" % (major, course)
    spaced = major[:2] + " " + major[2:]
    first = ECALL.validate_call("query_course", {"major": major, "course": course},
                                resolver.resolve(query))
    second = ECALL.validate_call("query_course", {"major": spaced, "course": course},
                                 resolver.resolve(query))
    assert first.decision == second.decision == "allow"
    assert first.raw_args != second.raw_args
    assert ECALL.canonical_call_key("query_course", first.canonical_args) == \
        ECALL.canonical_call_key("query_course", second.canonical_args)


def test_canonical_call_key_is_reproducible(resolver, facts):
    args = {"major": facts["major"]["canonical_name"]}
    keys = {json.dumps(ECALL.canonical_call_key("list_courses", args))
            for _ in range(3)}
    assert len(keys) == 1


# ================================================================ G 静态边界

def _tree():
    import ast
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _imported():
    import ast
    found = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def test_calls_module_imports_only_pure_modules():
    # `entity_prior` 是多轮 prior-context 的纯数据模型（pydantic/re/typing），
    # 不接图、不接 Registry、不碰网络；与 entity_calls 同属纯验证层。
    allowed = {"__future__", "json", "dataclasses", "entities.entity_resolver",
               "entities.entity_prior", "agent.core.evidence"}
    assert _imported() <= allowed, _imported() - allowed


@pytest.mark.parametrize("forbidden", ["graph", "state", "registry", "repository",
                                       "llm", "qdrant", "requests", "httpx", "socket",
                                       "agent.runtime", "budgets", "tools"])
def test_calls_module_never_touches_runtime(forbidden):
    for module in _imported():
        assert forbidden not in module, module


def test_calls_module_returns_no_tool_contract():
    source = MODULE.read_text(encoding="utf-8")
    import ast
    names = {node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(_tree()) if isinstance(node, ast.Attribute)}
    assert not names & {"ToolResult", "ToolStatus", "ToolError", "render_observation"}
    assert ".replace(" not in source


def test_mapping_covers_every_registered_tool():
    """映射表必须覆盖**全部**已注册工具 —— 将来加工具而漏映射会被这条抓住。"""
    import agent.runtime.registry as registry

    covered = set(ECALL.STRUCTURED_ARGS) | set(ECALL.OPAQUE_TOOLS)
    assert covered == set(registry.REGISTRY), covered ^ set(registry.REGISTRY)
    assert list(registry.REGISTRY) == json.loads(
        (ROOT / "backend" / "tests" / "agent_platform" /
         "runtime_tool_names.json").read_text(encoding="utf-8"))


def test_seven_tool_schemas_are_untouched_by_this_task():
    """本 Task **未改**任何工具 schema：名字、顺序、参数块都取自 Registry 自身。"""
    import agent.runtime.registry as registry

    schemas = registry.schemas()
    assert [s["function"]["name"] for s in schemas] == list(registry.REGISTRY)
    for schema in schemas:
        assert schema["type"] == "function"
        assert "properties" in schema["function"]["parameters"]
    assert [s["function"]["name"] for s in registry.training_schemas()] == \
        list(registry.TRAINING_TOOL_NAMES)
