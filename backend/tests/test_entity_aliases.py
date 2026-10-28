# -*- coding: utf-8 -*-
"""Task 2：别名与实体族契约 —— 严格校验、确定性加载、reviewed-only 生产视图。

纯数据契约：不需要 Qdrant、LLM、网络。
"""
import ast
import hashlib
import json
import pathlib
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from entities import entity_catalog as EC  # noqa: E402
from entities import entity_aliases as EA  # noqa: E402

PLAN_DB = ROOT / "backend" / "data" / "plan_db.json"
CORPUS = ROOT / "_md"
FORMAL_DIR = ROOT / "backend" / "data"


# ------------------------------------------------------------------ 夹具

@pytest.fixture(scope="session")
def pristine(tmp_path_factory):
    """干净目录：只含三份正式 entity_catalog 产物（本轮 `_md` / plan_db 未变）。"""
    out = tmp_path_factory.mktemp("pristine_cat")
    EC.build(PLAN_DB, CORPUS, out)
    return out


@pytest.fixture(scope="session")
def facts(pristine):
    """真实目录里可用的实体 —— 夹具**取自当前正式目录**，不手编 id。"""
    cat = json.loads((pristine / "entity_catalog.json").read_text(encoding="utf-8"))
    collisions = json.loads(
        (pristine / "entity_catalog.collisions.json").read_text(encoding="utf-8"))
    return {
        "catalog": cat,
        "course_a": cat["entities"]["course"][0],
        "course_b": cat["entities"]["course"][1],
        "major_a": cat["entities"]["major"][0],
        "regulation_a": cat["entities"]["regulation"][0],
        "collision_pair": collisions["groups"][0]["names"],
    }


def _copy(tmp_path, pristine):
    """每个调用一个**独立**子目录 —— 同一用例内多次建包时不得互相撞车。"""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / ("b%d" % len(list(tmp_path.iterdir())))
    shutil.copytree(pristine, out)
    return out


def _pack(tmp_path, pristine, aliases=(), families=()):
    out = _copy(tmp_path, pristine)
    EA.build(out, aliases, families)
    return out


def _load(out):
    return EA.load_alias_bundle(out)


def _reject(out):
    with pytest.raises(EA.AliasCatalogError):
        _load(out)


_UNSET = object()


def _alias(facts, entity_type="course", alias="某简称", mapping_type="one_to_one",
           canonical=_UNSET, canonical_family=None, scope=None,
           review_status="reviewed", reviewed_by="human:owner", review_note=""):
    if canonical is _UNSET:
        canonical = facts["course_a"]["entity_id"] if mapping_type == "one_to_one" else None
    row = {"alias": alias, "entity_type": entity_type, "mapping_type": mapping_type,
           "canonical": canonical, "canonical_family": canonical_family,
           "scope": {} if scope is None else scope, "review_status": review_status,
           "reviewed_by": reviewed_by, "review_note": review_note}
    row["alias_id"] = (EA._alias_id(row) if isinstance(alias, str)
                       else "alias:" + "0" * 32)
    return row


def _family(facts, entity_type="course", members=None, label="某课程族",
            review_status="reviewed", reviewed_by="human:owner", review_note=""):
    if members is None:
        members = sorted([facts["course_a"]["entity_id"], facts["course_b"]["entity_id"]])
    row = {"entity_type": entity_type, "canonical_label": label, "members": members,
           "review_status": review_status, "reviewed_by": reviewed_by,
           "review_note": review_note}
    row["family_id"] = EA._family_id(row)
    return row


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")) + "\n" for r in rows),
                    encoding="utf-8")


