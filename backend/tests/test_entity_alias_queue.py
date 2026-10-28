# -*- coding: utf-8 -*-
"""未决简称的去标识化待审核队列。"""
from dataclasses import replace
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from entities import entity_alias_queue as QUEUE  # noqa: E402
from entities import entity_resolver as ER  # noqa: E402


def unresolved(query="形策有哪些课"):
    """取一个**仍未决**的提及。

    ★ 这里以前用「计科」。2026-10-17 的人工裁决把 高数/大物/毛概/代数/计科/软工
      落成正式别名后，计科 已能被解析（T2），不再是未决提及。
      改用同样是 T4、但**未被裁决**的「形策」—— 本文件测的是队列本身
      （隐私门 / 聚合 / 形状），不是某一条具体别名。
    """
    envelope = ER.build_production_resolver().resolve(query)
    assert any(m.selected is None and m.requires_clarification
               for m in envelope.mentions)
    return envelope


def test_only_unresolved_or_ambiguous_mentions_enter_the_queue():
    exact = replace(unresolved(), mentions=(), original_query="已解决问句")
    rows = QUEUE.queue_rows([exact, unresolved()])
    assert len(rows) == 1
    assert rows[0]["alias"] == "形策"
    assert rows[0]["resolution_status"] == "ambiguous"
    assert rows[0]["count"] == 1


def test_duplicate_rows_aggregate_without_saving_the_original_query():
    rows = QUEUE.queue_rows([unresolved(), unresolved()])
    assert len(rows) == 1 and rows[0]["count"] == 2
    blob = json.dumps(rows[0], ensure_ascii=False)
    assert "original_query" not in rows[0]
    assert "形策有哪些课" not in blob
    assert rows[0]["catalog_sha256"]
    assert rows[0]["aliases_sha256"]
    assert rows[0]["resolver_version"] == ER.RESOLVER_VERSION


def test_queue_shape_cannot_masquerade_as_a_formal_alias_record():
    row = QUEUE.queue_rows([unresolved()])[0]
    assert "review_status" not in row
    assert "reviewed_by" not in row
    assert "canonical" not in row
    assert "canonical_family" not in row
    assert row["candidate_entities"]


@pytest.mark.parametrize("query", [
    "手机号13800138000，形策有哪些课",
    "邮箱student@example.com，形策有哪些课",
    "学号：202512345678，形策有哪些课",
    "姓名：张三，形策有哪些课",
    "班级：形策2501班，形策有哪些课",
])
def test_personal_information_stops_before_any_write(tmp_path, query):
    envelope = replace(unresolved(), original_query=query)
    path = tmp_path / "pending-aliases.jsonl"
    path.write_bytes(b"OLD")
    with pytest.raises(QUEUE.QueuePrivacyError):
        QUEUE.write_queue(path, [envelope])
    assert path.read_bytes() == b"OLD"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_is_reproducible_and_does_not_touch_formal_aliases(tmp_path):
    formal = ROOT / "backend" / "data" / "entity_aliases.jsonl"
    before = hashlib.sha256(formal.read_bytes()).hexdigest()
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    QUEUE.write_queue(left, [unresolved(), unresolved()])
    QUEUE.write_queue(right, [unresolved(), unresolved()])
    assert left.read_bytes() == right.read_bytes()
    assert QUEUE.load_queue(left)[0]["count"] == 2
    assert hashlib.sha256(formal.read_bytes()).hexdigest() == before
