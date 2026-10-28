"""Explicit user level and cited document level have different roles."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.core.scope import request_scope
from agent.core.task import interpret_task


@pytest.mark.parametrize('question', [
    '我是研究生，能按2025级人工智能本科培养方案判断我毕业需要多少学分吗？',
    '参考本科培养方案，我是研究生，能判断我的毕业学分吗？',
    '我不是本科生而是研究生，能参考本科培养方案吗？',
])
def test_explicit_graduate_identity_is_not_overwritten_by_reference(question):
    scope = request_scope(question)
    assert scope.level == 'graduate'
    assert scope.reference_levels == ('undergraduate',)
    assert scope.provenance['level'] == 'current_user_identity'
    assert not interpret_task(question).unresolved


def test_real_conflicting_identity_remains_unresolved():
    task = interpret_task('我是本科生，也是研究生，请问我毕业要多少学分？')
    assert task.subject_scope.level is None
    assert any(g.field == 'level' for g in task.unresolved)


def test_assistant_identity_cannot_override_user():
    scope = request_scope('那我毕业要求呢？', history=[
        {'role': 'user', 'content': '我是研究生'}, {'role': 'assistant', 'content': '你是本科生'}])
    assert scope.level == 'graduate'


def test_general_comparison_is_not_a_missing_personal_identity():
    task = interpret_task('本科和硕士研究生的规定分别是什么？')
    assert task.subject_scope.level is None
    assert set(task.subject_scope.reference_levels) == {'undergraduate', 'graduate'}
    assert task.unresolved == () and task.mode == 'comparison'