def _mutate_rows(out, name, mutate):
    """读出 JSONL、改写、写回 —— 用于逐行门的隔离测试。"""
    rows = [json.loads(line) for line in
            (out / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    mutate(rows)
    _write_jsonl(out / name, rows)


def _manifest(out):
    return json.loads((out / EA.MANIFEST_NAME).read_text(encoding="utf-8"))


def _manifest_reject(tmp_path, pristine, mutate):
    out = _pack(tmp_path, pristine, aliases=[], families=[])
    m = _manifest(out)
    mutate(m)
    (out / EA.MANIFEST_NAME).write_text(
        json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8")
    _reject(out)


def _resign(out, *, parse=True):
    """只重签 manifest 的 rows / sha256 / 状态分布 —— 让哈希门放行，
    把 sidecar 行级门单独隔离出来。

    `parse=False` 用于**故意畸形**的行：此时只按行数与文本重签，
    测试助手不得因为自己解析不了而先炸掉（那会把真红灯变成构造错误）。
    """
    m = _manifest(out)
    for key, name in (("aliases", EA.ALIASES_NAME), ("families", EA.FAMILIES_NAME)):
        text = (out / name).read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        m[key]["rows"] = len(lines)
        m[key]["sha256"] = EA._sha256_text(text)
        if parse:
            m[key]["by_review_status"] = EA._by_status(
                [json.loads(line) for line in lines])
    (out / EA.MANIFEST_NAME).write_text(
        json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8")


def _write_unchecked(out, aliases, families):
    """**只组装、不校验**地落盘。

    复用 `EA.build` 的同一份组装逻辑（`_pack_products`），所以不会另造一套
    manifest 契约；目的是把**加载侧**的行级门单独隔离出来测。
    """
    _manifest, staged = EA._pack_products(Path(out), aliases, families)
    for name, text in staged.items():
        (Path(out) / name).write_text(text, encoding="utf-8")


def _row_reject(tmp_path, pristine, aliases=(), families=()):
    """行非法 → **生成侧和加载侧都必须拒**，且生成侧不得留下任何半成品。"""
    out = _copy(tmp_path, pristine)
    before = sorted(p.name for p in out.iterdir())
    with pytest.raises(EA.AliasCatalogError):
        EA.build(out, aliases, families)
    assert sorted(p.name for p in out.iterdir()) == before, "生成侧写下了半成品"
    assert not list(out.glob("*.tmp"))
    _write_unchecked(out, aliases, families)
    with pytest.raises(EA.AliasCatalogError):
        EA.load_alias_bundle(out)
    return out


# ================================================================ A EntityFamily

def test_reviewed_family_is_accepted(tmp_path, pristine, facts):
    fam = _family(facts)
    out = _pack(tmp_path, pristine, families=[fam])
    bundle = _load(out)
    assert [f["family_id"] for f in EA.reviewed_families(bundle)] == [fam["family_id"]]


def test_pending_family_stays_in_audit_only(tmp_path, pristine, facts):
    fam = _family(facts, review_status="pending", reviewed_by=None)
    out = _pack(tmp_path, pristine, families=[fam])
    bundle = _load(out)
    assert EA.reviewed_families(bundle) == ()
    assert len(EA.audit_families(bundle)) == 1


def test_rejected_family_is_audit_only(tmp_path, pristine, facts):
    fam = _family(facts, review_status="rejected", review_note="成员选错")
    out = _pack(tmp_path, pristine, families=[fam])
    bundle = _load(out)
    assert EA.reviewed_families(bundle) == ()
    assert len(EA.audit_families(bundle)) == 1


@pytest.mark.parametrize("members", [[0], [], ["a"]])
def test_family_needs_two_distinct_members(tmp_path, pristine, facts, members):
    _row_reject(tmp_path, pristine,
                families=[_family(facts, members=members)])


def test_family_with_duplicate_members_is_rejected(tmp_path, pristine, facts):
    one = facts["course_a"]["entity_id"]
    _row_reject(tmp_path, pristine, families=[_family(facts, members=[one, one])])


def test_family_with_unsorted_members_is_rejected(tmp_path, pristine, facts):
    members = sorted([facts["course_a"]["entity_id"], facts["course_b"]["entity_id"]],
                     reverse=True)
    _row_reject(tmp_path, pristine, families=[_family(facts, members=members)])


def test_orphan_family_member_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts, members=sorted([facts["course_a"]["entity_id"],
                                         "course:1999:999999:XXXX:deadbeef"]))
    _row_reject(tmp_path, pristine, families=[fam])


def test_family_member_of_another_entity_type_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts, entity_type="course",
                  members=sorted([facts["course_a"]["entity_id"],
                                  facts["major_a"]["entity_id"]]))
    _row_reject(tmp_path, pristine, families=[fam])


def test_wrong_family_id_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts)
    fam["family_id"] = "family:" + "0" * 32
    _row_reject(tmp_path, pristine, families=[fam])


def test_same_member_set_registered_twice_is_rejected(tmp_path, pristine, facts):
    """相同成员集合**不得**以不同 id 重复登记（重复 id 亦不例外）。"""
    fam = _family(facts)
    twin = dict(fam, family_id="family:" + "ordinal".ljust(32, "0"))
    twin["canonical_label"] = "另一个标签"
    _row_reject(tmp_path, pristine, families=[fam, twin])


def test_duplicate_family_id_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts)
    _row_reject(tmp_path, pristine, families=[fam, dict(fam)])


@pytest.mark.parametrize("field", EA.FAMILY_FIELDS)
def test_family_missing_field_is_rejected(tmp_path, pristine, facts, field):
    fam = _family(facts)
    fam.pop(field)
    _row_reject(tmp_path, pristine, families=[fam])


def test_family_extra_field_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts)
    fam["score"] = 0.97
    _row_reject(tmp_path, pristine, families=[fam])


def test_family_bad_entity_type_is_rejected(tmp_path, pristine, facts):
    _row_reject(tmp_path, pristine, families=[_family(facts, entity_type="teacher")])


@pytest.mark.parametrize("status", ["approved", "REVIEWED", "", None])
def test_family_bad_review_status_is_rejected(tmp_path, pristine, facts, status):
    _row_reject(tmp_path, pristine,
                families=[_family(facts, review_status=status)])


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_family_reviewed_needs_reviewed_by(tmp_path, pristine, facts, bad):
    _row_reject(tmp_path, pristine, families=[_family(facts, reviewed_by=bad)])


def test_family_pending_must_not_carry_reviewed_by(tmp_path, pristine, facts):
    _row_reject(tmp_path, pristine,
                families=[_family(facts, review_status="pending",
                                  reviewed_by="human:owner")])


@pytest.mark.parametrize("note", ["", "   ", None])
def test_family_rejected_needs_review_note(tmp_path, pristine, facts, note):
    _row_reject(tmp_path, pristine,
                families=[_family(facts, review_status="rejected",
                                  review_note=note)])


@pytest.mark.parametrize("label", ["", "   ", None, 123])
def test_family_needs_a_human_readable_label(tmp_path, pristine, facts, label):
    _row_reject(tmp_path, pristine, families=[_family(facts, label=label)])


