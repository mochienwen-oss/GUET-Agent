# -*- coding: utf-8 -*-
"""规范实体目录 —— 由上游事实源**确定性生成**的只读目录 + manifest。

上游事实源（本模块只读，绝不改写）：

  · `backend/data/plan_db.json`   专业与课程事实
  · `final-md/规章制度/**/*.md`   当前清洗后的规章文档（身份来自**稳定规范标题**）

本模块只做两件事：**确定性生成** 与 **严格加载**。它**不**接运行图、**不**做别名、
**不**推断课程族、**不**使用 Qdrant `document_id`。

★ 只读产物不是第二份事实：它必须能从上游重算，且上游一变就拒绝加载。
"""
from __future__ import annotations

import hashlib
import re
import json
from pathlib import Path

from core.paths import BACKEND_ROOT, PROJECT_ROOT

SCHEMA_VERSION = "entity_catalog/1"
GENERATOR_VERSION = "entity_catalog/1.0.0"

#: manifest 的固定字段形状 —— 多一个都不许（防环境字段混入导致不可复现）
MANIFEST_FIELDS = (
    "artifact_kind", "schema_version", "generator_version",
    "catalog", "audit", "upstream", "counts",
    "course_input_rows", "distinct_canonical_course_names", "distinct_course_codes",
    "join_key_distinct_names", "join_key_collision_groups",
)

ENTITY_TYPES = ("major", "course", "regulation", "institutional_term")

#: catalog 的固定顶层字段形状 —— 多一个都不许（防第二份事实混入）
CATALOG_FIELDS = ("schema_version", "generator_version", "entities",
                  "course_family", "audit_metrics")

#: 五类实体计数键 —— manifest.counts 缺一即拒、多一即拒
COUNTS_KEYS = ("major", "course", "regulation", "institutional_term", "course_family")

#: `audit_metrics` 的固定键 —— 计数口径不得被替换或增补
METRIC_KEYS = ("course_input_rows", "distinct_canonical_course_names",
               "distinct_course_codes", "join_key_distinct_names",
               "join_key_collision_groups")

#: manifest 各嵌套块的固定字段形状
CATALOG_BLOCK_FIELDS = ("logical_path", "exists", "rows", "sha256")
AUDIT_BLOCK_FIELDS = ("logical_path", "exists", "sha256")
UPSTREAM_FIELDS = ("plan_db", "corpus")
PLAN_DB_BLOCK_FIELDS = ("logical_path", "sha256", "source_fingerprint", "docs")
CORPUS_BLOCK_FIELDS = ("logical_path", "tree_sha256", "files", "regulation_files")

#: 碰撞审计 sidecar 的固定形状 —— 它**只是审计件**，不得成为第二份事实
SIDECAR_FIELDS = ("schema_version", "metric", "note", "groups")
SIDECAR_GROUP_FIELDS = ("join_key", "names")
SIDECAR_METRIC = "join_key_collisions"

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class CatalogError(RuntimeError):
    """目录非法或不一致。**一律拒绝加载**，不得降级、不得回退旧目录。"""


