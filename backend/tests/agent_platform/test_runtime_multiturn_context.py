# -*- coding: utf-8 -*-
"""跨用户轮次恢复：缺参 → pending_task；用户纠正 → 旧实体失效。

这两条走真实的 resolver + `graph.validate_entity_calls` + 持久化，不调模型；
"模型本轮发出的调用" 以 `tool_calls` 夹具给出。

Run:
  D:\\venv-guet-ppt\\Scripts\\python.exe -m pytest backend/tests/agent_platform/test_runtime_multiturn_context.py -q
"""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "训练集"))

from conversations import conversation_context as CC  # noqa: E402
from conversations import conversation_context_store as store  # noqa: E402
from database import db as dbmod  # noqa: E402
from entities import entity_prior  # noqa: E402
from entities import entity_resolver as ER  # noqa: E402
from agent.runtime import graph  # noqa: E402

RESOLVER = ER.build_production_resolver()
CATALOG = json.loads((ROOT / "backend/data/entity_catalog.json").read_text(encoding="utf-8"))
MAJOR_ID = {row["canonical_name"]: row["entity_id"]
            for row in CATALOG["entities"]["major"]}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    dbmod.Conversation.__table__.create(engine)
    local = sessionmaker(bind=engine)()
    conv = dbmod.Conversation(user_id=1, title="multiturn-test")
    local.add(conv)
    local.commit()
    local.refresh(conv)
    local.conversation = conv
    try:
        yield local
    finally:
        local.close()


def _validate(question, tool_calls, prior):
    envelope = ER.resolve_question(RESOLVER, question)
    return graph.validate_entity_calls({
        "resolution": envelope,
        "tool_calls": tool_calls,
        "prior_context": prior.model_dump(mode="json") if prior else None,
    })


def _call_log(name, validated, observation_sha="d" * 64):
    return [{"name": name, "status": "success",
             "canonical_args": dict(validated["validated_calls"][0]["arguments"]),
             "observation_sha256": observation_sha}]


def test_missing_field_clarification_persists_and_resumes(session):
    conv = session.conversation

    first = _validate("《数字逻辑与计算机组成》是多少学分？",
                      [{"name": "query_course",
                        "arguments": {"course": "数字逻辑与计算机组成"}}],
                      store.load_context(session, conv.id).prior_context)
    assert first["validation_decision"] == "reject"
    assert first["call_validations"][0]["reason"] == "bad_arg:major"

    after_first = store.record_turn(session, conv.id,
                                    validations=first["call_validations"],
                                    calls=[])
    assert after_first.revision == 2
    assert after_first.pending_task is not None
    assert after_first.pending_task.task_type == "query_course"
    assert after_first.pending_task.missing_fields == ("major",)
    assert after_first.pending_task.created_turn == 1
    assert after_first.pending_task.last_updated_turn == 1

    loaded = store.load_context(session, conv.id)
    second = _validate("软件工程专业的《数字逻辑与计算机组成》是多少学分？",
                       [{"name": "query_course",
                         "arguments": {"major": "软件工程",
                                       "course": "数字逻辑与计算机组成"}}],
                       loaded.prior_context)
    assert second["validation_decision"] == "allow"
    after_second = store.record_turn(
        session, conv.id, validations=second["call_validations"],
        calls=_call_log("query_course", second))

    reloaded = store.load_context(session, conv.id)
    assert after_second.revision == 3
    assert reloaded.pending_task is None
    entities = {one.entity_id: one for one in reloaded.prior_context.entities}
    major = entities[MAJOR_ID["软件工程"]]
    assert major.active and major.source == "tool_canonical_args"
    assert major.source_turn == 2
    assert len(major.evidence_identity or "") == 64


def test_correction_sequence_uses_only_the_replacement_major(session):
    conv = session.conversation
    old_id = MAJOR_ID["交通工程"]
    new_id = MAJOR_ID["土木工程"]
    old = entity_prior.PriorEntity(
        entity_type="major", canonical_name="交通工程", entity_id=old_id,
        source="resolver_selected", source_turn=1)
    base = store.load_context(session, conv.id)
    seeded = CC.apply_turn_update(
        base, entities=(old,), facts=(), unresolved=(), correction=None,
        pending_task=None)
    store.save_context(session, conv.id, seeded, expected_revision=1)

    question = "不是，我问的是土木工程的《工程力学1》"
    validated = _validate(question,
                          [{"name": "query_course",
                            "arguments": {"major": "土木工程",
                                          "course": "工程力学1"}}],
                          seeded.prior_context)
    assert validated["validation_decision"] == "allow"

    replacement = entity_prior.PriorEntity(
        entity_type="major", canonical_name="土木工程", entity_id=new_id,
        source="human_correction", source_turn=2)
    correction = CC.UserCorrection(turn_index=2, replaced_entity_id=old_id,
                                   replacement_entity_id=new_id)
    corrected = store.record_turn(
        session, conv.id, validations=validated["call_validations"],
        calls=_call_log("query_course", validated), correction=correction,
        explicit_entities=(replacement,))

    by_id = {one.entity_id: one for one in corrected.prior_context.entities}
    assert by_id[old_id].active is False
    assert by_id[new_id].active is True
    assert by_id[new_id].source == "human_correction"

    # 后续轮次只认替换后的专业名 —— 旧专业必须被拒绝，且不靠 top-1 猜。
    later = ER.resolve_question(RESOLVER, "那土木工程的《工程力学1》呢？")
    old_major = graph.validate_entity_calls({
        "resolution": later,
        "tool_calls": [{"name": "query_course",
                        "arguments": {"major": "交通工程",
                                      "course": "工程力学1"}}],
        "prior_context": corrected.prior_context.model_dump(mode="json"),
    })
    assert old_major["validation_decision"] == "reject"
    assert old_major["validated_calls"] == []

    new_major = graph.validate_entity_calls({
        "resolution": later,
        "tool_calls": [{"name": "query_course",
                        "arguments": {"major": "土木工程",
                                      "course": "工程力学1"}}],
        "prior_context": corrected.prior_context.model_dump(mode="json"),
    })
    assert new_major["validation_decision"] == "allow"
    assert new_major["validated_calls"][0]["arguments"]["major"] == "土木工程"
