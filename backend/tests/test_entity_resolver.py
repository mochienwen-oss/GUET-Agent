# -*- coding: utf-8 -*-
"""Task 3：纯函数实体解析器 —— 候选分级、上下文收敛、歧义判定、规范问句。

纯函数：不联网、不调模型、不碰工具、不接运行图。
"""
import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from entities import entity_catalog as EC  # noqa: E402
from entities import entity_aliases as EA  # noqa: E402
from entities import entity_resolver as ER  # noqa: E402

FORMAL = ROOT / "backend" / "data"
MODULE = ROOT / "backend" / "entities/entity_resolver.py"


# ------------------------------------------------------------------ 夹具

@pytest.fixture(scope="session")
def catalog():
    return EC.load(FORMAL / "entity_catalog.json",
                   FORMAL / "entity_catalog.manifest.json")


def _join_groups(courses):
    """名称按正式归一后的同组计数 —— **夹具要避开 13 组归一化碰撞**，
    否则同一 span 会合法地出现两个 T1 候选，用例就测不到它想测的东西。"""
    groups = {}
    for course in courses:
        key = ER._join_key(course["canonical_name"])
        groups.setdefault(key, set()).add(course["canonical_name"])
    return {name: len(names) for names in groups.values() for name in names}


@pytest.fixture(scope="session")
def facts(catalog):
    courses = catalog["entities"]["course"]
    majors = catalog["entities"]["major"]
    course_names = {c["canonical_name"] for c in courses}
    major_names = {m["canonical_name"] for m in majors}
    by_name = {}
    for course in courses:
        by_name.setdefault(course["canonical_name"], []).append(course)
    by_code = {}
    for course in courses:
        by_code.setdefault(course["course_code"], set()).add(course["canonical_name"])

    many_name = next(n for n, rows in sorted(by_name.items())
                     if 2 <= len(rows) <= 4 and n not in major_names
                     and _join_groups(courses)[n] == 1)
    one_course = next(c for c in courses
                      if len(by_name[c["canonical_name"]]) == 1
                      and c["canonical_name"] not in major_names
                      and _join_groups(courses)[c["canonical_name"]] == 1)
    return {
        "courses": courses, "majors": majors, "by_name": by_name, "by_code": by_code,
        "course_names": course_names, "major_names": major_names,
        "major": next(m for m in majors if m["canonical_name"] not in course_names),
        "many_name": many_name,
        "many_instances": by_name[many_name],
        "one_course": one_course,
        "reg": catalog["entities"]["regulation"][0],
        "unique_code": next(k for k, v in sorted(by_code.items()) if len(v) == 1 and k),
        "cross_name": "软件工程",
    }


@pytest.fixture(scope="session")
def hashes():
    return {"catalog": EC.sha256_file(FORMAL / "entity_catalog.json"),
            "aliases": EC.sha256_file(FORMAL / EA.ALIASES_NAME)}


@pytest.fixture
def make(catalog, hashes):
    """构造解析器（可注入 reviewed aliases / families / provider）。"""
    def _make(reviewed_aliases=(), reviewed_families=(),
              embedding_provider=None, llm_provider=None):
        return ER.Resolver(catalog, reviewed_aliases, reviewed_families,
                           catalog_sha256=hashes["catalog"],
                           aliases_sha256=hashes["aliases"],
                           embedding_provider=embedding_provider,
                           llm_provider=llm_provider)
    return _make


@pytest.fixture
def resolver(make):
    return make()


def _mention_at(envelope, text, query):
    index = query.index(text)
    for mention in envelope.mentions:
        if mention.start == index and mention.end == index + len(text):
            return mention
    return None


def _tiers(mention):
    return [c.tier for c in mention.candidates]


# ------------------------------------------------------------------ 别名 / 族夹具

def _alias(alias, entity_type, canonical=None, canonical_family=None,
           mapping_type="one_to_one", scope=None, reviewed_by="human:owner"):
    row = {"alias": alias, "entity_type": entity_type, "mapping_type": mapping_type,
           "canonical": canonical, "canonical_family": canonical_family,
           "scope": {} if scope is None else scope, "review_status": "reviewed",
           "reviewed_by": reviewed_by, "review_note": ""}
    row["alias_id"] = EA._alias_id(row)
    return row


