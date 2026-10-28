# -*- coding: utf-8 -*-
"""ConversationContext 持久化：null 上下文、revision、乐观锁、未知版本。

Run:
  D:\\venv-guet-ppt\\Scripts\\python.exe -m pytest backend/tests/test_conversation_context_store.py -q
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "训练集"))

from conversations import conversation_context as CC  # noqa: E402
from conversations import conversation_context_store as store  # noqa: E402
from database import db as dbmod  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    dbmod.Conversation.__table__.create(engine)
    local = sessionmaker(bind=engine)()
    conv = dbmod.Conversation(user_id=1, title="context-test")
    local.add(conv)
    local.commit()
    local.refresh(conv)
    local.conversation = conv
    try:
        yield local
    finally:
        local.close()


def advance(context):
    return CC.apply_turn_update(
        context, entities=(), facts=(), unresolved=(), correction=None,
        pending_task=None)


def test_existing_conversation_with_null_context_loads_clean_v1(session):
    conv = session.conversation
    assert conv.context_json is None
    context = store.load_context(session, conv.id)
    assert context.conversation_id == str(conv.id)
    assert context.context_version == CC.CONTEXT_VERSION
    assert context.revision == 1
    assert context.prior_context.entities == ()
    assert context.prior_context.trusted_facts() == ()
    assert context.pending_task is None


def test_save_increments_revision_and_persists(session):
    conv = session.conversation
    context = store.load_context(session, conv.id)
    saved = store.save_context(session, conv.id, advance(context),
                               expected_revision=context.revision)
    assert saved.revision == 2
    session.expire_all()
    reloaded = store.load_context(session, conv.id)
    assert reloaded.revision == 2
    assert conv.context_revision == 2
    assert conv.context_json["context_version"] == CC.CONTEXT_VERSION


def test_a_stale_revision_is_rejected(session):
    conv = session.conversation
    context = store.load_context(session, conv.id)
    store.save_context(session, conv.id, advance(context), expected_revision=1)

    stale_next = advance(context)      # 基于 revision=1 的过期快照
    with pytest.raises(store.ContextRevisionConflict):
        store.save_context(session, conv.id, stale_next, expected_revision=1)

    session.expire_all()
    assert store.load_context(session, conv.id).revision == 2


def test_unknown_context_version_fails_closed(session):
    conv = session.conversation
    conv.context_json = {
        "conversation_id": str(conv.id),
        "context_version": "conversation-context/999",
        "revision": 2,
        "prior_context": {
            "conversation_id": str(conv.id),
            "entities": [], "facts": [], "unresolved": [],
        },
    }
    conv.context_revision = 2
    session.commit()

    with pytest.raises(store.ContextVersionError):
        store.load_context(session, conv.id)


def test_cancel_pending_is_persisted_and_revision_bound(session):
    conv = session.conversation
    context = store.load_context(session, conv.id)
    pending = CC.PendingTask(task_type="query_major_requirements", missing_fields=("major",),
                             created_turn=1, last_updated_turn=1)
    updated = CC.apply_turn_update(context, entities=(), facts=(), unresolved=(),
                                   correction=None, pending_task=pending)
    store.save_context(session, conv.id, updated, expected_revision=1)
    action = {"clear_pending": True, "expected_revision": 2}
    with pytest.raises(store.ContextRevisionConflict):
        store.record_turn(session, conv.id, validations=[], calls=[],
                          context_action={**action, "expected_revision": 1})
    assert store.load_context(session, conv.id).pending_task is not None
    store.record_turn(session, conv.id, validations=[], calls=[], context_action=action)
    assert store.load_context(session, conv.id).pending_task is None