def test_families_are_never_derived_from_names(tmp_path, pristine, facts):
    """同名 / join_key 相同 / 字符相似**都不得**自动建族 —— 名字再像也要人审。"""
    out = _pack(tmp_path, pristine, aliases=[], families=[])
    assert _manifest(out)["families"]["rows"] == 0
    assert len(facts["collision_pair"]) >= 2      # 目录里确实存在归一化碰撞
    assert _load(out).families == ()


def test_each_gate_reports_its_own_reason(tmp_path, pristine, facts):
    """拒绝必须**指名**是哪道门 —— 否则「拒了」可能来自另一道门，等于假绿。"""
    orphan = "course:1999:199999:XXXX:deadbeef"
    cases = [
        (dict(aliases=[_alias(facts, alias=facts["course_a"]["canonical_name"])]),
         "与正式"),
        (dict(aliases=[_alias(facts, canonical=orphan)]), "孤儿"),
        (dict(aliases=[_alias(facts, mapping_type="one_to_many", canonical=None,
                              canonical_family="family:" + "0" * 32)]), "孤儿"),
        (dict(aliases=[_alias(facts, scope={"college": "x"})]), "未知键"),
        (dict(aliases=[_alias(facts, alias="角色错", entity_type="course",
                              canonical=facts["major_a"]["entity_id"])]), "不符"),
        (dict(families=[_family(facts, members=[facts["course_a"]["entity_id"]])]),
         "至少两个"),
        (dict(families=[_family(facts, review_status="pending",
                                reviewed_by="human:owner")]), "pending"),
        (dict(families=[_family(facts, review_status="rejected", review_note="")]),
         "拒绝理由"),
        (dict(aliases=[_alias(facts, alias="撞车", canonical=facts["course_a"]["entity_id"]),
                       _alias(facts, alias="撞车",
                              canonical=facts["course_b"]["entity_id"])]), "整体拒绝"),
        (dict(aliases=[_alias(facts, alias="待审", review_status="pending")]), "pending"),
    ]
    for kwargs, needle in cases:
        out = _copy(tmp_path, pristine)
        with pytest.raises(EA.AliasCatalogError) as excinfo:
            EA.build(out, kwargs.get("aliases", []), kwargs.get("families", []))
        assert needle in str(excinfo.value), (needle, str(excinfo.value))


# ================================================================ B AliasRecord

def test_reviewed_one_to_one_is_accepted(tmp_path, pristine, facts):
    row = _alias(facts, alias="某门课的简称")
    bundle = _load(_pack(tmp_path, pristine, aliases=[row]))
    assert [r["alias_id"] for r in EA.reviewed_aliases(bundle)] == [row["alias_id"]]


def test_reviewed_one_to_many_is_accepted(tmp_path, pristine, facts):
    fam = _family(facts)
    row = _alias(facts, alias="某课程族简称", mapping_type="one_to_many",
                 canonical=None, canonical_family=fam["family_id"])
    bundle = _load(_pack(tmp_path, pristine, aliases=[row], families=[fam]))
    assert len(EA.reviewed_aliases(bundle)) == 1


def test_one_to_one_filling_canonical_family_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts)
    row = _alias(facts, alias="错填族", canonical_family=fam["family_id"])
    _row_reject(tmp_path, pristine, aliases=[row], families=[fam])


def test_one_to_many_filling_canonical_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts, alias="错填实体", mapping_type="one_to_many",
                 canonical=facts["course_a"]["entity_id"])
    _row_reject(tmp_path, pristine, aliases=[row])


def test_both_targets_filled_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts)
    row = _alias(facts, alias="两个都填", canonical_family=fam["family_id"])
    _row_reject(tmp_path, pristine, aliases=[row], families=[fam])


def test_no_target_filled_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts, alias="两个都空", canonical=None)
    _row_reject(tmp_path, pristine, aliases=[row])


def test_orphan_canonical_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts, alias="孤儿实体", canonical="course:1999:999999:XXXX:deadbeef")
    _row_reject(tmp_path, pristine, aliases=[row])


def test_orphan_family_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts, alias="孤儿族", mapping_type="one_to_many",
                 canonical=None, canonical_family="family:" + "0" * 32)
    _row_reject(tmp_path, pristine, aliases=[row])


def test_entity_type_mismatch_with_target_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts, entity_type="course", alias="类型不符",
                 canonical=facts["major_a"]["entity_id"])
    _row_reject(tmp_path, pristine, aliases=[row])


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_one_to_many_pointing_to_unreviewed_family_is_rejected(
        tmp_path, pristine, facts, status):
    """族没审过就不许被映射 —— 即使这条 alias 自己也没审过。"""
    fam = _family(facts, review_status=status,
                  reviewed_by=None if status == "pending" else "human:owner",
                  review_note="" if status == "pending" else "成员选错")
    row = _alias(facts, alias="指向未审族", mapping_type="one_to_many",
                 canonical=None, canonical_family=fam["family_id"],
                 review_status="pending", reviewed_by=None)
    _row_reject(tmp_path, pristine, aliases=[row], families=[fam])


def test_family_entity_type_mismatch_is_rejected(tmp_path, pristine, facts):
    fam = _family(facts, entity_type="major",
                  members=sorted([facts["major_a"]["entity_id"]] +
                                 [m["entity_id"] for m in facts["catalog"]["entities"]["major"]
                                  if m["entity_id"] != facts["major_a"]["entity_id"]][:1]))
    row = _alias(facts, entity_type="course", alias="族类型不符",
                 mapping_type="one_to_many", canonical=None,
                 canonical_family=fam["family_id"])
    _row_reject(tmp_path, pristine, aliases=[row], families=[fam])