def _family(entity_type, members, label="某族"):
    row = {"entity_type": entity_type, "canonical_label": label, "members": members,
           "review_status": "reviewed", "reviewed_by": "human:owner", "review_note": ""}
    row["family_id"] = EA._family_id(row)
    return row


# ================================================================ A 留痕

def test_envelope_records_the_full_trail(resolver, facts, hashes):
    query = facts["major"]["canonical_name"] + "的培养方案"
    env = resolver.resolve(query)
    assert env.original_query == query
    assert env.query_sha256 == hashlib.sha256(query.encode("utf-8")).hexdigest()
    assert env.catalog_sha256 == hashes["catalog"]
    assert env.aliases_sha256 == hashes["aliases"]
    assert env.resolver_version == ER.RESOLVER_VERSION
    mention = _mention_at(env, facts["major"]["canonical_name"], query)
    assert mention is not None and mention.selected is not None
    assert mention.selected.canonical_name == facts["major"]["canonical_name"]


def test_original_query_is_preserved_verbatim(resolver):
    query = "  （全角）软件工程　和\t计科 "
    env = resolver.resolve(query)
    assert env.original_query == query


def test_two_runs_are_byte_identical(make, facts):
    first = ER.dump_envelope(make().resolve(facts["cross_name"] + " 与 " + facts["many_name"]))
    second = ER.dump_envelope(make().resolve(facts["cross_name"] + " 与 " + facts["many_name"]))
    assert first == second


def test_no_mentions_means_nothing_to_clarify(resolver, catalog):
    """问句里**没有任何实体提及**时，没有需要澄清的东西。

    用**目录里从未出现过**的字符造句 —— 否则 T4/T5 会命中 `openEuler应用实践`
    之类以拉丁字母开头的名称（实测踩过）。
    """
    used = set()
    for kind in ER.ENTITY_TYPES:
        for row in catalog["entities"][kind]:
            used.update(json.dumps(row, ensure_ascii=False))
    probe = [ch for ch in "龘龖龗鱻鸝麤麠鸞驫黐黌龏" if ch not in used]
    assert len(probe) >= 3
    query = "".join(probe[:3])
    env = resolver.resolve(query)
    assert env.mentions == ()
    assert env.normalized_query == query
    assert env.requires_clarification is False


# ================================================================ B T1

def test_formal_major_name_resolves_exactly(resolver, facts):
    major = facts["major"]
    env = resolver.resolve(major["canonical_name"] + "有哪些课")
    mention = _mention_at(env, major["canonical_name"], env.original_query)
    assert mention.resolution_status == "exact_canonical"
    assert mention.support_tier == "T1"
    assert mention.selected.kind == "instance"
    assert mention.selected.entity_id == major["entity_id"]
    assert mention.selected.entity_type == "major"
    assert mention.requires_clarification is False


def test_formal_course_name_with_many_instances_is_kind_name(resolver, facts):
    name = facts["many_name"]
    env = resolver.resolve(name + "这门课")
    mention = _mention_at(env, name, env.original_query)
    assert mention.selected is not None
    assert mention.selected.kind == "name"
    assert mention.selected.entity_id is None
    assert len(mention.selected.instance_ids) == len(facts["many_instances"])
    assert set(mention.selected.instance_ids) == {c["entity_id"]
                                                 for c in facts["many_instances"]}


def test_kind_name_never_smuggles_the_first_instance(resolver, facts):
    mention = _mention_at(resolver.resolve(facts["many_name"]),
                          facts["many_name"], facts["many_name"])
    first = facts["many_instances"][0]["entity_id"]
    assert mention.selected.entity_id is None
    assert not (mention.selected.instance_ids == (first,))


