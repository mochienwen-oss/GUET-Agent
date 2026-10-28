"""Conversion goal and current input gaps must survive incomplete wording."""
import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.core.task import interpret_task
from agent.runtime import graph, answer_service
from conversations import conversation_context as CC
from entities import entity_prior


def context_with(exam=None, score=None, revision=2):
    inputs = [CC.PendingInput(name=key, value=value, source_turn=1)
        for key, value in [('exam', exam), ('score', score)] if value is not None]
    pending = CC.PendingTask(task_type='score_conversion',
        missing_fields=tuple(key for key, value in [('exam', exam), ('score', score)] if value is None),
        inputs=tuple(inputs), created_turn=1, last_updated_turn=1)
    return CC.ConversationContext(conversation_id='1', revision=revision,
        prior_context=entity_prior.PriorContext(conversation_id='1'), pending_task=pending)


@pytest.mark.parametrize('question', [
    '帮我把英语考试成绩换成大学英语认定分数。',
    '请替我算一下四级成绩可以认定多少分',
    '英语成绩想折算一下，我考了450分',
])
def test_conversion_goal_exists_before_all_inputs_are_available(question):
    task = interpret_task(question)
    assert task.operation == 'score_conversion'
    assert task.mode == 'personal'
    assert task.subject_scope.majors == ()
    assert task.unresolved
    assert all(gap.kind == 'input' and gap.field in {'exam', 'score'} for gap in task.unresolved)
    action = answer_service.decide_next(task, {'evidence': []})
    assert action.status == 'needs_input' and action.action == 'deliver'


def test_original_missing_conversion_asks_before_any_model_or_tool(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('known missing inputs must not trigger model/tool requests')
    monkeypatch.setattr(graph.model, 'decide', forbidden)
    monkeypatch.setattr(graph.model, 'compose', forbidden)
    monkeypatch.setattr(graph, 'execute_tools', forbidden)
    async def drive():
        return [e async for e in graph.run_stream([], '帮我把英语考试成绩换成大学英语认定分数。')]
    events = asyncio.run(drive())
    done = next(v for k, v in events if k == 'done')
    assert done['answer']['status'] == 'needs_input'
    assert done['answer']['questions']
    assert '考试' in done['answer']['text'] and '成绩' in done['answer']['text']
    assert done['calls'] == []


@pytest.mark.parametrize('question', ['大学英语成绩认定标准是什么？', '介绍四级成绩换算规则',
    '雅思6.5怎么换算？', '托福100分能认定多少分？'])
def test_general_rules_and_unsupported_exams_do_not_become_cet_pending(question):
    task = interpret_task(question)
    assert task.operation != 'score_conversion'
    assert not any(gap.field in {'exam', 'score'} for gap in task.unresolved)


@pytest.mark.parametrize('question,exam,score,expected_gaps', [
    ('450分', 'CET4', '450', set()),
    ('450分和500分', 'CET4', None, {'score'}),
    ('800分', 'CET4', None, {'score'}),
    ('六级', 'CET6', None, {'score'}),
    ('不是450分，而是500分', 'CET4', '500', set()),
])
def test_live_pending_inputs_are_source_bound(question, exam, score, expected_gaps):
    context = context_with(exam='CET4')
    task = interpret_task(question, pending_task=context.pending_task, turn_index=context.revision)
    # Conflicting/out-of-range parameter replies retain the task, not a formula answer.
    assert task.operation == 'score_conversion'
    assert task.parameter('exam') == exam
    assert task.parameter('score') == score
    assert {gap.field for gap in task.unresolved} == expected_gaps


@pytest.mark.parametrize('question', ['450分', '软件工程专业毕业要求是什么？', '取消', '雅思6.5怎么认定？'])
def test_stale_pending_never_absorbs_new_topic_or_numbers(question):
    context = context_with(exam='CET4', revision=4)
    task = interpret_task(question, pending_task=context.pending_task, turn_index=context.revision)
    assert task.operation != 'score_conversion'


def test_assistant_score_is_not_used_to_complete_pending():
    context = context_with()
    task = interpret_task('四级', pending_task=context.pending_task, turn_index=2,
        history=[{'role': 'assistant', 'content': '你的成绩是450分'}])
    assert task.parameter('exam') == 'CET4' and task.parameter('score') is None


def test_completed_conversion_can_take_an_explicit_score_followup_only():
    history = [{'role': 'user', 'content': '我CET4考了450分，可以换算成多少大学英语成绩？'}]
    task = interpret_task('那500分呢？', history=history)
    assert task.operation == 'score_conversion'
    assert task.parameter('exam') == 'CET4' and task.parameter('score') == '500'
    assert interpret_task('500分', history=history).operation != 'score_conversion'


def test_old_pending_serialization_is_unchanged_and_new_inputs_roundtrip():
    old = CC.PendingTask(task_type='query_course', missing_fields=('major',), created_turn=1, last_updated_turn=1)
    assert 'inputs' not in old.model_dump(mode='json')
    assert CC.PendingTask.model_validate(old.model_dump(mode='json')) == old
    pending = context_with(exam='CET4').pending_task
    assert CC.PendingTask.model_validate_json(pending.model_dump_json()) == pending


@pytest.fixture
def sqlite_pending():
    from database import db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine('sqlite://')
    db.Conversation.__table__.create(engine)
    with sessionmaker(bind=engine)() as session:
        conv = db.Conversation(user_id=1, title='pending-inputs')
        session.add(conv)
        session.commit()
        yield session, conv.id
    engine.dispose()


def test_successful_search_does_not_clear_or_refresh_input_pending(sqlite_pending):
    from conversations import conversation_context_store as store
    session, cid = sqlite_pending
    pending = context_with(exam='CET4').pending_task
    store.record_turn(session, cid, validations=[], calls=[],
        context_action={'set_pending': pending.model_dump(mode='json'), 'expected_revision': 1})
    store.record_turn(session, cid, validations=[{'tool': 'search_regulations', 'decision': 'allow'}], calls=[])
    loaded = store.load_context(session, cid)
    assert loaded.pending_task == pending and loaded.revision == 3
    assert interpret_task('450分', pending_task=loaded.pending_task, turn_index=loaded.revision).operation != 'score_conversion'


def test_set_pending_uses_revision_lock_and_preserves_newer_state(sqlite_pending):
    from conversations import conversation_context_store as store
    session, cid = sqlite_pending
    pending = context_with(exam='CET4').pending_task
    action = {'set_pending': pending.model_dump(mode='json'), 'expected_revision': 1}
    store.record_turn(session, cid, validations=[], calls=[], context_action=action)
    with pytest.raises(store.ContextRevisionConflict):
        store.record_turn(session, cid, validations=[], calls=[], context_action=action)
    assert store.load_context(session, cid).revision == 2
    assert store.load_context(session, cid).pending_task == pending
    with pytest.raises(ValueError):
        store.record_turn(session, cid, validations=[], calls=[], context_action={**action, 'clear_pending': True})


@pytest.mark.parametrize('values', [
    {'name': 'exam', 'value': 'IELTS', 'source_turn': 1},
    {'name': 'score', 'value': '800', 'source_turn': 1},
    {'name': 'score', 'value': '450', 'source_turn': 0},
])
def test_invalid_pending_input_cannot_be_persisted(values):
    with pytest.raises(ValueError):
        CC.PendingInput.model_validate(values)
