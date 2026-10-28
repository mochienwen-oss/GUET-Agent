# -*- coding: utf-8 -*-
"""Task 1：规范实体目录的生成与加载门。

纯函数 / 只读上游，不需要 Qdrant、LLM 或网络。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from entities import entity_catalog as EC  # noqa: E402

PLAN_DB = ROOT / "backend" / "data" / "plan_db.json"
CORPUS = ROOT / "_md"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """在两个**独立目录**各生成一次；顺带支撑逐字节一致的复现门。"""
    a = tmp_path_factory.mktemp("catalog_a")
    b = tmp_path_factory.mktemp("catalog_b")
    summary_a = EC.build(PLAN_DB, CORPUS, a)
    summary_b = EC.build(PLAN_DB, CORPUS, b)
    return {"a": a, "b": b, "summary_a": summary_a, "summary_b": summary_b}


def _read(d, name):
    return (d / name).read_text(encoding="utf-8")


def _json(d, name):
    return json.loads(_read(d, name))


# ---------------------------------------------------------------- 复现性

def test_two_independent_dirs_are_byte_identical(built):
    for name in ("entity_catalog.json", "entity_catalog.manifest.json",
                 "entity_catalog.collisions.json"):
        assert _read(built["a"], name) == _read(built["b"], name), name


def test_entity_ids_are_stable_across_generations(built):
    ca, cb = _json(built["a"], "entity_catalog.json"), _json(built["b"], "entity_catalog.json")
    for kind in EC.ENTITY_TYPES:
        assert ([e["entity_id"] for e in ca["entities"][kind]]
                == [e["entity_id"] for e in cb["entities"][kind]]), kind


# ---------------------------------------------------------------- 计数口径

def test_canonical_course_name_count_is_2116(built):
    assert _json(built["a"], "entity_catalog.json")["audit_metrics"]["distinct_canonical_course_names"] == 2116


def test_course_code_count_is_2410(built):
    assert _json(built["a"], "entity_catalog.json")["audit_metrics"]["distinct_course_codes"] == 2410


def test_course_input_rows_is_7212(built):
    assert _json(built["a"], "entity_catalog.json")["audit_metrics"]["course_input_rows"] == 7212


def test_join_key_metrics_are_audit_only(built):
    """2,103 只作审计指标，**不得**覆盖或合并正式课程名。"""
    cat = _json(built["a"], "entity_catalog.json")
    m = cat["audit_metrics"]
    assert m["join_key_distinct_names"] == 2103
    assert m["join_key_collision_groups"] == 13
    # 正式名计数仍是逐字精确去重的 2,116 —— 没有被归一化口径顶掉
    assert m["distinct_canonical_course_names"] == 2116
    names = {e["canonical_name"] for e in cat["entities"]["course"]}
    assert len(names) == 2116


def test_join_key_collisions_do_not_drop_course_records(built):
    """13 组归一化碰撞**不得**造成课程记录丢失。"""
    cat = _json(built["a"], "entity_catalog.json")
    assert len(cat["entities"]["course"]) == cat["audit_metrics"]["course_input_rows"]
    collisions = _json(built["a"], "entity_catalog.collisions.json")
    assert len(collisions["groups"]) == 13
    for group in collisions["groups"]:
        assert len(group["names"]) >= 2
        for name in group["names"]:
            assert name in {e["canonical_name"] for e in cat["entities"]["course"]}


# ---------------------------------------------------------------- 身份

def test_major_entity_id_is_grade_plus_major_code(built):
    majors = _json(built["a"], "entity_catalog.json")["entities"]["major"]
    assert len(majors) == 63
    for m in majors:
        assert m["entity_id"] == "major:%s:%s" % (m["grade"], m["major_code"])


def test_course_entity_id_carries_grade_major_code_row_hash(built):
    courses = _json(built["a"], "entity_catalog.json")["entities"]["course"]
    for c in courses:
        assert c["entity_id"] == "course:%s:%s:%s:%s" % (
            c["grade"], c["major_code"], c["course_code"], c["row_hash"])
        assert c["row_hash"]


def test_regulation_identity_does_not_use_qdrant_document_id(built):
    regs = _json(built["a"], "entity_catalog.json")["entities"]["regulation"]
    assert len(regs) == 28
    for r in regs:
        assert r["entity_id"].startswith("regulation:")
        assert "document_id" not in r
        assert r["canonical_title"]


# ---------------------------------------------------------------- 禁止项

def test_no_alias_or_family_entities_are_created(built):
    cat = _json(built["a"], "entity_catalog.json")
    assert cat["entities"]["institutional_term"] == []
    assert cat["course_family"] == []
    for kind in EC.ENTITY_TYPES:
        for e in cat["entities"][kind]:
            assert "alias" not in json.dumps(e, ensure_ascii=False)


def test_query_expansions_never_enters_the_catalog(built):
    """`query_expansions.json` 不是实体来源 —— 它的词一个都不许进目录。"""
    blob = _read(built["a"], "entity_catalog.json")
    assert "转专业" not in blob.split('"entities"')[0]  # 头部无路由主题词
    cat = _json(built["a"], "entity_catalog.json")
    names = {e["canonical_name"] for e in cat["entities"]["course"]}
    assert "换专业" not in names and "换个专业" not in names and "专业分流" not in names


def test_institutional_term_is_zero_not_guessed(built):
    m = _json(built["a"], "entity_catalog.manifest.json")
    assert m["counts"]["institutional_term"] == 0
    assert _json(built["a"], "entity_catalog.json")["entities"]["institutional_term"] == []


# ---------------------------------------------------------------- 加载门

def _built_dirs(tmp_path):
    out = tmp_path / "cat"
    EC.build(PLAN_DB, CORPUS, out)
    return out


def _resign(out, catalog):
    """改写 catalog 并**同步更新 manifest 的哈希** —— 让哈希门放行，
    从而把「实体级校验」单独隔离出来测。"""
    import hashlib
    text = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (out / "entity_catalog.json").write_text(text, encoding="utf-8")
    mpath = out / "entity_catalog.manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["catalog"]["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    mpath.write_text(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                     encoding="utf-8")


def test_clean_catalog_loads(tmp_path):
    out = _built_dirs(tmp_path)
    cat = EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                  plan_db_path=PLAN_DB, corpus_root=CORPUS)
    assert cat["schema_version"] == EC.SCHEMA_VERSION


def test_tampered_catalog_is_rejected(tmp_path):
    """只改 catalog、**不改 manifest** → 哈希门必须拦下。"""
    out = _built_dirs(tmp_path)
    p = out / "entity_catalog.json"
    cat = json.loads(p.read_text(encoding="utf-8"))
    cat["entities"]["major"].append({"entity_id": "major:1999:999999",
                                     "canonical_name": "伪造专业", "grade": "1999",
                                     "major_code": "999999", "source_file": "x.md"})
    p.write_text(json.dumps(cat, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 encoding="utf-8")
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                plan_db_path=PLAN_DB, corpus_root=CORPUS)


def test_upstream_change_with_same_filename_is_rejected(tmp_path):
    """上游**内容**变了（文件名不变）→ 旧 catalog 必须拒绝加载，不得静默沿用。"""
    db = tmp_path / "plan_db.json"
    shutil.copy(PLAN_DB, db)
    out = tmp_path / "cat"
    EC.build(db, CORPUS, out)
    EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
            plan_db_path=db, corpus_root=CORPUS)          # 先确认干净可加载
    raw = json.loads(db.read_text(encoding="utf-8"))
    raw["majors"][0]["major"] = raw["majors"][0]["major"] + "X"
    db.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                plan_db_path=db, corpus_root=CORPUS)


def test_source_fingerprint_mismatch_is_rejected(tmp_path):
    db = tmp_path / "plan_db.json"
    shutil.copy(PLAN_DB, db)
    out = tmp_path / "cat"
    EC.build(db, CORPUS, out)
    mpath = out / "entity_catalog.manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["upstream"]["plan_db"]["source_fingerprint"] = "0" * 64
    mpath.write_text(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                     encoding="utf-8")
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", mpath, plan_db_path=db, corpus_root=CORPUS)


def test_duplicate_entity_id_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    cat = json.loads((out / "entity_catalog.json").read_text(encoding="utf-8"))
    cat["entities"]["major"].append(dict(cat["entities"]["major"][0]))
    _resign(out, cat)
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                plan_db_path=PLAN_DB, corpus_root=CORPUS)


def test_missing_provenance_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    cat = json.loads((out / "entity_catalog.json").read_text(encoding="utf-8"))
    del cat["entities"]["course"][0]["source_file"]
    _resign(out, cat)
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                plan_db_path=PLAN_DB, corpus_root=CORPUS)


def test_unknown_entity_type_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    cat = json.loads((out / "entity_catalog.json").read_text(encoding="utf-8"))
    cat["entities"]["teacher"] = []
    _resign(out, cat)
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                plan_db_path=PLAN_DB, corpus_root=CORPUS)


def test_manifest_has_no_environment_fields(built):
    m = _read(built["a"], "entity_catalog.manifest.json")
    for bad in ("timestamp", "hostname", "username", "C:\\", "/root/", "/home/"):
        assert bad not in m, bad
    assert "logical_path" in m

# ================================================================ Task 1.1
# 严格加载收口：上游不可绕过、manifest 形状固定、三方自洽、ID 派生自洽。

def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")), encoding="utf-8")


def _manifest(out):
    return json.loads((out / "entity_catalog.manifest.json").read_text(encoding="utf-8"))


def _catalog(out):
    return json.loads((out / "entity_catalog.json").read_text(encoding="utf-8"))


def _load(out):
    return EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json",
                   plan_db_path=PLAN_DB, corpus_root=CORPUS)


def _reject(out):
    with pytest.raises(EC.CatalogError):
        _load(out)


# ---------------------------------------------------------- 1 上游不可绕过

def test_load_without_paths_succeeds_on_clean_catalog(tmp_path):
    out = _built_dirs(tmp_path)
    EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json")


@pytest.mark.parametrize("mutate", [
    lambda m: m["upstream"]["plan_db"].__setitem__("source_fingerprint", "0" * 64),
    lambda m: m["upstream"]["plan_db"].__setitem__("sha256", "0" * 64),
    lambda m: m["upstream"]["corpus"].__setitem__("tree_sha256", "0" * 64),
])
def test_load_without_paths_still_checks_canonical_upstream(tmp_path, mutate):
    """**不传上游路径也必须校验** —— 不得存在「调用者不传就不校验」的绕过语义。"""
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    mutate(m)
    _write(out / "entity_catalog.manifest.json", m)
    with pytest.raises(EC.CatalogError):
        EC.load(out / "entity_catalog.json", out / "entity_catalog.manifest.json")


def test_no_escape_hatch_parameter_exists():
    import inspect
    params = set(inspect.signature(EC.load).parameters)
    assert not (params & {"skip_upstream", "unsafe", "force", "trusted"})


# ---------------------------------------------------------- 2 manifest 形状固定

def test_manifest_missing_field_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    del m["counts"]
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


def test_manifest_extra_field_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m["extra_probe"] = 1
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


def test_manifest_bad_artifact_kind_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m["artifact_kind"] = "something_else"
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


@pytest.mark.parametrize("field", ["schema_version", "generator_version"])
def test_manifest_bad_version_is_rejected(tmp_path, field):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m[field] = "nope/9"
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


@pytest.mark.parametrize("key", ["catalog", "audit"])
def test_manifest_exists_false_is_rejected(tmp_path, key):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m[key]["exists"] = False
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


@pytest.mark.parametrize("key,bad", [
    ("catalog", "C:/tmp/entity_catalog.json"),
    ("catalog", "entity_catalog_v2.json"),
    ("audit", "/abs/entity_catalog.collisions.json"),
    ("audit", "other.json"),
])
def test_manifest_arbitrary_logical_path_is_rejected(tmp_path, key, bad):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m[key]["logical_path"] = bad
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


# ---------------------------------------------------------- 3 三方自洽

@pytest.mark.parametrize("field,value", [
    ("course_input_rows", 1), ("distinct_canonical_course_names", 1),
    ("distinct_course_codes", 1), ("join_key_distinct_names", 1),
    ("join_key_collision_groups", 1),
])
def test_manifest_metric_tamper_is_rejected(tmp_path, field, value):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m[field] = value
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


def test_manifest_counts_tamper_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m["counts"]["major"] = 62
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


def test_manifest_rows_tamper_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m["catalog"]["rows"] = 1
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


def test_manifest_audit_sha_tamper_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    m["audit"]["sha256"] = "0" * 64
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


def test_collisions_sidecar_tamper_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    side = json.loads((out / "entity_catalog.collisions.json").read_text(encoding="utf-8"))
    side["groups"].append({"join_key": "假碰撞", "names": ["甲课", "乙课"]})
    _write(out / "entity_catalog.collisions.json", side)
    _reject(out)


def test_catalog_audit_metrics_tamper_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    cat = _catalog(out)
    cat["audit_metrics"]["join_key_collision_groups"] = 1
    _resign(out, cat)
    _reject(out)


# ---------------------------------------------------------- 4 ID 派生自洽

@pytest.mark.parametrize("kind", ["major", "course", "regulation"])
def test_unique_but_wrong_entity_id_is_rejected(tmp_path, kind):
    """「唯一」不够 —— ID 必须由行内身份字段**正确派生**。"""
    out = _built_dirs(tmp_path)
    cat = _catalog(out)
    cat["entities"][kind][0]["entity_id"] = "wrong:but:unique:%s" % kind
    cat["entities"][kind].sort(key=lambda e: e["entity_id"])
    _resign(out, cat)
    _reject(out)


@pytest.mark.parametrize("kind,field", [
    ("major", "grade"), ("major", "major_code"),
    ("course", "course_code"), ("course", "row_hash"),
    ("regulation", "canonical_title"), ("regulation", "relpath"),
])
def test_blank_identity_field_is_rejected(tmp_path, kind, field):
    out = _built_dirs(tmp_path)
    cat = _catalog(out)
    cat["entities"][kind][0][field] = ""
    _resign(out, cat)
    _reject(out)


def test_regulation_relpath_escaping_corpus_is_rejected(tmp_path):
    out = _built_dirs(tmp_path)
    cat = _catalog(out)
    cat["entities"]["regulation"][0]["relpath"] = "../../etc/passwd"
    _resign(out, cat)
    _reject(out)


def test_regulation_file_sha_must_match_upstream(tmp_path):
    out = _built_dirs(tmp_path)
    cat = _catalog(out)
    cat["entities"]["regulation"][0]["sha256"] = "0" * 64
    _resign(out, cat)
    _reject(out)


# ---------------------------------------------------------- 5 build 先验证后落盘

def test_build_static_failure_leaves_no_half_products(tmp_path):
    """静态验证失败 → **不创建**任何正式文件、**不留** .tmp、不靠 load 事后拒绝。"""
    raw = json.loads(PLAN_DB.read_text(encoding="utf-8"))
    del raw["majors"][0]["courses"][0]["src"]["row_hash"]      # 身份字段缺失
    db = tmp_path / "plan_db.json"
    _write(db, raw)
    out = tmp_path / "cat"
    with pytest.raises(Exception):
        EC.build(db, CORPUS, out)
    assert not out.exists() or list(out.iterdir()) == [], \
        "静态验证失败却留下了半成品：%s" % (list(out.iterdir()) if out.exists() else [])


def test_build_rejects_duplicate_identity(tmp_path):
    raw = json.loads(PLAN_DB.read_text(encoding="utf-8"))
    raw["majors"][0]["courses"].append(dict(raw["majors"][0]["courses"][0]))
    db = tmp_path / "plan_db.json"
    _write(db, raw)
    out = tmp_path / "cat"
    with pytest.raises(Exception):
        EC.build(db, CORPUS, out)
    assert not out.exists() or list(out.iterdir()) == []

# ================================================================ Task 1.1.1
# 写盘失败边界（stage / replace）与嵌套契约收口。
#
# ★ 注入只拦本次 build 自己的 stage 临时文件与正式文件 replace，
#   不误伤 plan_db 读取、语料读取或 pytest 自身的临时文件。

NAMES = (EC.CATALOG_NAME, EC.AUDIT_NAME, EC.MANIFEST_NAME)
CATALOG_FIELDS = ("schema_version", "generator_version", "entities",
                  "course_family", "audit_metrics")
METRIC_KEYS = ("course_input_rows", "distinct_canonical_course_names",
               "distinct_course_codes", "join_key_distinct_names",
               "join_key_collision_groups")
COUNTS_KEYS = ("major", "course", "regulation", "institutional_term", "course_family")
CATALOG_BLOCK_KEYS = ("logical_path", "exists", "rows", "sha256")
AUDIT_BLOCK_KEYS = ("logical_path", "exists", "sha256")
PLAN_DB_KEYS = ("logical_path", "sha256", "source_fingerprint", "docs")
CORPUS_KEYS = ("logical_path", "tree_sha256", "files", "regulation_files")
SIDECAR_FIELDS = ("schema_version", "metric", "note", "groups")
SIDECAR_GROUP_KEYS = ("join_key", "names")


def _seed_old_bytes(out):
    """三份正式文件写成**可区分的旧哨兵字节** —— 不得拿相同字节制造假绿。"""
    old = {}
    for name in NAMES:
        old[name] = b"OLD-SENTINEL-BYTES\n" + name.encode("utf-8") + b"\n"
        (out / name).write_bytes(old[name])
    return old


def _tmp_names(out):
    return sorted(p.name for p in out.iterdir() if p.name.endswith(".tmp"))


def _patch_write_text(monkeypatch, out_dir, fail_index, *, partial):
    """第 fail_index 次 stage 临时文件写入失败；partial=True 时已部分落盘。"""
    import pathlib
    orig = pathlib.Path.write_text
    seen = {"n": 0}
    out_dir = Path(out_dir)

    def wrapper(self, data, *args, **kwargs):
        p = Path(self)
        if p.parent == out_dir and p.name.endswith(".tmp") and p.name[:-4] in NAMES:
            seen["n"] += 1
            if seen["n"] == fail_index:
                if partial:
                    orig(p, data[: max(1, len(data) // 2)], *args, **kwargs)
                raise OSError("injected-stage-write-%d" % fail_index)
        return orig(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", wrapper)


def _patch_replace(monkeypatch, out_dir, fail_index):
    """第 fail_index 次正式文件 replace 失败（固定顺序 catalog → audit → manifest）。"""
    import pathlib
    orig = pathlib.Path.replace
    seen = {"n": 0}
    out_dir = Path(out_dir)

    def wrapper(self, target, *args, **kwargs):
        t = Path(target)
        if t.parent == out_dir and t.name in NAMES:
            seen["n"] += 1
            if seen["n"] == fail_index:
                raise OSError("injected-replace-%d" % fail_index)
        return orig(self, target, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "replace", wrapper)


# ---------------------------------------------------------- A stage 失败三连

@pytest.mark.parametrize("fail_index", [1, 2, 3])
def test_stage_failure_cleans_partial_tmp_and_never_claims_replacement(
        tmp_path, monkeypatch, fail_index):
    """stage 第 N 次写入失败：正式文件全是旧字节、无 .tmp 残留、文本不谎报已替换。"""
    out = _built_dirs(tmp_path)
    old = _seed_old_bytes(out)
    _patch_write_text(monkeypatch, out, fail_index, partial=True)

    with pytest.raises(EC.CatalogError) as excinfo:
        EC.build(PLAN_DB, CORPUS, out)
    msg = str(excinfo.value)

    for name in NAMES:
        assert (out / name).read_bytes() == old[name], name
    assert _tmp_names(out) == []
    assert "尚未 replace 任何正式文件" in msg
    assert "已替换正式文件" not in msg
    cleaned = sorted(NAMES[i] + ".tmp" for i in range(fail_index))
    assert "已清理临时文件 %s" % cleaned in msg
    assert "未清理临时文件 []" in msg
    assert isinstance(excinfo.value.__cause__, OSError)
    assert "injected-stage-write-%d" % fail_index in str(excinfo.value.__cause__)


@pytest.mark.parametrize("fail_index", [1, 2, 3])
def test_stage_failure_without_partial_write_leaves_no_tmp(
        tmp_path, monkeypatch, fail_index):
    """写入完全没落盘就抛错：同样不得残留 .tmp、不得替换正式文件。"""
    out = _built_dirs(tmp_path)
    old = _seed_old_bytes(out)
    _patch_write_text(monkeypatch, out, fail_index, partial=False)

    with pytest.raises(EC.CatalogError) as excinfo:
        EC.build(PLAN_DB, CORPUS, out)

    for name in NAMES:
        assert (out / name).read_bytes() == old[name], name
    assert _tmp_names(out) == []
    assert isinstance(excinfo.value.__cause__, OSError)


# ---------------------------------------------------------- B replace 失败三连

@pytest.mark.parametrize("fail_index", [1, 2, 3])
def test_replace_failure_state_table_is_exact(tmp_path, monkeypatch, fail_index):
    """replace 第 N 次失败：已替换的是新版、未替换的是旧版、.tmp 全清、文本如实。"""
    out = _built_dirs(tmp_path)
    old = _seed_old_bytes(out)
    _patch_replace(monkeypatch, out, fail_index)

    with pytest.raises(EC.CatalogError) as excinfo:
        EC.build(PLAN_DB, CORPUS, out)
    msg = str(excinfo.value)

    replaced = list(NAMES[: fail_index - 1])
    remaining = list(NAMES[fail_index - 1:])
    for name in replaced:
        assert (out / name).read_bytes() != old[name], name
    for name in remaining:
        assert (out / name).read_bytes() == old[name], name
    assert _tmp_names(out) == []
    assert "已替换正式文件 %s" % replaced in msg
    assert "未替换正式文件 %s" % remaining in msg
    cleared = sorted(n + ".tmp" for n in remaining)
    assert "已清理未替换的临时文件 %s" % cleared in msg
    assert isinstance(excinfo.value.__cause__, OSError)
    assert "injected-replace-%d" % fail_index in str(excinfo.value.__cause__)


# ---------------------------------------------------------- C manifest 嵌套形状

def _manifest_reject(tmp_path, mutate):
    out = _built_dirs(tmp_path)
    m = _manifest(out)
    mutate(m)
    _write(out / "entity_catalog.manifest.json", m)
    _reject(out)


@pytest.mark.parametrize("key", COUNTS_KEYS)
def test_manifest_counts_missing_key_is_rejected(tmp_path, key):
    _manifest_reject(tmp_path, lambda m: m["counts"].pop(key))


def test_manifest_counts_extra_key_is_rejected(tmp_path):
    _manifest_reject(tmp_path, lambda m: m["counts"].__setitem__("bogus", 1))


def test_manifest_counts_non_object_is_rejected(tmp_path):
    _manifest_reject(tmp_path, lambda m: m.__setitem__("counts", ["major"]))


@pytest.mark.parametrize("key", COUNTS_KEYS)
def test_manifest_counts_wrong_type_is_rejected(tmp_path, key):
    _manifest_reject(tmp_path, lambda m: m["counts"].__setitem__(key, "0"))


@pytest.mark.parametrize("field", CATALOG_BLOCK_KEYS)
def test_manifest_catalog_block_missing_field_is_rejected(tmp_path, field):
    _manifest_reject(tmp_path, lambda m: m["catalog"].pop(field))


def test_manifest_catalog_block_extra_field_is_rejected(tmp_path):
    _manifest_reject(tmp_path, lambda m: m["catalog"].__setitem__("extra", 1))


@pytest.mark.parametrize("field", AUDIT_BLOCK_KEYS)
def test_manifest_audit_block_missing_field_is_rejected(tmp_path, field):
    _manifest_reject(tmp_path, lambda m: m["audit"].pop(field))


def test_manifest_audit_block_extra_field_is_rejected(tmp_path):
    _manifest_reject(tmp_path, lambda m: m["audit"].__setitem__("rows", 1))


@pytest.mark.parametrize("field,bad", [("rows", "7303"), ("rows", None),
                                       ("exists", 1), ("exists", "true"),
                                       ("sha256", "not-a-sha")])
def test_manifest_catalog_block_wrong_type_is_rejected(tmp_path, field, bad):
    _manifest_reject(tmp_path, lambda m: m["catalog"].__setitem__(field, bad))


@pytest.mark.parametrize("bad", ["A" * 64, "0" * 63, "g" * 64, "", 123])
def test_manifest_audit_sha_must_be_lowercase_hex(tmp_path, bad):
    _manifest_reject(tmp_path, lambda m: m["audit"].__setitem__("sha256", bad))


@pytest.mark.parametrize("key", ("catalog", "audit"))
def test_manifest_nested_block_non_object_is_rejected(tmp_path, key):
    _manifest_reject(tmp_path, lambda m: m.__setitem__(key, []))


@pytest.mark.parametrize("field", PLAN_DB_KEYS)
def test_manifest_upstream_plan_db_missing_field_is_rejected(tmp_path, field):
    _manifest_reject(tmp_path, lambda m: m["upstream"]["plan_db"].pop(field))


@pytest.mark.parametrize("field", CORPUS_KEYS)
def test_manifest_upstream_corpus_missing_field_is_rejected(tmp_path, field):
    _manifest_reject(tmp_path, lambda m: m["upstream"]["corpus"].pop(field))


def test_manifest_upstream_missing_block_is_rejected(tmp_path):
    _manifest_reject(tmp_path, lambda m: m["upstream"].pop("corpus"))


def test_manifest_upstream_plan_db_non_object_is_rejected(tmp_path):
    _manifest_reject(tmp_path, lambda m: m["upstream"].__setitem__("plan_db", "x"))


@pytest.mark.parametrize("field,bad", [("docs", "63"), ("docs", None),
                                       ("sha256", "not-a-sha"),
                                       ("source_fingerprint", "short")])
def test_manifest_upstream_plan_db_wrong_type_is_rejected(tmp_path, field, bad):
    _manifest_reject(tmp_path, lambda m: m["upstream"]["plan_db"].__setitem__(field, bad))


@pytest.mark.parametrize("field,bad", [("files", "91"), ("regulation_files", None),
                                       ("tree_sha256", "not-a-sha")])
def test_manifest_upstream_corpus_wrong_type_is_rejected(tmp_path, field, bad):
    _manifest_reject(tmp_path, lambda m: m["upstream"]["corpus"].__setitem__(field, bad))


@pytest.mark.parametrize("bad", ["_md", "backend/data/other.json", "/abs/plan_db.json"])
def test_manifest_upstream_plan_db_logical_path_is_fixed(tmp_path, bad):
    _manifest_reject(tmp_path,
                     lambda m: m["upstream"]["plan_db"].__setitem__("logical_path", bad))


@pytest.mark.parametrize("bad", ["backend/data", "规章制度", "/abs/_md", "_md2"])
def test_manifest_upstream_corpus_logical_path_is_fixed(tmp_path, bad):
    _manifest_reject(tmp_path,
                     lambda m: m["upstream"]["corpus"].__setitem__("logical_path", bad))


# ---------------------------------------------------------- D catalog 自身契约

def _catalog_reject(tmp_path, mutate):
    out = _built_dirs(tmp_path)
    cat = _catalog(out)
    mutate(cat)
    _resign(out, cat)
    _reject(out)


@pytest.mark.parametrize("field", CATALOG_FIELDS)
def test_catalog_missing_top_level_field_is_rejected(tmp_path, field):
    _catalog_reject(tmp_path, lambda c: c.pop(field))


def test_catalog_extra_top_level_field_is_rejected(tmp_path):
    _catalog_reject(tmp_path, lambda c: c.__setitem__("bogus", 1))


@pytest.mark.parametrize("bad", ["entity_catalog/9.9.9", "", None, 1])
def test_catalog_generator_version_tamper_is_rejected(tmp_path, bad):
    _catalog_reject(tmp_path, lambda c: c.__setitem__("generator_version", bad))


def test_catalog_schema_version_tamper_is_rejected(tmp_path):
    _catalog_reject(tmp_path, lambda c: c.__setitem__("schema_version", "entity_catalog/2"))


def test_catalog_course_family_must_stay_empty(tmp_path):
    _catalog_reject(tmp_path, lambda c: c.__setitem__("course_family", [{"family_id": "x"}]))


def test_catalog_course_family_must_be_list(tmp_path):
    _catalog_reject(tmp_path, lambda c: c.__setitem__("course_family", {}))


@pytest.mark.parametrize("key", METRIC_KEYS)
def test_catalog_audit_metrics_missing_key_is_rejected(tmp_path, key):
    _catalog_reject(tmp_path, lambda c: c["audit_metrics"].pop(key))


def test_catalog_audit_metrics_extra_key_is_rejected(tmp_path):
    _catalog_reject(tmp_path, lambda c: c["audit_metrics"].__setitem__("bogus", 1))


@pytest.mark.parametrize("bad", ["7212", None, True, 1.0])
def test_catalog_audit_metrics_wrong_type_is_rejected(tmp_path, bad):
    _catalog_reject(tmp_path,
                    lambda c: c["audit_metrics"].__setitem__("course_input_rows", bad))


def test_catalog_audit_metrics_non_object_is_rejected(tmp_path):
    _catalog_reject(tmp_path, lambda c: c.__setitem__("audit_metrics", ["x"]))


@pytest.mark.parametrize("kind", EC.ENTITY_TYPES)
def test_catalog_entity_type_must_be_list(tmp_path, kind):
    _catalog_reject(tmp_path, lambda c: c["entities"].__setitem__(kind, {"not": "a list"}))


@pytest.mark.parametrize("bad", [1, "row", None, ["nested"]])
def test_catalog_entity_row_must_be_object(tmp_path, bad):
    def mutate(c):
        c["entities"]["major"] = [bad] + list(c["entities"]["major"])
    _catalog_reject(tmp_path, mutate)


# ---------------------------------------------------------- E sidecar 契约

def _sidecar(out):
    return json.loads((out / EC.AUDIT_NAME).read_text(encoding="utf-8"))


def _sidecar_resign(out, side):
    import hashlib
    text = json.dumps(side, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (out / EC.AUDIT_NAME).write_text(text, encoding="utf-8")
    m = _manifest(out)
    m["audit"]["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _write(out / EC.MANIFEST_NAME, m)


def _sidecar_reject(tmp_path, mutate):
    out = _built_dirs(tmp_path)
    side = _sidecar(out)
    mutate(side)
    _sidecar_resign(out, side)
    _reject(out)


@pytest.mark.parametrize("field", SIDECAR_FIELDS)
def test_sidecar_missing_field_is_rejected(tmp_path, field):
    _sidecar_reject(tmp_path, lambda s: s.pop(field))


def test_sidecar_extra_field_is_rejected(tmp_path):
    _sidecar_reject(tmp_path, lambda s: s.__setitem__("bogus", 1))


def test_sidecar_schema_version_tamper_is_rejected(tmp_path):
    _sidecar_reject(tmp_path, lambda s: s.__setitem__("schema_version", "entity_catalog/2"))


def test_sidecar_metric_tamper_is_rejected(tmp_path):
    _sidecar_reject(tmp_path, lambda s: s.__setitem__("metric", "something_else"))


def test_sidecar_note_must_be_non_empty_string(tmp_path):
    _sidecar_reject(tmp_path, lambda s: s.__setitem__("note", ""))


def test_sidecar_groups_must_be_list(tmp_path):
    _sidecar_reject(tmp_path, lambda s: s.__setitem__("groups", {"join_key": "x"}))


@pytest.mark.parametrize("field", SIDECAR_GROUP_KEYS)
def test_sidecar_group_missing_field_is_rejected(tmp_path, field):
    _sidecar_reject(tmp_path, lambda s: s["groups"][0].pop(field))


def test_sidecar_group_extra_field_is_rejected(tmp_path):
    _sidecar_reject(tmp_path, lambda s: s["groups"][0].__setitem__("extra", 1))


def test_sidecar_duplicate_join_key_is_rejected(tmp_path):
    def mutate(s):
        s["groups"][1]["join_key"] = s["groups"][0]["join_key"]
    _sidecar_reject(tmp_path, mutate)


def test_sidecar_names_internal_duplicate_is_rejected(tmp_path):
    def mutate(s):
        g = s["groups"][0]
        g["names"] = [g["names"][0], g["names"][1], g["names"][0]]
    _sidecar_reject(tmp_path, mutate)


def test_sidecar_name_in_two_groups_is_rejected(tmp_path):
    def mutate(s):
        first = s["groups"][0]["names"][0]
        s["groups"][1]["names"] = list(s["groups"][1]["names"]) + [first]
    _sidecar_reject(tmp_path, mutate)


def test_sidecar_join_key_must_match_derived_join_key(tmp_path):
    """组内名称仍在 course 实体里，但 group.join_key 被改成错误值 → 必须拒绝。"""
    def mutate(s):
        s["groups"][0]["join_key"] = "伪造的碰撞键"
    _sidecar_reject(tmp_path, mutate)


def test_sidecar_group_members_must_share_one_join_key(tmp_path):
    """组内两个**真实**课程名的正式 join_key 不同 → 必须拒绝（复用正式 join_key）。"""
    def mutate(s):
        s["groups"][0]["names"] = [s["groups"][0]["names"][0],
                                   s["groups"][1]["names"][0]]
    _sidecar_reject(tmp_path, mutate)


@pytest.mark.parametrize("bad", [123, None, ["nested"]])
def test_sidecar_names_must_be_strings(tmp_path, bad):
    def mutate(s):
        g = s["groups"][0]
        g["names"] = [g["names"][0], bad]
    _sidecar_reject(tmp_path, mutate)


def test_sidecar_missing_file_is_catalog_error(tmp_path):
    """sidecar 不存在 → CatalogError，不得裸露 FileNotFoundError。"""
    out = _built_dirs(tmp_path)
    (out / EC.AUDIT_NAME).unlink()
    with pytest.raises(EC.CatalogError):
        _load(out)




# ---------------------------------------------------------- F 畸形产物 / 上游缺失
# C 节明令「不得裸露 KeyError / TypeError / FileNotFoundError」；
# 加载器的既定契约是「任一门不通过即抛 CatalogError」——把畸形容器一并收口。

def test_missing_plan_db_path_is_catalog_error(tmp_path):
    """上游 plan_db 路径不存在 → CatalogError，不得裸露 FileNotFoundError。"""
    out = _built_dirs(tmp_path)
    with pytest.raises(EC.CatalogError):
        EC.load(out / EC.CATALOG_NAME, out / EC.MANIFEST_NAME,
                plan_db_path=tmp_path / "no_such_plan_db.json", corpus_root=CORPUS)


def test_missing_corpus_root_is_catalog_error(tmp_path):
    """上游语料根不存在 → CatalogError，不得裸露 FileNotFoundError。"""
    out = _built_dirs(tmp_path)
    with pytest.raises(EC.CatalogError):
        EC.load(out / EC.CATALOG_NAME, out / EC.MANIFEST_NAME,
                plan_db_path=PLAN_DB, corpus_root=tmp_path / "no_such_corpus")


def test_malformed_plan_db_is_catalog_error(tmp_path):
    out = _built_dirs(tmp_path)
    bad = tmp_path / "broken_plan_db.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(EC.CatalogError):
        EC.load(out / EC.CATALOG_NAME, out / EC.MANIFEST_NAME,
                plan_db_path=bad, corpus_root=CORPUS)


def test_malformed_catalog_json_is_catalog_error(tmp_path):
    """catalog 不是合法 JSON（哈希已重签放行）→ CatalogError，不得裸露 json 异常。"""
    out = _built_dirs(tmp_path)
    text = "{not valid json"
    (out / EC.CATALOG_NAME).write_text(text, encoding="utf-8")
    m = _manifest(out)
    import hashlib
    m["catalog"]["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _write(out / EC.MANIFEST_NAME, m)
    with pytest.raises(EC.CatalogError):
        _load(out)


def test_malformed_manifest_json_is_catalog_error(tmp_path):
    out = _built_dirs(tmp_path)
    (out / EC.MANIFEST_NAME).write_text("{not valid json", encoding="utf-8")
    with pytest.raises(EC.CatalogError):
        _load(out)


def test_malformed_sidecar_json_is_catalog_error(tmp_path):
    out = _built_dirs(tmp_path)
    text = "{not valid json"
    (out / EC.AUDIT_NAME).write_text(text, encoding="utf-8")
    m = _manifest(out)
    import hashlib
    m["audit"]["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _write(out / EC.MANIFEST_NAME, m)
    with pytest.raises(EC.CatalogError):
        _load(out)


def test_official_artifacts_equal_fresh_build(built):
    """正式产物必须能从上游确定性重算 —— 逐字节相同（不放宽任何门）。"""
    official = ROOT / "backend" / "data"
    for name in NAMES:
        assert _read(built["a"], name) == (official / name).read_text(
            encoding="utf-8"), name