def test_single_instance_course_is_kind_instance(resolver, facts):
    course = facts["one_course"]
    env = resolver.resolve(course["canonical_name"])
    mention = _mention_at(env, course["canonical_name"], env.original_query)
    assert mention.selected.kind == "instance"
    assert mention.selected.entity_id == course["entity_id"]


def test_formal_regulation_title_resolves(resolver, facts):
    title = facts["reg"]["canonical_title"]
    env = resolver.resolve("《%s》怎么规定的" % title)
    mention = _mention_at(env, title, env.original_query)
    assert mention.selected.entity_type == "regulation"
    assert mention.selected.entity_id == facts["reg"]["entity_id"]


def test_cross_type_collision_stays_ambiguous(resolver, facts):
    name = facts["cross_name"]
    assert name in facts["course_names"] and name in facts["major_names"]
    env = resolver.resolve(name + "专业怎么样")
    mention = _mention_at(env, name, env.original_query)
    assert mention.resolution_status == "ambiguous"
    assert mention.selected is None
    assert mention.entity_type is None
    assert mention.requires_clarification is True
    assert {c.entity_type for c in mention.candidates if c.tier == "T1"} == {"course", "major"}
    assert name not in env.normalized_query.replace(env.original_query, "")


def test_context_narrows_a_multi_instance_course(resolver, facts):
    name = facts["many_name"]
    target = facts["many_instances"][0]
    context = ER.ResolutionContext(grade=target["grade"], major_code=target["major_code"])
    mention = _mention_at(resolver.resolve(name, context), name, name)
    assert mention.resolution_status == "resolved_by_context"
    assert mention.selected.kind == "instance"
    assert mention.selected.entity_id == target["entity_id"]


def test_context_contradicting_every_instance_is_scope_conflict(resolver, facts):
    name = facts["many_name"]
    context = ER.ResolutionContext(grade="1900")
    mention = _mention_at(resolver.resolve(name, context), name, name)
    assert mention.resolution_status == "scope_conflict"
    assert mention.selected is None
    assert mention.requires_clarification is True


def test_course_absent_in_target_major_is_resolved_scope_absent(resolver):
    """规范课程全局存在、但目标专业作用域内不存在 —— 与 scope_conflict 分开记。"""
    major, course = "交通工程", "3D打印技术及实践"
    query = "%s专业的《%s》是多少学分？" % (major, course)
    env = ER.resolve_question(resolver, query)
    mention = _mention_at(env, course, query)
    assert mention.resolution_status == "resolved_scope_absent"
    assert mention.selected is None
    assert mention.requires_clarification is True
    assert mention.scope_absent["reason"] == "canonical_entity_absent_in_scope"
    assert mention.scope_absent["canonical_name"] == course
    assert mention.scope_absent["entity_type"] == "course"
    assert mention.scope_absent["instance_ids"]
    assert mention.scope_absent["target_major_code"]
    major_mention = _mention_at(env, major, query)
    assert major_mention.resolution_status == "exact_canonical"
    assert env.resolution_status == "resolved_scope_absent"


# ================================================================ C T2

def test_reviewed_one_to_one_resolves(make, facts):
    course = facts["one_course"]
    alias = _alias("某简写甲", "course", canonical=course["entity_id"])
    env = make([alias]).resolve("某简写甲 的学分")
    mention = _mention_at(env, "某简写甲", env.original_query)
    assert mention.resolution_status == "resolved_reviewed_alias"
    assert mention.support_tier == "T2"
    assert mention.selected.entity_id == course["entity_id"]
    assert mention.selected.alias_id == alias["alias_id"]


def test_reviewed_one_to_one_scope_conflict_is_not_forced(make, facts):
    course = facts["one_course"]
    alias = _alias("某简写乙", "course", canonical=course["entity_id"],
                   scope={"grade": "2099"})
    query = "某简写乙 的学分"
    env = make([alias]).resolve(query, ER.ResolutionContext(grade="2025"))
    mention = _mention_at(env, "某简写乙", query)
    assert mention.resolution_status == "scope_conflict"
    assert mention.selected is None
    assert env.normalized_query == query