def test_unreviewed_aliases_never_reach_production(tmp_path, pristine, facts):
    pending = _alias(facts, alias="待审简称", review_status="pending", reviewed_by=None)
    rejected = _alias(facts, alias="已拒简称", review_status="rejected",
                      review_note="不是校内通用简称")
    ok = _alias(facts, alias="已审简称")
    bundle = _load(_pack(tmp_path, pristine, aliases=[pending, rejected, ok]))
    assert [r["alias"] for r in EA.reviewed_aliases(bundle)] == ["已审简称"]
    assert len(EA.audit_aliases(bundle)) == 3


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_reviewed_alias_needs_reviewed_by(tmp_path, pristine, facts, bad):
    _row_reject(tmp_path, pristine, aliases=[_alias(facts, reviewed_by=bad)])


def test_pending_alias_must_not_carry_reviewed_by(tmp_path, pristine, facts):
    _row_reject(tmp_path, pristine,
                aliases=[_alias(facts, review_status="pending",
                                reviewed_by="human:owner")])


@pytest.mark.parametrize("note", ["", "   ", None])
def test_rejected_alias_needs_review_note(tmp_path, pristine, facts, note):
    _row_reject(tmp_path, pristine,
                aliases=[_alias(facts, review_status="rejected", review_note=note)])


@pytest.mark.parametrize("alias", ["", "   ", "\t", "\n", "　", None, 7])
def test_alias_must_be_non_blank_text(tmp_path, pristine, facts, alias):
    _row_reject(tmp_path, pristine, aliases=[_alias(facts, alias=alias)])


@pytest.mark.parametrize("alias", ["高\x00数", "\x07提示", "高数\x1b"])
def test_alias_with_control_characters_is_rejected(tmp_path, pristine, facts, alias):
    _row_reject(tmp_path, pristine, aliases=[_alias(facts, alias=alias)])


def test_wrong_alias_id_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts)
    row["alias_id"] = "alias:" + "0" * 32
    _row_reject(tmp_path, pristine, aliases=[row])


def test_alias_id_ignores_review_notes(tmp_path, pristine, facts):
    """审核备注变化**不得**改变身份。"""
    a = _alias(facts)
    b = dict(a, review_note="换个说法", reviewed_by="someone:else")
    assert EA._alias_id(a) == EA._alias_id(b)


def test_alias_conflicting_with_formal_course_name_is_rejected(tmp_path, pristine, facts):
    name = facts["course_a"]["canonical_name"]
    row = _alias(facts, alias=name, canonical=facts["course_a"]["entity_id"])
    _row_reject(tmp_path, pristine, aliases=[row])


def test_alias_conflicting_only_by_spacing_is_rejected(tmp_path, pristine, facts):
    """别名**不是**正式名，只是差一个空格 —— 仍然不许遮蔽正式名。"""
    name = facts["course_a"]["canonical_name"]
    middle = len(name) // 2
    spaced = name[:middle] + " " + name[middle:]
    assert spaced != name
    row = _alias(facts, alias=spaced, canonical=facts["course_a"]["entity_id"])
    _row_reject(tmp_path, pristine, aliases=[row])


def test_alias_matching_another_entity_type_is_rejected(tmp_path, pristine, facts):
    """冲突检查按**全实体目录**做 —— 不得只看同类型。"""
    row = _alias(facts, entity_type="course", alias=facts["major_a"]["canonical_name"])
    _row_reject(tmp_path, pristine, aliases=[row])


def test_alias_matching_a_regulation_title_is_rejected(tmp_path, pristine, facts):
    row = _alias(facts, entity_type="course",
                 alias=facts["regulation_a"]["canonical_title"])
    _row_reject(tmp_path, pristine, aliases=[row])


def test_conflicting_reviewed_mappings_are_rejected(tmp_path, pristine, facts):
    """同一 alias + entity_type + scope 的 reviewed 映射目标不同 → 整体拒绝。"""
    first = _alias(facts, alias="同一个简称", canonical=facts["course_a"]["entity_id"])
    second = _alias(facts, alias="同一个简称", canonical=facts["course_b"]["entity_id"])
    assert first["alias_id"] != second["alias_id"]
    _row_reject(tmp_path, pristine, aliases=[first, second])


def test_identical_duplicate_rows_are_rejected(tmp_path, pristine, facts):
    row = _alias(facts, alias="完全重复")
    _row_reject(tmp_path, pristine, aliases=[row, dict(row)])


def test_pending_row_may_repeat_a_key_without_touching_production(
        tmp_path, pristine, facts):
    """非 reviewed 的提案不构成生产冲突；它也不会进生产视图。"""
    done = _alias(facts, alias="已定简称", canonical=facts["course_a"]["entity_id"])
    draft = _alias(facts, alias="已定简称", canonical=facts["course_b"]["entity_id"],
                   review_status="pending", reviewed_by=None)
    bundle = _load(_pack(tmp_path, pristine, aliases=[done, draft]))
    assert len(EA.reviewed_aliases(bundle)) == 1


def test_same_alias_with_different_scope_coexists(tmp_path, pristine, facts):
    a = _alias(facts, alias="跨年级简称", scope={"grade": "2024"})
    b = _alias(facts, alias="跨年级简称", scope={"grade": "2025"})
    bundle = _load(_pack(tmp_path, pristine, aliases=[a, b]))
    assert len(EA.reviewed_aliases(bundle)) == 2