def _dump(value) -> str:
    """规范化 JSON —— 确定性排序 + 紧凑分隔符，保证逐字节可复现。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_DOC_NO = re.compile(r"([一-鿿]{1,6}〔\d{4}〕\d+号)")


def _doc_no(title: str) -> str:
    """从规范标题里取文号；取不到就留空 —— **不猜**。"""
    found = _DOC_NO.search(title)
    return found.group(1) if found else ""


def _major_entity_id(grade, major_code) -> str:
    return "major:%s:%s" % (grade, major_code)


def _course_entity_id(grade, major_code, course_code, row_hash) -> str:
    return "course:%s:%s:%s:%s" % (grade, major_code, course_code, row_hash)


def _regulation_entity_id(canonical_title: str) -> str:
    return "regulation:" + hashlib.sha256(canonical_title.encode("utf-8")).hexdigest()[:32]


def _major_facts(plan_db) -> list[dict]:
    out = []
    for major in plan_db["majors"]:
        out.append({
            "entity_id": _major_entity_id(major.get("grade"), major.get("code")),
            "canonical_name": major.get("major"),
            "grade": major.get("grade"),
            "major_code": major.get("code"),
            "college": major.get("college"),
            "source_file": major.get("file"),
        })
    return out


def _course_facts(plan_db) -> list[dict]:
    out = []
    for major in plan_db["majors"]:
        grade, major_code = major.get("grade"), major.get("code")
        for course in major.get("courses") or []:
            src = course.get("src") or {}
            out.append({
                "entity_id": _course_entity_id(grade, major_code,
                                               course.get("code"), src.get("row_hash")),
                "canonical_name": course.get("name"),
                "grade": grade,
                "major_code": major_code,
                "course_code": course.get("code"),
                "row_hash": src.get("row_hash"),
                "source_file": src.get("file") or major.get("file"),
                "source_line": src.get("line") if src.get("line") is not None
                else course.get("src_line"),
            })
    return out


def _regulation_facts(corpus_root) -> list[dict]:
    """规章目录及根目录规章；不移动正文，身份与 Qdrant document_id 无关。"""
    reg_root = Path(corpus_root) / "规章制度"
    out = []
    paths = list(reg_root.rglob("*.md")) + list(Path(corpus_root).glob("*.md"))
    titles = {}
    for path in sorted(paths):
        title = path.stem
        digest = sha256_file(path)
        if title in titles:
            if titles[title] != digest:
                raise CatalogError("同名规章正文不一致，未自动合并：%s" % title)
            continue
        titles[title] = digest
        out.append({
            "entity_id": _regulation_entity_id(title),
            "canonical_title": title,
            "doc_no": _doc_no(title),
            "relpath": path.relative_to(Path(corpus_root)).as_posix(),
            "sha256": digest,
        })
    return out


def _audit_metrics(courses: list[dict], corpus_root) -> dict:
    """名称计数口径 —— 逐字精确去重为准；`join_key` **只作碰撞审计**。"""
    from agent.tools.query_course.scripts.normalize import join_key

    names = [c["canonical_name"] for c in courses]
    groups: dict[str, list[str]] = {}
    for name in sorted(set(names)):
        groups.setdefault(join_key(name), []).append(name)
    collisions = {key: value for key, value in groups.items() if len(value) > 1}
    return {
        "course_input_rows": len(courses),
        "distinct_canonical_course_names": len(set(names)),
        "distinct_course_codes": len({c["course_code"] for c in courses}),
        "join_key_distinct_names": len(groups),
        "join_key_collision_groups": len(collisions),
        "_collisions": collisions,
    }


def build(plan_db_path, corpus_root, out_dir) -> dict:
    """确定性生成 catalog / manifest / 碰撞审计 sidecar，返回摘要。

    ★ 只读上游；不同步、不修补、不推断课程族。
    """
    plan_db_path = Path(plan_db_path)
    corpus_root = Path(corpus_root)
    out_dir = Path(out_dir)

    plan_db = _load_json(plan_db_path, "上游 plan_db")
    majors = _major_facts(plan_db)
    courses = _course_facts(plan_db)
    regulations = _regulation_facts(corpus_root)
    metrics = _audit_metrics(courses, corpus_root)
    collisions = metrics.pop("_collisions")

    catalog = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "entities": {
            "major": sorted(majors, key=lambda e: e["entity_id"]),
            "course": sorted(courses, key=lambda e: e["entity_id"]),
            "regulation": sorted(regulations, key=lambda e: e["entity_id"]),
            "institutional_term": [],
        },
        "course_family": [],
        "audit_metrics": metrics,
    }

    audit = {
        "schema_version": SCHEMA_VERSION,
        "metric": "join_key_collisions",
        "note": "仅审计指标：不得据此合并、改写或删除规范课程名",
        "groups": [{"join_key": key, "names": value}
                   for key, value in sorted(collisions.items())],
    }

    catalog_text = _dump(catalog)
    audit_text = _dump(audit)
    corpus_files = sorted(p.relative_to(corpus_root).as_posix()
                          for p in corpus_root.rglob("*.md"))
    corpus_digest, _ = _corpus_tree_sha256(corpus_root)

    manifest = {
        "artifact_kind": "entity_catalog_manifest",
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "catalog": {
            "logical_path": CATALOG_LOGICAL,
            "exists": True,
            "rows": sum(len(v) for v in catalog["entities"].values()),
            "sha256": hashlib.sha256(catalog_text.encode("utf-8")).hexdigest(),
        },
        "audit": {
            "logical_path": AUDIT_LOGICAL,
            "exists": True,
            "sha256": hashlib.sha256(audit_text.encode("utf-8")).hexdigest(),
        },
        "upstream": {
            "plan_db": {
                "logical_path": "backend/data/plan_db.json",
                "sha256": sha256_file(plan_db_path),
                "source_fingerprint": (plan_db.get("meta") or {}).get("source_fingerprint"),
                "docs": (plan_db.get("meta") or {}).get("docs"),
            },
            "corpus": {
                "logical_path": UPSTREAM_CORPUS_LOGICAL,
                "tree_sha256": corpus_digest,
                "files": len(corpus_files),
                "regulation_files": len(regulations),
            },
        },
        "counts": {
            "major": len(majors),
            "course": len(courses),
            "regulation": len(regulations),
            "institutional_term": 0,
            "course_family": 0,
        },
        "course_input_rows": metrics["course_input_rows"],
        "distinct_canonical_course_names": metrics["distinct_canonical_course_names"],
        "distinct_course_codes": metrics["distinct_course_codes"],
        "join_key_distinct_names": metrics["join_key_distinct_names"],
        "join_key_collision_groups": metrics["join_key_collision_groups"],
    }

    # ★★ **先验证、后落盘**：任何静态失败都不得留下半成品。
    #    （在写任何文件、建任何目录之前完成全部校验）
    _check_catalog_shape(catalog)
    _check_entities(catalog)
    _check_manifest(manifest)
    staged = {CATALOG_NAME: catalog_text, AUDIT_NAME: audit_text,
              MANIFEST_NAME: _dump(manifest)}
    for name, text in staged.items():
        if not isinstance(text, str) or not text:
            raise CatalogError("序列化产物为空：%s" % name)

    # 落盘：staged 临时文件 → 固定顺序 replace（**manifest 最后**）
    out_dir.mkdir(parents=True, exist_ok=True)
    # ★ 临时路径**先进可清理集合再写** —— 否则部分写失败会留下无人认领的 .tmp
    temps = {name: out_dir / (name + ".tmp") for name in staged}
    replaced: list[str] = []
    replaced_set: set[str] = set()
    replacing = False
    try:
        for name, text in staged.items():
            temps[name].write_text(text, encoding="utf-8")
        replacing = True
        for name in (CATALOG_NAME, AUDIT_NAME, MANIFEST_NAME):
            temps[name].replace(out_dir / name)
            replaced.append(name)
            replaced_set.add(name)
    except Exception as error:
        cleaned, uncleaned = _cleanup_temps(temps, replaced_set)
        remaining = [n for n in (CATALOG_NAME, AUDIT_NAME, MANIFEST_NAME)
                     if n not in replaced_set]
        if replacing:
            detail = ("落盘失败（replace 阶段）：已替换正式文件 %s；未替换正式文件 %s；"
                      "已清理未替换的临时文件 %s；未清理临时文件 %s")
            fields = (replaced, remaining, cleaned, uncleaned)
        else:
            detail = ("落盘失败（stage 阶段）：尚未 replace 任何正式文件；"
                      "已清理临时文件 %s；未清理临时文件 %s")
            fields = (cleaned, uncleaned)
        raise CatalogError(detail % fields) from error
    return manifest


#: 各实体类型的**必要 provenance 字段** —— 缺一即拒（不得用「尽力而为」放行）
REQUIRED_FIELDS = {
    "major": ("entity_id", "canonical_name", "grade", "major_code", "source_file"),
    "course": ("entity_id", "canonical_name", "grade", "major_code",
               "course_code", "row_hash", "source_file"),
    "regulation": ("entity_id", "canonical_title", "relpath", "sha256"),
    "institutional_term": ("entity_id", "canonical_name"),
}

_BACKEND = BACKEND_ROOT
_ROOT = PROJECT_ROOT

#: 上游与产物的**确定性位置** —— 调用者不传也必须校验，不存在「不传就不校验」。
DEFAULT_PLAN_DB = _BACKEND / "data" / "plan_db.json"
DEFAULT_CORPUS = _ROOT / "final-md"
CATALOG_NAME = "entity_catalog.json"
MANIFEST_NAME = "entity_catalog.manifest.json"
AUDIT_NAME = "entity_catalog.collisions.json"
CATALOG_LOGICAL = "backend/data/" + CATALOG_NAME
AUDIT_LOGICAL = "backend/data/" + AUDIT_NAME
#: 上游的**受限逻辑路径** —— manifest 不得指向别处
UPSTREAM_PLAN_DB_LOGICAL = "backend/data/plan_db.json"
UPSTREAM_CORPUS_LOGICAL = "final-md"


def _expected_entity_id(kind: str, row: dict) -> str:
    """行内身份字段 → 该行**应当**有的 entity_id（自洽门的唯一口径）。"""
    if kind == "major":
        return _major_entity_id(row["grade"], row["major_code"])
    if kind == "course":
        return _course_entity_id(row["grade"], row["major_code"],
                                 row["course_code"], row["row_hash"])
    if kind == "regulation":
        return _regulation_entity_id(row["canonical_title"])
    return row["entity_id"]          # institutional_term：本轮为空，无正式规则可推导


def _read_text(path, label: str) -> str:
    """读文本 —— 缺失/不可读一律转 CatalogError，不得裸露 FileNotFoundError。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise CatalogError("%s 无法读取：%s" % (label, path)) from error