def test_reviewed_one_to_many_without_context_is_ambiguous(make, facts):
    members = [c["entity_id"] for c in facts["many_instances"]]
    family = _family("course", sorted(members))
    alias = _alias("某族简称", "course", canonical_family=family["family_id"],
                   mapping_type="one_to_many")
    env = make([alias], [family]).resolve("某族简称 是什么")
    mention = _mention_at(env, "某族简称", env.original_query)
    assert mention.resolution_status == "ambiguous"
    assert mention.selected is None


def test_reviewed_one_to_many_converges_with_context(make, facts):
    members = [c["entity_id"] for c in facts["many_instances"]]
    family = _family("course", sorted(members))
    alias = _alias("某族简称", "course", canonical_family=family["family_id"],
                   mapping_type="one_to_many")
    target = facts["many_instances"][0]
    context = ER.ResolutionContext(grade=target["grade"], major_code=target["major_code"])
    mention = _mention_at(make([alias], [family]).resolve("某族简称", context),
                          "某族简称", "某族简称")
    assert mention.resolution_status == "resolved_by_context"
    assert mention.selected.kind == "instance"
    assert mention.selected.entity_id == target["entity_id"]
    assert mention.selected.family_id == family["family_id"]


def test_pending_alias_is_refused_at_construction(make, facts):
    alias = dict(_alias("某简写丙", "course", canonical=facts["one_course"]["entity_id"]),
                 review_status="pending", reviewed_by=None)
    with pytest.raises(ER.ResolverError):
        make([alias])


def test_rejected_family_is_refused_at_construction(make, facts):
    family = dict(_family("course", sorted(c["entity_id"] for c in facts["many_instances"])),
                  review_status="rejected", review_note="选错")
    with pytest.raises(ER.ResolverError):
        make([], [family])


def test_resolver_refuses_an_alias_bundle(catalog, hashes):
    from entities.entity_aliases import AliasBundle
    bundle = AliasBundle(catalog=catalog, aliases=(), families=(),
                         catalog_sha256=hashes["catalog"],
                         aliases_sha256=hashes["aliases"], families_sha256="0" * 64)
    with pytest.raises(TypeError):
        ER.Resolver(bundle, catalog_sha256=hashes["catalog"],
                    aliases_sha256=hashes["aliases"])


# ================================================================ D T3

def test_unique_course_code_resolves_by_rule(resolver, facts):
    code = facts["unique_code"]
    env = resolver.resolve("%s 这门课" % code)
    mention = _mention_at(env, code, env.original_query)
    assert mention.resolution_status == "resolved_by_deterministic_rule"
    assert mention.support_tier == "T3"
    assert mention.selected.canonical_name in facts["by_code"][code]


def test_code_inside_a_longer_digit_run_does_not_match(resolver, facts):
    code = facts["unique_code"]
    query = "1%s9" % code
    env = resolver.resolve(query)
    assert _mention_at(env, code, query) is None


# ================================================================ E T4 / T5：弱证据绝不选中

@pytest.mark.parametrize("text", ["高数", "大物", "计科", "软工"])
def test_abbreviation_never_auto_selects(resolver, text):
    env = resolver.resolve(text + "怎么学")
    mention = _mention_at(env, text, env.original_query)
    assert mention is not None, text
    assert mention.selected is None, text
    assert mention.requires_clarification is True, text
    assert set(_tiers(mention)) <= set(ER.WEAK_TIERS), text


def test_gaoshu_candidates_include_the_wrong_course_too(resolver):
    """`高数` 会命中 **高等代数** —— 正是它不能构成真相的证据。"""
    env = resolver.resolve("高数")
    mention = _mention_at(env, "高数", "高数")
    names = {c.canonical_name for c in mention.candidates}
    assert any(n.startswith("高等数学") for n in names)
    assert "高等代数1" in names