def test_same_key_with_different_scope_target_is_allowed(tmp_path, pristine, facts):
    a = _alias(facts, alias="分年级简称", scope={"grade": "2024"},
               canonical=facts["course_a"]["entity_id"])
    b = _alias(facts, alias="分年级简称", scope={"grade": "2025"},
               canonical=facts["course_b"]["entity_id"])
    bundle = _load(_pack(tmp_path, pristine, aliases=[a, b]))
    assert len(EA.reviewed_aliases(bundle)) == 2


def test_scope_must_be_an_object(tmp_path, pristine, facts):
    _row_reject(tmp_path, pristine, aliases=[_alias(facts, scope=["grade"])])


def test_unknown_scope_key_is_rejected(tmp_path, pristine, facts):
    _row_reject(tmp_path, pristine,
                aliases=[_alias(facts, scope={"college": "计算机与信息安全学院"})])


@pytest.mark.parametrize("scope,bad", [
    ({"grade": ""}, "空串"), ({"grade": None}, "None"), ({"grade": 2025}, "整数"),
    ({"major_code": ""}, "空串"), ({"major_code": 81802}, "整数"),
    ({"semester": 0}, "零"), ({"semester": -1}, "负数"), ({"semester": True}, "bool"),
    ({"semester": "3"}, "字符串"), ({"semester": 1.0}, "浮点"),
])
def test_scope_value_type_errors_are_rejected(tmp_path, pristine, facts, scope, bad):
    _row_reject(tmp_path, pristine, aliases=[_alias(facts, scope=scope)])


@pytest.mark.parametrize("field", EA.ALIAS_FIELDS)
def test_alias_missing_field_is_rejected(tmp_path, pristine, facts, field):
    row = _alias(facts)
    row.pop(field)
    _row_reject(tmp_path, pristine, aliases=[row])


@pytest.mark.parametrize("field,bad", [("score", 0.97), ("embedding", [0.1]),
                                       ("llm_reason", "我觉得是")])
def test_alias_forbids_modelish_fields(tmp_path, pristine, facts, field, bad):
    row = _alias(facts)
    row[field] = bad
    _row_reject(tmp_path, pristine, aliases=[row])


@pytest.mark.parametrize("entity_type", ["teacher", "MAJOR", "", None])
def test_alias_bad_entity_type_is_rejected(tmp_path, pristine, facts, entity_type):
    _row_reject(tmp_path, pristine, aliases=[_alias(facts, entity_type=entity_type)])


def test_alias_bad_mapping_type_is_rejected(tmp_path, pristine, facts):
    _row_reject(tmp_path, pristine,
                aliases=[_alias(facts, mapping_type="many_to_many")])


# ================================================================ C 安全门

def test_all_failures_are_alias_catalog_error(tmp_path, pristine):
    out = _pack(tmp_path, pristine, aliases=[], families=[])
    (out / EA.ALIASES_NAME).write_text("{ not json", encoding="utf-8")
    with pytest.raises(EA.AliasCatalogError):
        _load(out)


def test_every_failure_mode_raises_the_dedicated_error(tmp_path, pristine, facts):
    """缺文件 / 畸形 JSON / 畸形 JSONL / 类型错 —— 一律专用异常，不裸露他类异常。"""
    out = _pack(tmp_path, pristine, aliases=[], families=[])
    for name in (EA.ALIASES_NAME, EA.FAMILIES_NAME, EA.MANIFEST_NAME):
        backup = (out / name).read_bytes()
        (out / name).unlink()
        with pytest.raises(EA.AliasCatalogError):
            _load(out)
        (out / name).write_bytes(backup)
    (out / EA.MANIFEST_NAME).write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(EA.AliasCatalogError):
        _load(out)


def test_catalog_failure_is_not_disguised(tmp_path, pristine):
    """目录坏掉时**明确报错** —— 不得伪装成 no_evidence / irrelevant / tool_failed。"""
    out = _pack(tmp_path, pristine, aliases=[], families=[])
    (out / EC.CATALOG_NAME).write_text('{"schema_version":"x"}', encoding="utf-8")
    with pytest.raises(EA.AliasCatalogError):
        _load(out)


def test_no_bypass_parameter_exists():
    import inspect

    for func in (EA.load_alias_bundle, EA.reviewed_aliases, EA.audit_aliases,
                 EA.reviewed_families, EA.audit_families):
        names = set(inspect.signature(func).parameters)
        assert not names & {"include_pending", "unsafe", "force", "skip_review",
                            "trusted", "all_statuses"}, func.__name__


# ---------------------------------------------------------------- manifest

@pytest.mark.parametrize("field", EA.MANIFEST_FIELDS)
def test_manifest_missing_field_is_rejected(tmp_path, pristine, field):
    _manifest_reject(tmp_path, pristine, lambda m: m.pop(field))


def test_manifest_extra_field_is_rejected(tmp_path, pristine):
    _manifest_reject(tmp_path, pristine, lambda m: m.__setitem__("timestamp", 1))


@pytest.mark.parametrize("field,bad", [("artifact_kind", "entity_catalog_manifest"),
                                       ("schema_version", "entity_aliases/2"),
                                       ("generator_version", "entity_aliases/9.9.9")])
def test_manifest_bad_version_or_kind_is_rejected(tmp_path, pristine, field, bad):
    _manifest_reject(tmp_path, pristine, lambda m: m.__setitem__(field, bad))


