# -*- coding: utf-8 -*-
"""未决实体简称的去标识化待审核队列。

队列不是正式别名表：不含 ``review_status`` / ``canonical``，也没有任何晋升入口。
它只聚合 resolver 已判为 ambiguous / unknown / scope_conflict 的提及，且默认不保存
完整问句。正式别名仍只能走 ``entity_aliases`` 的人工审核契约。
"""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

from entities import entity_resolver as ER

QUEUE_VERSION = "entity_alias_queue/1.0.0"
QUEUE_STATUSES = frozenset({"ambiguous", "unknown", "scope_conflict"})
ROW_FIELDS = frozenset({
    "alias", "alias_key", "resolution_status", "support_tier",
    "entity_types", "candidate_entities", "scope", "count",
    "catalog_sha256", "aliases_sha256", "resolver_version", "queue_version",
})

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LABELED_ID_RE = re.compile(r"(?:学号|身份证号?)\s*[:：]?\s*[A-Za-z0-9-]{6,}")
_LABELED_NAME_RE = re.compile(r"姓名\s*[:：]\s*[\u4e00-\u9fff·]{2,20}")
_LABELED_CLASS_RE = re.compile(r"班级\s*[:：]\s*[^，,。；;\s]{2,30}")
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class QueueError(RuntimeError):
    """队列数据或写盘失败。"""


class QueuePrivacyError(QueueError):
    """问句含可识别个人信息；必须在落盘前停止。"""


def _check_privacy(query: str) -> None:
    for label, pattern in (
        ("手机号", _PHONE_RE), ("邮箱", _EMAIL_RE), ("学号/证件号", _LABELED_ID_RE),
        ("姓名", _LABELED_NAME_RE), ("班级", _LABELED_CLASS_RE),
    ):
        if pattern.search(query or ""):
            raise QueuePrivacyError("原问句含%s；待审核队列未写入" % label)


def _scope(envelope) -> dict:
    values = {
        "grade": envelope.context.grade,
        "major_code": envelope.context.major_code,
        "semester": envelope.context.semester,
    }
    return {key: value for key, value in values.items() if value is not None}


def _candidate(candidate) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "entity_type": candidate.entity_type,
        "canonical_name": candidate.canonical_name,
        "tier": candidate.tier,
        "instance_ids": list(candidate.instance_ids),
    }


def _row_key(row) -> tuple:
    return (row["alias_key"], row["resolution_status"],
            json.dumps(row["scope"], ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")),
            row["catalog_sha256"], row["aliases_sha256"], row["resolver_version"])


def queue_rows(envelopes) -> tuple[dict, ...]:
    """聚合未决提及；完整原问句只用于写前隐私门，绝不进入返回值。"""
    merged: dict[tuple, dict] = {}
    for envelope in envelopes:
        _check_privacy(envelope.original_query)
        for mention in envelope.mentions:
            if mention.selected is not None or mention.resolution_status not in QUEUE_STATUSES:
                continue
            candidates = sorted((_candidate(item) for item in mention.candidates),
                                key=lambda item: (item["entity_type"],
                                                  item["canonical_name"],
                                                  item["candidate_id"]))
            row = {
                "alias": mention.raw_text,
                "alias_key": ER._join_key(mention.raw_text),
                "resolution_status": mention.resolution_status,
                "support_tier": mention.support_tier,
                "entity_types": sorted({item["entity_type"] for item in candidates}),
                "candidate_entities": candidates,
                "scope": _scope(envelope),
                "count": 1,
                "catalog_sha256": envelope.catalog_sha256,
                "aliases_sha256": envelope.aliases_sha256,
                "resolver_version": envelope.resolver_version,
                "queue_version": QUEUE_VERSION,
            }
            key = _row_key(row)
            if key not in merged:
                merged[key] = row
                continue
            current = merged[key]
            current["count"] += 1
            union = {item["candidate_id"]: item
                     for item in current["candidate_entities"]}
            union.update({item["candidate_id"]: item for item in candidates})
            current["candidate_entities"] = sorted(
                union.values(), key=lambda item: (item["entity_type"],
                                                   item["canonical_name"],
                                                   item["candidate_id"]))
            current["entity_types"] = sorted(
                {item["entity_type"] for item in current["candidate_entities"]})
    return tuple(merged[key] for key in sorted(merged))


def _validate_row(row, where="queue row") -> dict:
    if not isinstance(row, dict) or set(row) != ROW_FIELDS:
        raise QueueError("%s 字段必须恰好为 %s" % (where, sorted(ROW_FIELDS)))
    if not isinstance(row["alias"], str) or not row["alias"].strip():
        raise QueueError("%s.alias 不能为空" % where)
    if row["alias_key"] != ER._join_key(row["alias"]):
        raise QueueError("%s.alias_key 与正式 join_key 不一致" % where)
    if row["resolution_status"] not in QUEUE_STATUSES:
        raise QueueError("%s 不是未决状态" % where)
    if not isinstance(row["count"], int) or isinstance(row["count"], bool) \
            or row["count"] < 1:
        raise QueueError("%s.count 必须是正整数" % where)
    for field in ("catalog_sha256", "aliases_sha256"):
        if not isinstance(row[field], str) or not _SHA_RE.match(row[field]):
            raise QueueError("%s.%s 必须是 SHA-256" % (where, field))
    if row["queue_version"] != QUEUE_VERSION:
        raise QueueError("%s.queue_version 不受支持" % where)
    if not isinstance(row["scope"], dict) or set(row["scope"]) - {
            "grade", "major_code", "semester"}:
        raise QueueError("%s.scope 非法" % where)
    if not isinstance(row["candidate_entities"], list) \
            or not isinstance(row["entity_types"], list):
        raise QueueError("%s 候选字段必须是列表" % where)
    return row


def load_queue(path) -> tuple[dict, ...]:
    source = Path(path)
    rows = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise QueueError("无法读取待审核队列：%s" % source) from error
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise QueueError("%s:%d 不是合法 JSON" % (source, lineno)) from error
        rows.append(_validate_row(raw, "%s:%d" % (source, lineno)))
    keys = [_row_key(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise QueueError("待审核队列含重复聚合键")
    return tuple(rows)


def write_queue(path, envelopes) -> tuple[dict, ...]:
    """全量预检后原子替换单个队列文件；不会读取或写入正式 alias sidecar。"""
    rows = queue_rows(envelopes)
    for index, row in enumerate(rows):
        _validate_row(row, "queue[%d]" % index)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    try:
        with io.open(temp, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False,
                                        sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except OSError as error:
        temp.unlink(missing_ok=True)
        raise QueueError("待审核队列写盘失败：%s" % target) from error
    return rows


__all__ = ["QUEUE_VERSION", "QueueError", "QueuePrivacyError", "queue_rows",
           "load_queue", "write_queue"]