def test_abbreviation_is_compression_not_truncation():
    """T4 只认**压缩**（至少跳过 1 个字符），不认**截断**。

    ★ 收紧理由（实测）：不收紧时 `专业` 会命中 `专业外语`，使正常问句
    「计算机科学与技术**专业**的课程」无端多出一个 `专业` 提及并**强制澄清**。
    截断那一类归 **T5 真前缀**管（长度 ≥ 3）。
    """
    assert ER._abbrev_positions("专业", "专业外语") is None
    assert ER._abbrev_positions("高数", "高等数学") == (0, 2)
    assert ER._abbrev_positions("计科", "计算机科学与技术") == (0, 3)
    assert ER._abbrev_positions("软工", "软件工程") == (0, 2)


def test_ordinary_words_do_not_create_phantom_mentions(resolver, facts):
    name = facts["major"]["canonical_name"]
    for query in ("%s专业的课程" % name, "这个专业有哪些课", "学分是多少"):
        env = resolver.resolve(query)
        assert all(m.support_tier != "T4" or len(m.raw_text) > 2
                   or m.raw_text in ("高数", "大物", "计科", "软工", "毛概", "形策",
                                     "大英")
                   for m in env.mentions), query
        assert not any(m.raw_text == "专业" for m in env.mentions), query
    env = resolver.resolve("%s专业的课程" % name)
    assert [m.raw_text for m in env.mentions] == [name]
    assert env.requires_clarification is False


def test_weak_evidence_with_deterministic_context_selects_only_when_unique(resolver, facts):
    """★ 规则已由人工裁决变更（2026-10-17）：低层级提及在**确定性结构化上下文**下
    收敛到唯一实体时**允许**选中，并在 `context_filter` 里留下过滤前候选集与过滤依据；
    上下文缺席、或过滤后为 0 / 大于 1 时，仍一律澄清。

    本测试把六个条件逐条钉住：
      ① 候选来自真实 catalog；② 只靠结构化上下文（无 LLM/embedding/BM25/相似度）；
      ③ 过滤确实发生；④ 过滤后只剩 1 个候选；⑤ 该候选只剩 1 个实例；⑥ 过滤前有多个可能。
    旧行为（「收敛到 1 也绝不选中」）由本测试**取代**，不是删掉。
    """
    selected_case = clarified_case = None
    for text in ("形策", "大英", "高数", "大物", "毛概", "计科", "软工", "某简称"):
        base = _mention_at(resolver.resolve(text), text, text)
        if base is None or base.support_tier not in ER.WEAK_TIERS:
            continue
        # 无上下文 → 必须澄清，且不留过滤痕迹
        assert base.selected is None and base.requires_clarification, text
        assert base.context_filter is None, text

        rows = {c.canonical_name: facts["by_name"].get(c.canonical_name, [])
                for c in base.candidates}
        for dimension in ("grade", "major_code"):
            for value in sorted({row[dimension] for group in rows.values()
                                 for row in group}):
                context = ER.ResolutionContext(**{dimension: value})
                mention = _mention_at(resolver.resolve(text, context), text, text)
                assert mention.support_tier in ER.WEAK_TIERS, text
                unique = (len(mention.candidates) == 1
                          and len(mention.candidates[0].instance_ids) == 1)
                if not unique:
                    # ④⑤ 不满足 → 继续澄清，不猜
                    assert mention.selected is None, (text, dimension, value)
                    assert mention.requires_clarification is True
                    assert mention.context_filter is None
                    clarified_case = (text, dimension, value)
                    continue
                # ①–⑥ 全部满足 → 必须选中，并留下可复核的依据
                assert mention.selected is not None, (text, dimension, value)
                assert mention.resolution_status == "resolved_by_context"
                assert mention.requires_clarification is False
                basis = mention.context_filter
                assert basis and basis["rule"] == "structured_context_convergence"
                assert basis["candidates_after"] == 1 and basis["instances_after"] == 1
                assert len(basis["candidates_before"]) > 1
                assert basis[dimension] == [value]
                assert basis["selected_entity_id"] == mention.selected.instance_ids[0]
                assert mention.selected.entity_id in {
                    row["entity_id"] for group in facts["by_name"].values()
                    for row in group}
                selected_case = (text, dimension, value)
                break
            if selected_case:
                break
        if selected_case:
            break
    assert selected_case, "找不到能唯一收敛的上下文，用例失去意义"
    assert clarified_case, "找不到收敛后仍不唯一的上下文，用例失去意义"


