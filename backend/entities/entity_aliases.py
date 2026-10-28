# -*- coding: utf-8 -*-
"""别名与实体族契约 —— 纯数据契约、严格校验、确定性加载、**reviewed-only 生产视图**。

本模块**只**做四件事：

  1. `EntityFamily` 契约（family sidecar）；
  2. `AliasRecord` 契约（alias sidecar）；
  3. 两者共用的 manifest；
  4. **生产只读接口**（`load_alias_bundle` + `reviewed_*`）与**独立审计接口**（`audit_*`）。

它**不**做（后续 Task 才做）：候选生成、上下文消歧、`ResolutionResult`、
运行图接入、工具改造。它**不**读 `query_expansions.json`
（那是**路由主题词表**，不是实体别名表 —— 见设计 §2.5 / §5.1 硬门 7）。

★ 本轮**没有任何简称获得人工批准**：正式 aliases / families 都是**空文件**，
不接受从需求示例或 `query_expansions.json` 自动晋升。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from core.paths import BACKEND_ROOT, PROJECT_ROOT

from entities import entity_catalog as EC

SCHEMA_VERSION = "entity_aliases/1"
GENERATOR_VERSION = "entity_aliases/1.0.0"
ARTIFACT_KIND = "entity_aliases_manifest"

ENTITY_TYPES = EC.ENTITY_TYPES
REVIEW_STATUSES = ("pending", "reviewed", "rejected")
MAPPING_TYPES = ("one_to_one", "one_to_many")

#: scope 本轮**只**允许这三个键；未知键一律拒
SCOPE_KEYS = ("grade", "major_code", "semester")

#: manifest 的固定形状 —— 缺字段拒、多字段拒
MANIFEST_FIELDS = ("artifact_kind", "schema_version", "generator_version",
                   "catalog", "aliases", "families")
CATALOG_BLOCK_FIELDS = ("logical_path", "sha256")
FILE_BLOCK_FIELDS = ("logical_path", "exists", "rows", "sha256", "by_review_status")

#: sidecar 每行的固定形状 —— 多一个字段都不行（模型分数 / embedding / 时间戳一律拒）
FAMILY_FIELDS = ("family_id", "entity_type", "canonical_label", "members",
                 "review_status", "reviewed_by", "review_note")
ALIAS_FIELDS = ("alias_id", "alias", "entity_type", "canonical", "canonical_family",
                "mapping_type", "scope", "review_status", "reviewed_by", "review_note")

_BACKEND = BACKEND_ROOT
_ROOT = PROJECT_ROOT

DEFAULT_DIR = _BACKEND / "data"
ALIASES_NAME = "entity_aliases.jsonl"
FAMILIES_NAME = "entity_families.jsonl"
MANIFEST_NAME = "entity_aliases.manifest.json"

ALIASES_LOGICAL = "backend/data/" + ALIASES_NAME
FAMILIES_LOGICAL = "backend/data/" + FAMILIES_NAME
CATALOG_LOGICAL = EC.CATALOG_LOGICAL

#: 正式空的 JSONL 必须是**零字节**（`sha256` 为标准空文件哈希）
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

_CATALOG_NAME_FIELD = {"major": "canonical_name", "course": "canonical_name",
                       "regulation": "canonical_title",
                       "institutional_term": "canonical_name"}
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class AliasCatalogError(RuntimeError):
    """别名 / 实体族 sidecar 非法或不一致。**一律拒绝加载**，绝不降级、绝不回退。

    ★ 它**不得**被伪装成 `no_evidence` / `irrelevant_result` / `tool_failed`
      —— 数据契约坏掉是配置事故，不是「学校没有相关规定」。
    """


def _dump(value) -> str:
    """规范化 JSON —— 排序 + 紧凑分隔符，保证逐字节可复现。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _join_key(text: str) -> str:
    """**复用当前正式归一函数** —— 不另写第二份名称归一规则。

    `repository.find_major` / `match_course` 用的就是它，所以正式名冲突判据
    与运行时匹配判据**同源**。
    """
    from agent.tools.query_course.scripts.normalize import join_key

    return join_key(text)