@pytest.mark.parametrize("field", ("catalog", "aliases", "families"))
def test_manifest_block_missing_field_is_rejected(tmp_path, pristine, field):
    for key in (EA.CATALOG_BLOCK_FIELDS if field == "catalog" else EA.FILE_BLOCK_FIELDS):
        _manifest_reject(tmp_path, pristine, lambda m, k=key, f=field: m[f].pop(k))


@pytest.mark.parametrize("field", ("catalog", "aliases", "families"))
def test_manifest_block_extra_field_is_rejected(tmp_path, pristine, field):
    _manifest_reject(tmp_path, pristine,
                     lambda m, f=field: m[f].__setitem__("rows_extra", 0))


@pytest.mark.parametrize("key,name", [("aliases", EA.ALIASES_NAME),
                                      ("families", EA.FAMILIES_NAME)])
@pytest.mark.parametrize("bad", ["C:/tmp/entity_aliases.jsonl",
                                 "backend/data/other.jsonl",
                                 "backend/data/entity_aliases.manifest.json",
                                 "/abs/entity_aliases.jsonl", "entity_aliases.jsonl"])
def test_manifest_logical_path_is_fixed(tmp_path, pristine, key, name, bad):
    _manifest_reject(tmp_path, pristine, lambda m, k=key: m[k].__setitem__(
        "logical_path", bad))


def test_manifest_catalog_logical_path_is_fixed(tmp_path, pristine):
    _manifest_reject(tmp_path, pristine, lambda m: m["catalog"].__setitem__(
        "logical_path", "backend/data/entity_catalog.manifest.json"))


@pytest.mark.parametrize("bad", ["A" * 64, "0" * 63, "g" * 64, "", 123])
def test_manifest_sha_must_be_lowercase_hex(tmp_path, pristine, bad):
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["aliases"].__setitem__("sha256", bad))
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["catalog"].__setitem__("sha256", bad))


@pytest.mark.parametrize("bad", [False, 1, "true", None])
def test_manifest_exists_must_be_true(tmp_path, pristine, bad):
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["aliases"].__setitem__("exists", bad))


@pytest.mark.parametrize("field,bad", [("rows", "0"), ("rows", -1), ("rows", True),
                                       ("by_review_status", []),
                                       ("by_review_status", {"reviewed": 0})])
def test_manifest_rows_and_status_shape_is_fixed(tmp_path, pristine, field, bad):
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["aliases"].__setitem__(field, bad))


def test_manifest_by_review_status_needs_all_three_keys(tmp_path, pristine):
    for key in EA.REVIEW_STATUSES:
        _manifest_reject(tmp_path, pristine,
                         lambda m, k=key: m["families"]["by_review_status"].pop(k))
    _manifest_reject(tmp_path, pristine, lambda m: m["families"][
        "by_review_status"].__setitem__("bogus", 0))


def test_manifest_catalog_sha_must_match_the_catalog(tmp_path, pristine):
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["catalog"].__setitem__("sha256", "0" * 64))


def test_manifest_has_no_environment_fields(tmp_path, pristine):
    out = _pack(tmp_path, pristine, aliases=[], families=[])
    blob = (out / EA.MANIFEST_NAME).read_text(encoding="utf-8")
    for bad in ("timestamp", "hostname", "username", "C:\\", "/root/", "/home/"):
        assert bad not in blob, bad


# ---------------------------------------------------------------- sidecar 篡改

def test_tampered_aliases_file_is_rejected(tmp_path, pristine, facts):
    out = _pack(tmp_path, pristine, aliases=[_alias(facts, alias="甲")])
    _mutate_rows(out, EA.ALIASES_NAME, lambda rows: rows[0].__setitem__("alias", "乙"))
    _reject(out)


def test_tampered_families_file_is_rejected(tmp_path, pristine, facts):
    out = _pack(tmp_path, pristine, families=[_family(facts)])
    _mutate_rows(out, EA.FAMILIES_NAME,
                 lambda rows: rows[0].__setitem__("canonical_label", "改过"))
    _reject(out)


@pytest.mark.parametrize("offset", [0, 1])
def test_manifest_row_count_drift_is_rejected(tmp_path, pristine, offset):
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["aliases"].__setitem__("rows",
                                                        m["aliases"]["rows"] + 1 + offset))


def test_manifest_status_distribution_drift_is_rejected(tmp_path, pristine):
    _manifest_reject(tmp_path, pristine,
                     lambda m: m["aliases"]["by_review_status"].__setitem__(
                         "reviewed", m["aliases"]["by_review_status"]["reviewed"] + 1))


def test_malformed_jsonl_line_is_rejected(tmp_path, pristine, facts):
    out = _pack(tmp_path, pristine, aliases=[_alias(facts, alias="甲")])
    (out / EA.ALIASES_NAME).write_text(
        (out / EA.ALIASES_NAME).read_text(encoding="utf-8") + "{ oops\n",
        encoding="utf-8")
    _resign(out, parse=False)
    _reject(out)


def test_jsonl_line_must_be_an_object(tmp_path, pristine, facts):
    out = _pack(tmp_path, pristine, aliases=[_alias(facts, alias="甲")])
    _mutate_rows(out, EA.ALIASES_NAME, lambda rows: rows.clear() or rows.append(["x"]))
    _resign(out)
    _reject(out)


# ================================================================ D 禁止 grandfather