def test_lexical_only_candidate_never_selects(resolver, facts):
    name = facts["one_course"]["canonical_name"]
    prefix = name[:max(3, len(name) - 1)]
    while prefix and prefix in facts["course_names"] | facts["major_names"]:
        prefix = prefix[:-1]
    assert prefix and len(prefix) >= 3
    env = resolver.resolve(prefix)
    mention = _mention_at(env, prefix, prefix)
    if mention is None:
        pytest.skip("该前缀在当前目录下不产生提及")
    assert mention.selected is None
    assert set(_tiers(mention)) <= set(ER.WEAK_TIERS)


# ================================================================ F 注入式 provider

def _proposal(start, end, text, entity_id=None, canonical_name=None, score=0.9):
    return {"start": start, "end": end, "text": text, "entity_id": entity_id,
            "canonical_name": canonical_name, "score": score}


def test_embedding_only_candidate_is_not_selected(make, facts):
    course = facts["one_course"]
    query = "某某某课"
    provider = lambda _q: [_proposal(0, 3, "某某某", canonical_name=course["canonical_name"])]
    env = make(embedding_provider=provider).resolve(query)
    mention = _mention_at(env, "某某某", query)
    assert mention is not None
    assert mention.selected is None
    assert mention.resolution_status == "ambiguous"
    assert mention.support_tier == "T6"


def test_llm_only_candidate_is_not_selected(make, facts):
    course = facts["one_course"]
    query = "某某某课"
    provider = lambda _q: [_proposal(0, 3, "某某某", canonical_name=course["canonical_name"])]
    mention = _mention_at(make(llm_provider=provider).resolve(query), "某某某", query)
    assert mention.selected is None
    assert mention.support_tier == "T7"
    assert mention.candidates[0].score == 0.9


def test_proposal_whose_text_does_not_match_the_query_is_discarded(make, facts):
    provider = lambda _q: [_proposal(0, 3, "对不上", canonical_name=facts["many_name"])]
    env = make(embedding_provider=provider).resolve("某某某")
    assert [d.reason for d in env.discarded_proposals] == ["proposal_text_mismatch"]
    assert env.mentions == () or all(not m.candidates for m in env.mentions)


def test_proposal_outside_the_catalog_is_discarded(make):
    provider = lambda _q: [_proposal(0, 3, "某某某", canonical_name="不存在的课程名")]
    env = make(embedding_provider=provider).resolve("某某某")
    assert [d.reason for d in env.discarded_proposals] == ["proposal_entity_not_in_catalog"]


def test_proposal_with_out_of_range_span_is_discarded(make, facts):
    provider = lambda _q: [_proposal(50, 53, "某某某", canonical_name=facts["many_name"])]
    env = make(embedding_provider=provider).resolve("某某某")
    assert [d.reason for d in env.discarded_proposals] == ["proposal_span_out_of_range"]


# ================================================================ G 提及机制

def test_multiple_mentions_are_ordered_by_start(resolver, facts):
    left, right = facts["major"]["canonical_name"], facts["cross_name"]
    assert right != left
    query = "%s 和 %s" % (left, right)
    env = resolver.resolve(query)
    starts = [m.start for m in env.mentions]
    assert starts == sorted(starts)
    assert _mention_at(env, left, query) is not None
    assert _mention_at(env, right, query) is not None


def test_repeated_mention_resolves_identically_and_replaces_both(resolver, facts):
    name = facts["one_course"]["canonical_name"]
    query = "%s 与 %s" % (name, name)
    env = resolver.resolve(query)
    mentions = [m for m in env.mentions if m.raw_text == name]
    assert len(mentions) == 2
    assert {m.selected.entity_id for m in mentions} == {facts["one_course"]["entity_id"]}
    assert env.normalized_query.count(name) == 2