# ------------------------------------------------------------------ 身份派生

def _family_id(row: dict) -> str:
    """`family_id` = `family:` + sha256(entity_type + **排序后** members 的规范 JSON)[:32]。

    ★ 只由稳定规范字段派生：`review_note` / `canonical_label` / 时间戳 / 行号
      都不参与 —— 改文案不得让身份漂移，成员一变就得是新族。
    """
    payload = {"entity_type": row["entity_type"], "members": sorted(row["members"])}
    return "family:" + _sha256_text(_dump(payload))[:32]


def _alias_id(row: dict) -> str:
    """`alias_id` = `alias:` + sha256(规范 alias 形式 + entity_type + mapping_type +
    目标（canonical 或 canonical_family）+ 规范化 scope 的规范 JSON)[:32]。

    ★ `review_note` / `reviewed_by` **不参与** —— 审核备注变化不得改变身份。
    """
    target = row["canonical"] if row["mapping_type"] == "one_to_one" \
        else row["canonical_family"]
    payload = {"alias": _join_key(row["alias"]), "entity_type": row["entity_type"],
               "mapping_type": row["mapping_type"], "target": target,
               "scope": row["scope"]}
    return "alias:" + _sha256_text(_dump(payload))[:32]


# ------------------------------------------------------------------ 基本形状门

def _check_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise AliasCatalogError("%s 必须是对象，实际是 %s" % (label, type(value).__name__))
    return value


def _check_exact_fields(value: dict, required, label: str) -> None:
    missing = sorted(set(required) - set(value))
    if missing:
        raise AliasCatalogError("%s 缺字段：%s" % (label, missing))
    extra = sorted(set(value) - set(required))
    if extra:
        raise AliasCatalogError("%s 有多余字段（形状固定，不得增补）：%s" % (label, extra))