def test_module_never_touches_query_expansions():
    """**代码**里不得出现任何指向 `query_expansions.json` 的引用。

    模块文档里点名说明「它不读」是允许的（那是给人看的）；这里用 AST 排除
    文档字符串，只扫真正会参与执行的字符串常量 —— 静态检查不得被注释糊弄。
    """
    tree = ast.parse((ROOT / "backend" / "entities/entity_aliases.py").read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    live = [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]
    assert live, "AST 扫描没找到任何代码字符串 —— 检查本身失效了"
    for node in live:
        assert "query_expansions" not in node.value


def test_formal_sidecars_hold_only_reviewed_rows():
    """★ 前身是 `test_formal_sidecars_are_empty_zero_byte_files` —— 它钉的是「本轮为空」。
    2026-10-17 的人工裁决把 6 条简称落成正式映射后，那份"空"被取代；
    这里改钉**取代它的不变量的强形式**：两份 sidecar 非空，且**每一行都是 reviewed**。"""
    for name in (EA.ALIASES_NAME, EA.FAMILIES_NAME):
        path = FORMAL_DIR / name
        assert path.exists(), name
        assert path.stat().st_size > 0, name
        assert EC.sha256_file(path) != EA.EMPTY_SHA256, name
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert rows, name
        assert all(row["review_status"] == "reviewed" for row in rows), name
        assert all(row["reviewed_by"] == "human:owner" for row in rows), name
        assert all(row["review_note"].strip() for row in rows), name


@pytest.mark.parametrize("alias", ["高数", "毛概", "大物", "计科", "软工", "代数"])
def test_approved_demand_examples_are_in_formal_sidecars(alias):
    """★ 前身是 `..._are_not_in_formal_sidecars`。这 6 条简称已由人工裁决**批准落盘**，
    所以这里反过来钉「它们确实在正式表里，且是 reviewed」。"""
    text = (FORMAL_DIR / EA.ALIASES_NAME).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    matched = [row for row in rows if row["alias"] == alias]
    assert len(matched) == 1, alias
    assert matched[0]["review_status"] == "reviewed", alias
    assert matched[0]["reviewed_by"] == "human:owner", alias


def test_formal_bundle_loads_and_exposes_the_reviewed_rows():
    bundle = EA.load_alias_bundle()
    assert EA.audit_aliases(bundle) == EA.reviewed_aliases(bundle)
    assert EA.audit_families(bundle) == EA.reviewed_families(bundle)
    assert len(EA.reviewed_aliases(bundle)) == 6
    assert len(EA.reviewed_families(bundle)) == 4
    assert bundle.catalog_sha256 == EC.sha256_file(FORMAL_DIR / EC.CATALOG_NAME)


def test_formal_manifest_binds_to_the_formal_catalog():
    m = json.loads((FORMAL_DIR / EA.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert m["catalog"]["sha256"] == EC.sha256_file(FORMAL_DIR / EC.CATALOG_NAME)
    assert m["catalog"]["logical_path"] == EC.CATALOG_LOGICAL


def test_build_never_creates_entities_in_the_catalog(tmp_path, pristine, facts):
    """别名**不得**被复制成目录里的新实体。"""
    before = (tmp_path / "before.json")
    shutil.copy(pristine / "entity_catalog.json", before)
    out = _pack(tmp_path, pristine, aliases=[_alias(facts, alias="新简称")])
    assert (out / "entity_catalog.json").read_bytes() == before.read_bytes()


# ================================================================ 复现与写盘

def test_two_independent_dirs_are_byte_identical(tmp_path, pristine, facts):
    fam = _family(facts)
    alias = _alias(facts, alias="简称甲")
    first = _pack(tmp_path / "one", pristine, aliases=[alias], families=[fam])
    second = _pack(tmp_path / "two", pristine, aliases=[alias], families=[fam])
    for name in (EA.ALIASES_NAME, EA.FAMILIES_NAME, EA.MANIFEST_NAME):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_official_sidecars_are_a_fixed_point_of_the_generator(tmp_path, pristine):
    """正式 sidecar 必须与**现生成器**的输出逐字节一致。

    ★ 输入不再是 `[]`：正式表里已经有 6 条人工裁决的映射。把**已落盘的那些行**喂回去，
      验证「再次构建 = 原样输出」—— 即正式产物是当前生成器的不动点（排序 / 格式化 /
      manifest 记账都没有漂移）。行的**来源**（人工裁决）本身不在这里复现。
    """
    aliases = [json.loads(line) for line in
               (FORMAL_DIR / EA.ALIASES_NAME).read_text(encoding="utf-8").splitlines()
               if line.strip()]
    families = [json.loads(line) for line in
                (FORMAL_DIR / EA.FAMILIES_NAME).read_text(encoding="utf-8").splitlines()
                if line.strip()]
    assert aliases and families
    out = _pack(tmp_path, pristine, aliases=aliases, families=families)
    for name in (EA.ALIASES_NAME, EA.FAMILIES_NAME, EA.MANIFEST_NAME):
        assert (out / name).read_bytes() == (FORMAL_DIR / name).read_bytes(), name


def test_build_validation_happens_before_writing(tmp_path, pristine, facts):
    out = _copy(tmp_path, pristine)
    before = sorted(p.name for p in out.iterdir())
    bad = _alias(facts, alias="孤儿", canonical="course:1999:999999:XXXX:deadbeef")
    with pytest.raises(EA.AliasCatalogError):
        EA.build(out, [bad], [])
    assert sorted(p.name for p in out.iterdir()) == before
    assert not list(out.glob("*.tmp"))


def test_build_rejects_conflicting_reviewed_mappings(tmp_path, pristine, facts):
    out = _copy(tmp_path, pristine)
    before = sorted(p.name for p in out.iterdir())
    rows = [_alias(facts, alias="同名", canonical=facts["course_a"]["entity_id"]),
            _alias(facts, alias="同名", canonical=facts["course_b"]["entity_id"])]
    with pytest.raises(EA.AliasCatalogError):
        EA.build(out, rows, [])
    assert sorted(p.name for p in out.iterdir()) == before


# ---------------------------------------------------------------- 写盘失败边界

PRODUCTS = (EA.ALIASES_NAME, EA.FAMILIES_NAME, EA.MANIFEST_NAME)


def _sentinel(out):
    for name in PRODUCTS:
        (out / name).write_bytes(b"OLD-SENTINEL-BYTES")


def _patch_write_text(monkeypatch, out_dir, fail_index, *, partial):
    original = pathlib.Path.write_text
    seen = {"n": 0}

    def wrapper(self, data, *args, **kwargs):
        if self.parent == out_dir and self.name.endswith(".tmp"):
            seen["n"] += 1
            if seen["n"] == fail_index:
                if partial:
                    original(self, "HALF", *args, **kwargs)
                raise OSError("injected-stage-write-%d" % fail_index)
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", wrapper)


def _patch_replace(monkeypatch, out_dir, fail_index):
    original = pathlib.Path.replace
    seen = {"n": 0}

    def wrapper(self, target, *args, **kwargs):
        if self.parent == out_dir and self.name.endswith(".tmp"):
            seen["n"] += 1
            if seen["n"] == fail_index:
                raise OSError("injected-replace-%d" % fail_index)
        return original(self, target, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "replace", wrapper)


@pytest.mark.parametrize("fail_index", [1, 2, 3])
@pytest.mark.parametrize("partial", [True, False])
def test_stage_failure_cleans_temps_and_replaces_nothing(
        tmp_path, pristine, monkeypatch, fail_index, partial):
    out = _copy(tmp_path, pristine)
    _sentinel(out)
    _patch_write_text(monkeypatch, out, fail_index, partial=partial)
    with pytest.raises(EA.AliasCatalogError) as excinfo:
        EA.build(out, [], [])
    for name in PRODUCTS:
        assert (out / name).read_bytes() == b"OLD-SENTINEL-BYTES", name
    assert not list(out.glob("*.tmp"))
    assert "尚未 replace 任何正式文件" in str(excinfo.value)
    assert "injected-stage-write-%d" % fail_index in str(excinfo.value.__cause__)


@pytest.mark.parametrize("fail_index", [1, 2, 3])
def test_replace_failure_reports_exactly_what_happened(
        tmp_path, pristine, monkeypatch, fail_index):
    out = _copy(tmp_path, pristine)
    _sentinel(out)
    _patch_replace(monkeypatch, out, fail_index)
    with pytest.raises(EA.AliasCatalogError) as excinfo:
        EA.build(out, [], [])
    message = str(excinfo.value)
    expected_replaced = list(PRODUCTS[:fail_index - 1])
    expected_missing = list(PRODUCTS[fail_index - 1:])
    for name in expected_replaced:
        assert (out / name).read_bytes() != b"OLD-SENTINEL-BYTES", name
    for name in expected_missing:
        assert (out / name).read_bytes() == b"OLD-SENTINEL-BYTES", name
    assert "已替换正式文件 %s" % expected_replaced in message
    assert "未替换正式文件 %s" % expected_missing in message
    assert not list(out.glob("*.tmp"))
    assert "injected-replace-%d" % fail_index in str(excinfo.value.__cause__)


# ================================================================ 固定测试向量

def test_family_id_derivation_is_pinned():
    """固定向量：身份由 `entity_type` + **排序后** members 的规范 JSON 派生。

    这里**显式重算**规范 JSON，不靠 `_dump` 自证；再加字面量锁住当前值。
    """
    payload = '{"entity_type":"course","members":["course:a","course:b"]}'
    expected = "family:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    assert expected == "family:389e56ad1a2d57802c941426bb02064b"
    assert EA._family_id({"entity_type": "course",
                          "members": ["course:b", "course:a"]}) == expected
    assert EA._family_id({"entity_type": "course",
                          "members": ["course:a", "course:b"]}) == expected
    assert EA._family_id({"entity_type": "course",
                          "members": ["course:a", "course:b"],
                          "canonical_label": "改过文案"}) == expected


def test_alias_id_derivation_is_pinned():
    row = {"alias": "计科", "entity_type": "major", "mapping_type": "one_to_one",
           "canonical": "major:2025:080901", "canonical_family": None,
           "scope": {"grade": "2025"}}
    payload = ('{"alias":"计科","entity_type":"major","mapping_type":"one_to_one",'
               '"scope":{"grade":"2025"},"target":"major:2025:080901"}')
    expected = "alias:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    assert expected == "alias:32225e152fb8d4fb70bc31057ee7d83b"
    assert EA._alias_id(row) == expected
    assert EA._alias_id(dict(row, scope={"grade": "2026"})) != expected


def test_alias_id_is_stable_under_review_fields():
    row = {"alias": "计科", "entity_type": "major", "mapping_type": "one_to_one",
           "canonical": "major:2025:080901", "canonical_family": None,
           "scope": {"grade": "2025"}, "reviewed_by": "a", "review_note": "x"}
    other = dict(row, reviewed_by="b", review_note="y")
    assert EA._alias_id(row) == EA._alias_id(other)