def test_overlapping_spans_are_suppressed_not_dropped(resolver, facts):
    query = facts["cross_name"] + "怎么样"
    env = resolver.resolve(query)
    assert env.suppressed_spans, "应当有被重叠压制的 span"
    for span in env.suppressed_spans:
        assert query[span.start:span.end] == span.raw_text
        assert span.tier in ER.WEAK_TIERS
        assert "overlap" in span.reason
    kept = {(m.start, m.end) for m in env.mentions}
    assert all((s.start, s.end) not in kept for s in env.suppressed_spans)


def test_normalized_query_replaces_right_to_left(resolver, facts):
    one = facts["one_course"]["canonical_name"]
    second = facts["course_names"] - {one}
    other = next(c["canonical_name"] for c in facts["courses"]
                 if c["canonical_name"] in second and c["entity_id"] != facts["one_course"]["entity_id"]
                 and c["canonical_name"] != facts["many_name"]
                 and c["grade"] == facts["one_course"]["grade"])
    query = "%s 和 %s" % (one, other)
    env = resolver.resolve(query)
    assert env.normalized_query == "%s 和 %s" % (one, other)


def test_ambiguous_mention_is_not_replaced(resolver, facts):
    query = "%s 怎么样" % facts["cross_name"]
    env = resolver.resolve(query)
    assert env.normalized_query == query


def test_mention_record_is_self_consistent(resolver, facts):
    for query in (facts["major"]["canonical_name"], facts["cross_name"],
                  facts["one_course"]["canonical_name"]):
        env = resolver.resolve(query)
        for mention in env.mentions:
            assert mention.raw_text == env.original_query[mention.start:mention.end]
            assert mention.mention_id == "m:%d:%d" % (mention.start, mention.end)
            assert mention.detection_sources == tuple(sorted(set(mention.detection_sources)))
            assert mention.requires_clarification == (
                mention.resolution_status in ER.CLARIFYING_STATUSES)
            assert mention.support_tier is None or mention.candidates
            assert all(c.tier in ER.TIERS for c in mention.candidates)
            assert all(c.instance_ids for c in mention.candidates)


def test_one_unresolved_mention_does_not_drag_the_others(resolver, facts):
    """决策表第 14 行：部分歧义**不拖垮整句** —— 已确认的照样替换。"""
    other = facts["one_course"]["canonical_name"]
    query = "%s 和 %s" % (facts["cross_name"], other)
    env = resolver.resolve(query)
    blocked = _mention_at(env, facts["cross_name"], query)
    resolved = _mention_at(env, other, query)
    assert blocked.resolution_status == "ambiguous" and blocked.selected is None
    assert resolved.selected.entity_id == facts["one_course"]["entity_id"]
    assert env.requires_clarification is True
    assert other in env.normalized_query


def test_all_mentions_resolved_leaves_no_clarification(resolver, facts):
    """决策表第 15 行：全部确认时不需要澄清。"""
    major = facts["major"]
    other = next(m for m in facts["majors"]
                 if m["canonical_name"] != major["canonical_name"]
                 and m["canonical_name"] not in facts["course_names"])
    query = "%s 和 %s 有什么区别" % (major["canonical_name"], other["canonical_name"])
    env = resolver.resolve(query)
    assert env.requires_clarification is False
    assert [m.selected.entity_id for m in env.mentions] == [major["entity_id"],
                                                            other["entity_id"]]
    assert env.normalized_query == query


def test_semester_context_is_recorded_but_cannot_filter(resolver, facts):
    """★ 已声明的能力边界：目录里**没有** semester 字段，所以学期只记录、不参与过滤。"""
    name = facts["many_name"]
    context = ER.ResolutionContext(semester=1)
    env = resolver.resolve(name, context)
    assert env.context.semester == 1
    mention = _mention_at(env, name, name)
    assert len(mention.selected.instance_ids) == len(facts["many_instances"])