def _check_non_negative_int(value, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AliasCatalogError("%s 必须是非负整数，实际是 %r" % (label, value))


def _check_sha256(value, label: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise AliasCatalogError("%s 必须是 64 位小写十六进制 SHA-256，实际是 %r"
                                % (label, value))


def _check_text(value, label: str, *, allow_none=False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value.strip():
        raise AliasCatalogError("%s 必须是非空字符串，实际是 %r" % (label, value))


def _check_review_fields(row: dict, label: str) -> None:
    """`pending` / `reviewed` / `rejected` 三态**各自**该有什么、不该有什么。"""
    status = row["review_status"]
    if status not in REVIEW_STATUSES:
        raise AliasCatalogError("%s.review_status 非法：%r（只能是 %s）"
                                % (label, status, list(REVIEW_STATUSES)))
    reviewer, note = row["reviewed_by"], row["review_note"]
    if status == "pending":
        if reviewer is not None:
            raise AliasCatalogError("%s 是 pending，reviewed_by 必须为 null，实际是 %r"
                                    % (label, reviewer))
    else:
        _check_text(reviewer, "%s.reviewed_by" % label)
    if not isinstance(note, str):
        raise AliasCatalogError("%s.review_note 必须是字符串（可为空串），实际是 %r"
                                % (label, note))
    if status == "rejected" and not note.strip():
        raise AliasCatalogError("%s 是 rejected，review_note 必须写明拒绝理由" % label)


def _check_scope(scope, label: str) -> dict:
    """scope **只限制适用范围**，不创建实体；序列化由 `_dump` 保证确定性排序。"""
    scope = _check_object(scope, label)
    unknown = sorted(set(scope) - set(SCOPE_KEYS))
    if unknown:
        raise AliasCatalogError("%s 含未知键：%s（本轮只允许 %s）"
                                % (label, unknown, list(SCOPE_KEYS)))
    for key in ("grade", "major_code"):
        if key in scope:
            _check_text(scope[key], "%s.%s" % (label, key))
    if "semester" in scope:
        value = scope["semester"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AliasCatalogError(
                "%s.semester 必须是正整数（bool 不算整数），实际是 %r" % (label, value))
    return scope


# ------------------------------------------------------------------ 目录索引

def _catalog_indexes(catalog: dict):
    """`{entity_id: entity_type}` 与 `{join_key(正式名): (entity_type, entity_id)}`。"""
    by_id, by_name = {}, {}
    for kind in ENTITY_TYPES:
        for row in catalog["entities"][kind]:
            by_id[row["entity_id"]] = kind
            by_name.setdefault(_join_key(row[_CATALOG_NAME_FIELD[kind]]),
                               (kind, row["entity_id"]))
    return by_id, by_name


# ------------------------------------------------------------------ 行校验

def _check_families(rows, by_id) -> list[dict]:
    """实体族硬门。★ 族**只**来自人工登记 —— 同名 / join_key 相同 / 相似都不自动建族。"""
    checked = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(rows):
        label = "families[%d]" % index
        row = _check_object(raw, label)
        _check_exact_fields(row, FAMILY_FIELDS, label)
        kind = row["entity_type"]
        if kind not in ENTITY_TYPES:
            raise AliasCatalogError("%s.entity_type 非法：%r（只能是 %s）"
                                    % (label, kind, list(ENTITY_TYPES)))
        _check_text(row["canonical_label"], "%s.canonical_label" % label)
        members = row["members"]
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise AliasCatalogError("%s.members 必须是字符串列表，实际是 %r"
                                    % (label, members))
        if len(set(members)) < 2:
            raise AliasCatalogError("%s.members 必须是至少两个**不同**的 entity_id" % label)
        if members != sorted(members):
            raise AliasCatalogError("%s.members 必须排序，实际是 %r" % (label, members))
        for member in members:
            if member not in by_id:
                raise AliasCatalogError("%s 的成员不在规范实体目录中（孤儿）：%s"
                                        % (label, member))
            if by_id[member] != kind:
                raise AliasCatalogError("%s 的成员 %s 是 %s，与 family.entity_type=%s 不符"
                                        % (label, member, by_id[member], kind))
        _check_review_fields(row, label)
        if row["family_id"] != _family_id(row):
            raise AliasCatalogError(
                "%s.family_id 与成员不自洽：%s ≠ %s（成员一变就必须是新族；"
                "同一成员集合不得以不同 id 重复登记）"
                % (label, row["family_id"], _family_id(row)))
        if row["family_id"] in seen_ids:
            raise AliasCatalogError("family_id 重复：%s" % row["family_id"])
        seen_ids.add(row["family_id"])
        checked.append(row)
    return checked


def _check_aliases(rows, by_id, by_name, families) -> list[dict]:
    """别名硬门 —— 含孤儿目标、状态一致性、ID 自洽、`reviewed` 冲突后写、正式名遮蔽。"""
    checked = []
    seen_ids: set[str] = set()
    reviewed_keys: dict[tuple, tuple] = {}
    for index, raw in enumerate(rows):
        label = "aliases[%d]" % index
        row = _check_object(raw, label)
        _check_exact_fields(row, ALIAS_FIELDS, label)

        alias = row["alias"]
        if not isinstance(alias, str) or not alias.strip():
            raise AliasCatalogError("%s.alias 必须是去除首尾空白后仍非空的字符串，实际是 %r"
                                    % (label, alias))
        if _CONTROL_RE.search(alias):
            raise AliasCatalogError("%s.alias 不得含换行或控制字符：%r" % (label, alias))

        kind, mapping = row["entity_type"], row["mapping_type"]
        if kind not in ENTITY_TYPES:
            raise AliasCatalogError("%s.entity_type 非法：%r（只能是 %s）"
                                    % (label, kind, list(ENTITY_TYPES)))
        if mapping not in MAPPING_TYPES:
            raise AliasCatalogError("%s.mapping_type 非法：%r（只能是 %s）"
                                    % (label, mapping, list(MAPPING_TYPES)))

        canonical, family_id = row["canonical"], row["canonical_family"]
        if mapping == "one_to_one":
            if family_id is not None:
                raise AliasCatalogError(
                    "%s 是 one_to_one，canonical_family 必须为 null（实际 %r）"
                    % (label, family_id))
            _check_text(canonical, "%s.canonical" % label)
            if canonical not in by_id:
                raise AliasCatalogError("%s.canonical 不在规范实体目录中（孤儿）：%s"
                                        % (label, canonical))
            if by_id[canonical] != kind:
                raise AliasCatalogError("%s.canonical 是 %s，与 entity_type=%s 不符"
                                        % (label, by_id[canonical], kind))
        else:
            if canonical is not None:
                raise AliasCatalogError(
                    "%s 是 one_to_many，canonical 必须为 null（实际 %r）" % (label, canonical))
            _check_text(family_id, "%s.canonical_family" % label)
            family = families.get(family_id)
            if family is None:
                raise AliasCatalogError("%s.canonical_family 不在实体族 sidecar 中（孤儿）：%s"
                                        % (label, family_id))
            if family["entity_type"] != kind:
                raise AliasCatalogError(
                    "%s.canonical_family 是 %s 族，与 entity_type=%s 不符"
                    % (label, family["entity_type"], kind))
            if family["review_status"] != "reviewed":
                raise AliasCatalogError(
                    "%s 指向 %s 族 —— 未经审核的族不得进入正式生产映射"
                    % (label, family["review_status"]))

        _check_scope(row["scope"], "%s.scope" % label)
        _check_review_fields(row, label)

        if row["alias_id"] != _alias_id(row):
            raise AliasCatalogError("%s.alias_id 与规范字段不自洽：%s ≠ %s"
                                    % (label, row["alias_id"], _alias_id(row)))
        if row["alias_id"] in seen_ids:
            raise AliasCatalogError(
                "alias_id 重复：%s（完全重复行不得静默去重）" % row["alias_id"])
        seen_ids.add(row["alias_id"])

        # ★ 简称不许遮蔽正式名 —— 按**全实体目录**检查，不只看同类型
        clash = by_name.get(_join_key(alias))
        if clash is not None:
            raise AliasCatalogError(
                "%s.alias %r 与正式 %s 名称冲突（%s）—— 用户已经能写正式名，不需要别名"
                % (label, alias, clash[0], clash[1]))

        if row["review_status"] == "reviewed":
            key = (kind, _join_key(alias), _dump(row["scope"]))
            target = (mapping, canonical, family_id)
            if key in reviewed_keys and reviewed_keys[key] != target:
                raise AliasCatalogError(
                    "同一 alias+entity_type+scope 的 reviewed 映射出现不同目标，"
                    "整体拒绝（不得后写覆盖）：%s" % (key[1],))
            reviewed_keys[key] = target
        checked.append(row)
    return checked


# ------------------------------------------------------------------ 读取 / 解析

def _read_text(path, label: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise AliasCatalogError("%s 无法读取：%s" % (label, path)) from error


def _load_json(path, label: str):
    text = _read_text(path, label)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise AliasCatalogError("%s 不是合法 JSON" % label) from error


def _parse_jsonl(text: str, label: str) -> list:
    """逐行解析 —— 畸形 JSON 一律专用异常；**行是不是对象**由行级门负责。"""
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise AliasCatalogError("%s 第 %d 行不是合法 JSON" % (label, lineno)) from error
    return rows


def _jsonl_text(rows) -> str:
    """空列表写成**零字节**；非空时每行一个规范 JSON 对象。"""
    return "".join(_dump(row) + "\n" for row in rows)


def _by_status(rows) -> dict:
    """状态分布 —— 非对象行不计入任何一种状态（它们会被行级门单独拦下）。"""
    return {status: sum(1 for row in rows
                        if isinstance(row, dict) and row.get("review_status") == status)
            for status in REVIEW_STATUSES}


# ------------------------------------------------------------------ 生成

def _pack_products(out_dir: Path, alias_rows, family_rows):
    """组装三份产物的**文本**（不写盘、不校验）—— `build` 与此共用同一份组装逻辑。

    排序键取整行的规范 JSON：**对所有输入都成立**（含畸形行），
    因此「同样的行集合 → 同样的字节」不依赖调用者给的行序。
    """
    alias_rows = sorted(alias_rows, key=_dump)
    family_rows = sorted(family_rows, key=_dump)
    aliases_text = _jsonl_text(alias_rows)
    families_text = _jsonl_text(family_rows)
    manifest = {
        "artifact_kind": ARTIFACT_KIND,
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "catalog": {
            "logical_path": CATALOG_LOGICAL,
            "sha256": EC.sha256_file(out_dir / EC.CATALOG_NAME),
        },
        "aliases": {
            "logical_path": ALIASES_LOGICAL,
            "exists": True,
            "rows": len(alias_rows),
            "sha256": _sha256_text(aliases_text),
            "by_review_status": _by_status(alias_rows),
        },
        "families": {
            "logical_path": FAMILIES_LOGICAL,
            "exists": True,
            "rows": len(family_rows),
            "sha256": _sha256_text(families_text),
            "by_review_status": _by_status(family_rows),
        },
    }
    staged = {ALIASES_NAME: aliases_text, FAMILIES_NAME: families_text,
              MANIFEST_NAME: _dump(manifest)}
    return manifest, staged


def build(out_dir, aliases, families) -> dict:
    """确定性生成 aliases / families / manifest 三份 sidecar，返回 manifest。

    ★ **先全量验证、后落盘**：静态失败不建目录、不写正式文件、不留 `.tmp`。
    ★ 上游（`entity_catalog`）**只读**：本函数既不改目录，也不往里塞实体。
    """
    out_dir = Path(out_dir)
    try:
        catalog = EC.load(out_dir / EC.CATALOG_NAME, out_dir / EC.MANIFEST_NAME)
    except EC.CatalogError as error:
        raise AliasCatalogError("规范实体目录校验失败：%s" % error) from error

    by_id, by_name = _catalog_indexes(catalog)
    family_rows = _check_families(list(families), by_id)
    alias_rows = _check_aliases(list(aliases), by_id, by_name,
                                {row["family_id"]: row for row in family_rows})
    manifest, staged = _pack_products(out_dir, alias_rows, family_rows)
    _write_pack(out_dir, staged)
    return manifest


def _write_pack(out_dir: Path, staged: dict) -> None:
    """staged `.tmp` → 固定顺序 replace（**manifest 最后**）。

    ★ 临时路径**先进可清理集合再写**（部分写失败也要能认领）；
      失败一律保留原始异常于 `__cause__`；**不做伪事务回滚**，也不谎报未发生的清理。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    temps = {name: out_dir / (name + ".tmp") for name in staged}
    replaced: list[str] = []
    replaced_set: set[str] = set()
    replacing = False
    try:
        for name, text in staged.items():
            temps[name].write_text(text, encoding="utf-8")
        replacing = True
        for name in (ALIASES_NAME, FAMILIES_NAME, MANIFEST_NAME):
            temps[name].replace(out_dir / name)
            replaced.append(name)
            replaced_set.add(name)
    except Exception as error:
        cleaned, uncleaned = _cleanup_temps(temps, replaced_set)
        remaining = [n for n in (ALIASES_NAME, FAMILIES_NAME, MANIFEST_NAME)
                     if n not in replaced_set]
        if replacing:
            detail = ("落盘失败（replace 阶段）：已替换正式文件 %s；未替换正式文件 %s；"
                      "已清理未替换的临时文件 %s；未清理临时文件 %s")
            fields = (replaced, remaining, cleaned, uncleaned)
        else:
            detail = ("落盘失败（stage 阶段）：尚未 replace 任何正式文件；"
                      "已清理临时文件 %s；未清理临时文件 %s")
            fields = (cleaned, uncleaned)
        raise AliasCatalogError(detail % fields) from error


def _cleanup_temps(temps: dict, replaced: set) -> tuple[list, list]:
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


# ------------------------------------------------------------------ manifest 门

def _check_manifest_shape(manifest) -> dict:
    manifest = _check_object(manifest, "别名 manifest")
    _check_exact_fields(manifest, MANIFEST_FIELDS, "别名 manifest")
    if manifest["artifact_kind"] != ARTIFACT_KIND:
        raise AliasCatalogError("artifact_kind 非法：%s" % (manifest["artifact_kind"],))
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise AliasCatalogError("manifest schema_version 非法：%s"
                                % (manifest["schema_version"],))
    if manifest["generator_version"] != GENERATOR_VERSION:
        raise AliasCatalogError("manifest generator_version 非法：%s（正式版本 %s）"
                                % (manifest["generator_version"], GENERATOR_VERSION))

    catalog = _check_object(manifest["catalog"], "manifest.catalog")
    _check_exact_fields(catalog, CATALOG_BLOCK_FIELDS, "manifest.catalog")
    if catalog["logical_path"] != CATALOG_LOGICAL:
        raise AliasCatalogError("manifest.catalog.logical_path 非法：%s（约定 %s）"
                                % (catalog["logical_path"], CATALOG_LOGICAL))
    _check_sha256(catalog["sha256"], "manifest.catalog.sha256")

    for key, logical in (("aliases", ALIASES_LOGICAL), ("families", FAMILIES_LOGICAL)):
        block = _check_object(manifest[key], "manifest.%s" % key)
        _check_exact_fields(block, FILE_BLOCK_FIELDS, "manifest.%s" % key)
        if block["exists"] is not True:
            raise AliasCatalogError("manifest.%s.exists 必须为 true" % key)
        if block["logical_path"] != logical:
            raise AliasCatalogError("manifest.%s.logical_path 非法：%s（约定 %s）"
                                    % (key, block["logical_path"], logical))
        _check_sha256(block["sha256"], "manifest.%s.sha256" % key)
        _check_non_negative_int(block["rows"], "manifest.%s.rows" % key)
        status = _check_object(block["by_review_status"],
                               "manifest.%s.by_review_status" % key)
        _check_exact_fields(status, REVIEW_STATUSES,
                            "manifest.%s.by_review_status" % key)
        for name in REVIEW_STATUSES:
            _check_non_negative_int(status[name],
                                    "manifest.%s.by_review_status.%s" % (key, name))
    return manifest


def _check_disk_identity(manifest: dict, key: str, text: str) -> None:
    """rows 与 sha256 必须与磁盘实况一致。"""
    lines = [line for line in text.splitlines() if line.strip()]
    if manifest[key]["rows"] != len(lines):
        raise AliasCatalogError("manifest.%s.rows=%s ≠ 磁盘实际 %d 行"
                                % (key, manifest[key]["rows"], len(lines)))
    if manifest[key]["sha256"] != _sha256_text(text):
        raise AliasCatalogError("manifest.%s.sha256 与磁盘文件不一致（sidecar 被改写）"
                                % key)


def _check_status_distribution(manifest: dict, key: str, rows) -> None:
    actual = _by_status(rows)
    if manifest[key]["by_review_status"] != actual:
        raise AliasCatalogError("manifest.%s.by_review_status=%r ≠ 磁盘实况 %r"
                                % (key, manifest[key]["by_review_status"], actual))


# ------------------------------------------------------------------ 加载

class AliasBundle:
    """已通过全部严格门的别名包。

    ★ 里面**同时**装着 pending / rejected 行（审计需要），
      **生产只能用 `reviewed_aliases()` / `reviewed_families()`**。
    """

    __slots__ = ("catalog", "aliases", "families",
                 "catalog_sha256", "aliases_sha256", "families_sha256")

    def __init__(self, catalog, aliases, families,
                 catalog_sha256, aliases_sha256, families_sha256):
        self.catalog = catalog
        self.aliases = aliases
        self.families = families
        self.catalog_sha256 = catalog_sha256
        self.aliases_sha256 = aliases_sha256
        self.families_sha256 = families_sha256


def load_alias_bundle(bundle_dir=None) -> AliasBundle:
    """严格加载并校验别名包。任一门不通过即抛 `AliasCatalogError`。

    校验顺序：manifest 形状 → catalog 绑定 → 两份 sidecar 与磁盘一致 →
    规范目录严格加载 → 行级门（族在前，别名引用族）→ 组装。

    ★ 不存在 `include_pending` / `unsafe` / `force` / `skip_review` 之类的绕过参数。
    ★ 目录只能由 `bundle_dir` 指定**一个目录**，四份文件按固定名定位，
      不接受任意路径跳转。
    """
    directory = Path(DEFAULT_DIR if bundle_dir is None else bundle_dir)
    manifest = _check_manifest_shape(_load_json(directory / MANIFEST_NAME, "别名 manifest"))

    catalog_path = directory / EC.CATALOG_NAME
    try:
        catalog_sha = EC.sha256_file(catalog_path)
    except OSError as error:
        raise AliasCatalogError("规范实体目录不可读：%s" % catalog_path.name) from error
    if manifest["catalog"]["sha256"] != catalog_sha:
        raise AliasCatalogError(
            "manifest.catalog.sha256 与当前实体目录不一致 —— 目录已漂移，拒绝加载")

    aliases_text = _read_text(directory / ALIASES_NAME, "aliases")
    families_text = _read_text(directory / FAMILIES_NAME, "families")
    _check_disk_identity(manifest, "aliases", aliases_text)
    _check_disk_identity(manifest, "families", families_text)

    try:
        catalog = EC.load(catalog_path, directory / EC.MANIFEST_NAME)
    except EC.CatalogError as error:
        raise AliasCatalogError("规范实体目录校验失败：%s" % error) from error

    alias_raw = _parse_jsonl(aliases_text, "aliases")
    family_raw = _parse_jsonl(families_text, "families")
    _check_status_distribution(manifest, "aliases", alias_raw)
    _check_status_distribution(manifest, "families", family_raw)

    by_id, by_name = _catalog_indexes(catalog)
    family_rows = _check_families(family_raw, by_id)
    alias_rows = _check_aliases(alias_raw, by_id, by_name,
                                {row["family_id"]: row for row in family_rows})
    return AliasBundle(catalog=catalog, aliases=tuple(alias_rows),
                       families=tuple(family_rows), catalog_sha256=catalog_sha,
                       aliases_sha256=_sha256_text(aliases_text),
                       families_sha256=_sha256_text(families_text))


def reviewed_aliases(bundle: AliasBundle) -> tuple:
    """**生产视图** —— 只返回 `reviewed`，pending / rejected 一律不出现在这里。"""
    return tuple(row for row in bundle.aliases if row["review_status"] == "reviewed")


def audit_aliases(bundle: AliasBundle) -> tuple:
    """**审计视图**（独立接口，不与生产接口混用）—— 返回全部状态。"""
    return tuple(bundle.aliases)


def reviewed_families(bundle: AliasBundle) -> tuple:
    """**生产视图** —— 只返回 `reviewed`。"""
    return tuple(row for row in bundle.families if row["review_status"] == "reviewed")


def audit_families(bundle: AliasBundle) -> tuple:
    """**审计视图** —— 返回全部状态。"""
    return tuple(bundle.families)


def _main() -> int:
    """`python -m entities.entity_aliases --build` —— 生成正式（本轮为空）sidecar。"""
    import sys

    if "--build" not in sys.argv:
        print("用法：python -m entities.entity_aliases --build")
        return 2
    manifest = build(DEFAULT_DIR, [], [])
    print(_dump({"aliases": manifest["aliases"]["rows"],
                 "families": manifest["families"]["rows"],
                 "aliases_sha256": manifest["aliases"]["sha256"],
                 "families_sha256": manifest["families"]["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())