def _load_json(path, label: str):
    """读文件并解析 JSON —— 缺失/畸形一律转 CatalogError。"""
    return _load_json_text(_read_text(path, label), label)


def _load_json_text(text: str, label: str):
    """解析已读入的文本 —— 畸形一律转 CatalogError，不得裸露 JSONDecodeError。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise CatalogError("%s 不是合法 JSON" % label) from error


def _check_object(value, label: str) -> dict:
    """嵌套块必须是 JSON 对象 —— 否则 KeyError / AttributeError 会裸露出去。"""
    if not isinstance(value, dict):
        raise CatalogError("%s 必须是对象，实际是 %s" % (label, type(value).__name__))
    return value


def _check_exact_fields(value: dict, required, label: str) -> None:
    """固定形状：缺字段拒、多字段拒。"""
    missing = sorted(set(required) - set(value))
    if missing:
        raise CatalogError("%s 缺字段：%s" % (label, missing))
    extra = sorted(set(value) - set(required))
    if extra:
        raise CatalogError("%s 有多余字段（形状不固定）：%s" % (label, extra))


def _check_non_negative_int(value, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CatalogError("%s 必须是非负整数，实际是 %r" % (label, value))


def _check_sha256(value, label: str) -> None:
    """SHA 必须是 64 位**小写**十六进制 —— 大写、短串、非串一律拒。"""
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise CatalogError("%s 必须是 64 位小写十六进制 SHA-256，实际是 %r" % (label, value))


def _cleanup_temps(temps: dict, replaced: set) -> tuple[list, list]:
    """清理**尚未 replace** 的 .tmp（已 replace 的临时名已被消费）。

    只上报**确实存在过**的 .tmp —— 不得把从未写出的名字谎报成「已清理」。
    返回（已清理, 未清理）；不做伪事务回滚。
    """
    cleaned, uncleaned = [], []
    for name, tmp in temps.items():
        if name in replaced or not tmp.exists():
            continue
        try:
            tmp.unlink()
            cleaned.append(tmp.name)
        except OSError:
            uncleaned.append(tmp.name)
    return sorted(cleaned), sorted(uncleaned)


def _check_catalog_shape(catalog: dict) -> None:
    """catalog **顶层**形状固定 + 本 Task 不得生成课程族。"""
    _check_object(catalog, "catalog")
    _check_exact_fields(catalog, CATALOG_FIELDS, "catalog")
    if catalog["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("catalog schema_version 非法：%s" % (catalog["schema_version"],))
    if catalog["generator_version"] != GENERATOR_VERSION:
        raise CatalogError(
            "catalog generator_version 非法：%s（正式版本 %s）"
            % (catalog["generator_version"], GENERATOR_VERSION))
    families = catalog["course_family"]
    if not isinstance(families, list):
        raise CatalogError("catalog.course_family 必须是列表，实际是 %s"
                           % type(families).__name__)
    if families:
        raise CatalogError("catalog.course_family 必须为空 —— 课程族属 Task 2，本 Task 不推断")
    metrics = _check_object(catalog["audit_metrics"], "catalog.audit_metrics")
    _check_exact_fields(metrics, METRIC_KEYS, "catalog.audit_metrics")
    for key in METRIC_KEYS:
        _check_non_negative_int(metrics[key], "catalog.audit_metrics.%s" % key)
    _check_object(catalog["entities"], "catalog.entities")


def _check_entities(catalog: dict) -> None:
    entities = catalog.get("entities")
    if not isinstance(entities, dict):
        raise CatalogError("catalog 缺 entities")
    unknown = sorted(set(entities) - set(ENTITY_TYPES))
    if unknown:
        raise CatalogError("未知 entity_type：%s" % unknown)
    missing_types = sorted(set(ENTITY_TYPES) - set(entities))
    if missing_types:
        raise CatalogError("catalog 缺 entity_type：%s" % missing_types)

    for kind in ENTITY_TYPES:
        rows = entities[kind]
        if not isinstance(rows, list):
            raise CatalogError("catalog.entities.%s 必须是列表，实际是 %s"
                               % (kind, type(rows).__name__))
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise CatalogError("catalog.entities.%s[%d] 必须是对象，实际是 %s"
                                   % (kind, index, type(row).__name__))
    for kind in ENTITY_TYPES:
        seen: set[str] = set()
        for row in entities[kind]:
            for field in REQUIRED_FIELDS[kind]:
                if row.get(field) in (None, ""):
                    raise CatalogError(
                        "%s 实体缺必要字段 %s：%s" % (kind, field, _dump(row)[:200]))
            entity_id = row["entity_id"]
            if entity_id in seen:
                raise CatalogError("%s 实体 id 重复：%s" % (kind, entity_id))
            seen.add(entity_id)
            # ★ 「唯一」不够 —— 必须由行内身份字段**正确派生**
            if entity_id != _expected_entity_id(kind, row):
                raise CatalogError(
                    "%s 实体 id 与身份字段不自洽：%s ≠ %s"
                    % (kind, entity_id, _expected_entity_id(kind, row)))


def _corpus_tree_sha256(corpus_root) -> tuple[str, list[str]]:
    corpus_root = Path(corpus_root)
    files = sorted(p.relative_to(corpus_root).as_posix()
                   for p in corpus_root.rglob("*.md"))
    digest = hashlib.sha256("\n".join(
        "%s\t%s" % (rel, sha256_file(corpus_root / rel)) for rel in files
    ).encode("utf-8")).hexdigest()
    return digest, files


def _check_regulation_files(catalog: dict, corpus_root) -> None:
    corpus_root = Path(corpus_root).resolve()
    for row in catalog["entities"]["regulation"]:
        rel = row["relpath"]
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise CatalogError("regulation relpath 逃出语料目录：%s" % rel)
        path = (corpus_root / rel).resolve()
        if corpus_root not in path.parents:
            raise CatalogError("regulation relpath 逃出语料目录：%s" % rel)
        if not path.exists():
            raise CatalogError("regulation 上游文件不存在：%s" % rel)
        if sha256_file(path) != row["sha256"]:
            raise CatalogError("regulation 文件 SHA 与当前上游不一致：%s" % rel)


def _check_upstream(manifest: dict, plan_db_path, corpus_root) -> None:
    upstream = manifest.get("upstream") or {}
    recorded = upstream.get("plan_db") or {}

    if not Path(plan_db_path).exists():
        raise CatalogError("上游 plan_db 不存在：%s" % plan_db_path)
    if sha256_file(plan_db_path) != recorded.get("sha256"):
        raise CatalogError("上游 plan_db 内容已变（SHA 不一致），拒绝加载旧目录")
    plan_db = _load_json(plan_db_path, "上游 plan_db")
    current = (plan_db.get("meta") or {}).get("source_fingerprint")
    if current != recorded.get("source_fingerprint"):
        raise CatalogError("上游 source_fingerprint 不一致，拒绝加载旧目录")

    digest, _files = _corpus_tree_sha256(corpus_root)
    if digest != (upstream.get("corpus") or {}).get("tree_sha256"):
        raise CatalogError("上游语料树指纹不一致，拒绝加载旧目录")


def _check_manifest(manifest: dict) -> None:
    """manifest 形状**固定** —— 缺字段拒、多字段拒、版本不符拒。"""
    missing = sorted(set(MANIFEST_FIELDS) - set(manifest))
    if missing:
        raise CatalogError("manifest 缺字段：%s" % missing)
    extra = sorted(set(manifest) - set(MANIFEST_FIELDS))
    if extra:
        raise CatalogError("manifest 有多余字段（形状不固定）：%s" % extra)
    if manifest["artifact_kind"] != "entity_catalog_manifest":
        raise CatalogError("artifact_kind 非法：%s" % manifest["artifact_kind"])
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("manifest schema_version 非法：%s" % manifest["schema_version"])
    if manifest["generator_version"] != GENERATOR_VERSION:
        raise CatalogError(
            "manifest generator_version 非法：%s（正式版本 %s）"
            % (manifest["generator_version"], GENERATOR_VERSION))
    for key, logical, fields in (
            ("catalog", CATALOG_LOGICAL, CATALOG_BLOCK_FIELDS),
            ("audit", AUDIT_LOGICAL, AUDIT_BLOCK_FIELDS)):
        block = _check_object(manifest[key], "manifest.%s" % key)
        _check_exact_fields(block, fields, "manifest.%s" % key)
        if block["exists"] is not True:
            raise CatalogError("manifest.%s.exists 必须为 true" % key)
        if block["logical_path"] != logical:
            raise CatalogError(
                "manifest.%s.logical_path 非法：%s（约定 %s）"
                % (key, block["logical_path"], logical))
        _check_sha256(block["sha256"], "manifest.%s.sha256" % key)

    _check_non_negative_int(manifest["catalog"]["rows"], "manifest.catalog.rows")

    counts = _check_object(manifest["counts"], "manifest.counts")
    _check_exact_fields(counts, COUNTS_KEYS, "manifest.counts")
    for key in COUNTS_KEYS:
        _check_non_negative_int(counts[key], "manifest.counts.%s" % key)

    upstream = _check_object(manifest["upstream"], "manifest.upstream")
    _check_exact_fields(upstream, UPSTREAM_FIELDS, "manifest.upstream")
    plan_db = _check_object(upstream["plan_db"], "manifest.upstream.plan_db")
    _check_exact_fields(plan_db, PLAN_DB_BLOCK_FIELDS, "manifest.upstream.plan_db")
    corpus = _check_object(upstream["corpus"], "manifest.upstream.corpus")
    _check_exact_fields(corpus, CORPUS_BLOCK_FIELDS, "manifest.upstream.corpus")
    if plan_db["logical_path"] != UPSTREAM_PLAN_DB_LOGICAL:
        raise CatalogError("manifest.upstream.plan_db.logical_path 非法：%s（约定 %s）"
                           % (plan_db["logical_path"], UPSTREAM_PLAN_DB_LOGICAL))
    if corpus["logical_path"] != UPSTREAM_CORPUS_LOGICAL:
        raise CatalogError("manifest.upstream.corpus.logical_path 非法：%s（约定 %s）"
                           % (corpus["logical_path"], UPSTREAM_CORPUS_LOGICAL))
    _check_sha256(plan_db["sha256"], "manifest.upstream.plan_db.sha256")
    _check_sha256(corpus["tree_sha256"], "manifest.upstream.corpus.tree_sha256")
    if (not isinstance(plan_db["source_fingerprint"], str)
            or not _SHA256_RE.match(plan_db["source_fingerprint"])):
        raise CatalogError("manifest.upstream.plan_db.source_fingerprint 非法：%r"
                           % (plan_db["source_fingerprint"],))
    _check_non_negative_int(plan_db["docs"], "manifest.upstream.plan_db.docs")
    _check_non_negative_int(corpus["files"], "manifest.upstream.corpus.files")
    _check_non_negative_int(corpus["regulation_files"],
                            "manifest.upstream.corpus.regulation_files")


def _check_sidecar(sidecar, catalog: dict, metrics: dict) -> None:
    """碰撞审计 sidecar —— 形状固定 + join_key 语义必须**复用正式 join_key**。"""
    from agent.tools.query_course.scripts.normalize import join_key

    sidecar = _check_object(sidecar, "collisions sidecar")
    _check_exact_fields(sidecar, SIDECAR_FIELDS, "collisions sidecar")
    if sidecar["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("collisions sidecar schema_version 非法：%s"
                           % (sidecar["schema_version"],))
    if sidecar["metric"] != SIDECAR_METRIC:
        raise CatalogError("collisions sidecar metric 非法：%s（约定 %s）"
                           % (sidecar["metric"], SIDECAR_METRIC))
    if not isinstance(sidecar["note"], str) or not sidecar["note"].strip():
        raise CatalogError("collisions sidecar note 必须是非空字符串")

    groups = sidecar["groups"]
    if not isinstance(groups, list):
        raise CatalogError("collisions sidecar groups 必须是列表，实际是 %s"
                           % type(groups).__name__)
    if len(groups) != metrics["join_key_collision_groups"]:
        raise CatalogError("sidecar 组数 %s ≠ join_key_collision_groups %s"
                           % (len(groups), metrics["join_key_collision_groups"]))

    names = [e["canonical_name"] for e in catalog["entities"]["course"]]
    known = set(names)
    seen_keys: set[str] = set()
    seen_names: set[str] = set()
    for index, group in enumerate(groups):
        group = _check_object(group, "collisions sidecar groups[%d]" % index)
        _check_exact_fields(group, SIDECAR_GROUP_FIELDS,
                            "collisions sidecar groups[%d]" % index)
        key = group["join_key"]
        if not isinstance(key, str) or not key:
            raise CatalogError("sidecar 组 join_key 必须是非空字符串，实际是 %r" % (key,))
        if key in seen_keys:
            raise CatalogError("sidecar join_key 重复：%s" % key)
        seen_keys.add(key)
        members = group["names"]
        if not isinstance(members, list) or not all(isinstance(n, str) for n in members):
            raise CatalogError("sidecar 组 %s 的 names 必须是字符串列表" % key)
        if len(set(members)) < 2:
            raise CatalogError("sidecar 组 %s 少于两个不同正式名称" % key)
        # ★ 组内名称必须**真的**归一到该 join_key（复用正式 normalize.join_key）
        for name in members:
            if name not in known:
                raise CatalogError("sidecar 名称不在 course 实体中：%s" % name)
            if name in seen_names:
                raise CatalogError("sidecar 名称被多个组重复收录：%s" % name)
            seen_names.add(name)
            if join_key(name) != key:
                raise CatalogError(
                    "sidecar 组 %s 的成员 join_key 不符：%s → %s"
                    % (key, name, join_key(name)))
        # ★ 审计不得造成合并：组内每个名称都仍是**独立**课程实体
        if sum(1 for n in names if n in set(members)) < len(set(members)):
            raise CatalogError("sidecar 组 %s 造成了课程名合并" % key)


def _check_cross_file(catalog: dict, manifest: dict, audit_path) -> None:
    """catalog / manifest / collisions sidecar **三方自洽**。"""
    catalog_text = _dump(catalog)
    actual_rows = sum(len(v) for v in catalog["entities"].values())
    if manifest["catalog"]["rows"] != actual_rows:
        raise CatalogError("manifest.catalog.rows=%s ≠ 实际实体行数 %s"
                           % (manifest["catalog"]["rows"], actual_rows))

    counts = {kind: len(catalog["entities"][kind]) for kind in ENTITY_TYPES}
    counts["course_family"] = len(catalog["course_family"])
    for key in COUNTS_KEYS:
        if manifest["counts"][key] != counts[key]:
            raise CatalogError("manifest.counts.%s=%s ≠ 实际 %s"
                               % (key, manifest["counts"][key], counts[key]))

    metrics = catalog["audit_metrics"]
    for field in METRIC_KEYS:
        if manifest[field] != metrics[field]:
            raise CatalogError("manifest.%s=%s ≠ catalog.audit_metrics %s"
                               % (field, manifest[field], metrics[field]))

    audit_path = Path(audit_path)
    if not audit_path.exists():
        raise CatalogError("collisions sidecar 不存在：%s" % audit_path.name)
    audit_text = _read_text(audit_path, "collisions sidecar")
    if hashlib.sha256(audit_text.encode("utf-8")).hexdigest() != manifest["audit"]["sha256"]:
        raise CatalogError("collisions sidecar 与 manifest.audit.sha256 不一致")
    _check_sidecar(_load_json_text(audit_text, "collisions sidecar"), catalog, metrics)


def load(catalog_path, manifest_path, *, plan_db_path=None, corpus_root=None) -> dict:
    """严格加载并校验。任一门不通过即抛 `CatalogError`。

    ★ 上游路径**不传也会校验** —— 缺省确定性定位仓库内的正式上游；
      不存在 skip/force/unsafe 之类的逃生参数。
    ★ **绝不**静默回退旧目录，**绝不**把失败映射成 `no_evidence` / `irrelevant_result` /
      `tool_failed` —— 失败就是失败。
    """
    plan_db_path = DEFAULT_PLAN_DB if plan_db_path is None else Path(plan_db_path)
    corpus_root = DEFAULT_CORPUS if corpus_root is None else Path(corpus_root)

    catalog_path, manifest_path = Path(catalog_path), Path(manifest_path)
    if not catalog_path.exists():
        raise CatalogError("catalog 不存在：%s" % catalog_path.name)
    if not manifest_path.exists():
        raise CatalogError("manifest 不存在：%s" % manifest_path.name)

    manifest = _load_json(manifest_path, "manifest")
    _check_manifest(manifest)

    catalog_text = _read_text(catalog_path, "catalog")
    actual = hashlib.sha256(catalog_text.encode("utf-8")).hexdigest()
    if actual != (manifest.get("catalog") or {}).get("sha256"):
        raise CatalogError("catalog 与 manifest 哈希不一致（目录被改写），拒绝加载")

    catalog = _load_json_text(catalog_text, "catalog")
    _check_catalog_shape(catalog)
    _check_entities(catalog)
    _check_upstream(manifest, plan_db_path, corpus_root)
    _check_regulation_files(catalog, corpus_root)
    _check_cross_file(catalog, manifest, catalog_path.parent / AUDIT_NAME)
    return catalog

def _main() -> int:
    """`python -m entities.entity_catalog --build` —— 重新生成 `backend/data/` 下的只读目录。"""
    import sys

    if "--build" not in sys.argv:
        print("用法：python -m entities.entity_catalog --build")
        return 2
    backend = BACKEND_ROOT
    root = PROJECT_ROOT
    summary = build(backend / "data" / "plan_db.json", root / "_md", backend / "data")
    print(_dump(summary["counts"]))
    print("catalog sha256 =", summary["catalog"]["sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