def test_catalog_invalid_is_reserved_for_the_factory(resolver):
    """决策表第 12/13 行：目录/别名包坏掉时**在构造期失败**，不是伪造一个 envelope。"""
    assert "catalog_invalid" in ER.RESOLUTION_STATUSES
    env = resolver.resolve("软件工程")
    assert all(m.resolution_status != "catalog_invalid" for m in env.mentions)
    assert env.resolution_status != "catalog_invalid"


def test_narrow_candidates_rejects_an_unknown_type(resolver, facts):
    mention = _mention_at(resolver.resolve(facts["cross_name"]),
                          facts["cross_name"], facts["cross_name"])
    with pytest.raises(ER.ResolverError):
        ER.narrow_candidates(mention, "teacher")


# ================================================================ H narrow_candidates

def test_narrow_candidates_filters_by_expected_type(resolver, facts):
    mention = _mention_at(resolver.resolve(facts["cross_name"]),
                          facts["cross_name"], facts["cross_name"])
    kept, dropped = ER.narrow_candidates(mention, "major")
    assert len(kept) == 1 and kept[0].entity_type == "major"
    assert all(c.entity_type == "course" for c in dropped)
    kept_reg, _ = ER.narrow_candidates(mention, "regulation")
    assert kept_reg == ()


def test_narrow_candidates_never_decides_for_the_resolver(resolver, facts):
    mention = _mention_at(resolver.resolve(facts["cross_name"]),
                          facts["cross_name"], facts["cross_name"])
    ER.narrow_candidates(mention, "major")
    assert mention.selected is None
    assert mention.resolution_status == "ambiguous"


# ================================================================ I 静态边界

def _imported_modules():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def test_resolver_imports_only_pure_modules():
    allowed = {"__future__", "hashlib", "json", "re", "dataclasses", "pathlib",
               "entities.entity_catalog", "entities.entity_aliases",
               "documents.plan_data.normalize"}
    assert _imported_modules() <= allowed, _imported_modules() - allowed


@pytest.mark.parametrize("forbidden", ["llm", "qdrant", "requests", "httpx", "urllib",
                                       "socket", "openai", "agent.runtime", "agent.core",
                                       "registry", "graph", "state"])
def test_resolver_never_imports_runtime_or_network(forbidden):
    for module in _imported_modules():
        assert forbidden not in module, module


def test_resolver_never_touches_audit_api_or_bundle():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names & {"audit_aliases", "audit_families", "AliasBundle"}
    assert ".replace(" not in source          # 禁止全局替换
    assert "random" not in _imported_modules()
    assert "time" not in _imported_modules()


def test_production_factory_only_exposes_reviewed_rows():
    """生产工厂只暴露 `reviewed`；正式表落盘后已有 6 条 reviewed 别名。

    ★ 这里以前钉的是「生产视图为空」。空是**落盘前**的状态；现在钉同一个不变量的强形式：
      生产视图**恰好等于** sidecar 里的 reviewed 子集，一条 pending/rejected 都不多。
    """
    source = MODULE.read_text(encoding="utf-8")
    assert "reviewed_aliases(" in source and "reviewed_families(" in source
    resolver = ER.build_production_resolver()
    bundle = EA.load_alias_bundle()
    dump = EA._dump          # 组内规范 JSON —— 行是 dict，不能直接进 set
    assert sorted(map(dump, resolver.reviewed_aliases)) \
        == sorted(map(dump, EA.reviewed_aliases(bundle)))
    assert sorted(map(dump, resolver.reviewed_families)) \
        == sorted(map(dump, EA.reviewed_families(bundle)))
    assert len(resolver.reviewed_aliases) == 6
    assert len(resolver.reviewed_families) == 4
    assert all(row["review_status"] == "reviewed"
               for row in list(resolver.reviewed_aliases)
               + list(resolver.reviewed_families))
    env = resolver.resolve("hello world")
    assert env.catalog_sha256 == EC.sha256_file(FORMAL / "entity_catalog.json")


def test_production_factory_fails_loudly_on_a_broken_bundle(tmp_path):
    broken = tmp_path / "b"
    broken.mkdir()
    with pytest.raises(ER.ResolverError):
        ER.build_production_resolver(broken)
