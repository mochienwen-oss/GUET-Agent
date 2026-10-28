"""An API planner can ask multiple natural questions without policy retrieval."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.runtime import graph, model, registry


@pytest.fixture(autouse=True)
def api_mode(monkeypatch):
    from core.config import settings
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(settings, 'llm_api_config_path', str(root / 'deploy/llm-api.json'))


def test_api_clarification_uses_same_planning_call_and_preserves_business_schemas(monkeypatch):
    seen = []
    questions = ['1. 你要查询哪个校区？', '2. 想了解哪个时间段？']
    async def decide(messages, tools):
        seen.append((deepcopy(messages), deepcopy(tools)))
        return {'type': 'tool_calls', 'calls': [{'name': 'request_input', 'arguments': {'questions': questions}}]}
    monkeypatch.setattr(model.llm_client, 'chat_with_tools', decide)
    messages = [{'role': 'user', 'content': '帮我查一下具体情况'}]
    before = deepcopy(messages)
    result = asyncio.run(model.decide(messages))
    assert result == {'type': 'clarification', 'questions': questions}
    assert len(seen) == 1 and messages == before
    assert seen[0][1][:-1] == registry.schemas()
    assert seen[0][1][-1]['function']['name'] == 'request_input'
    assert 'request_input' not in registry.REGISTRY


def test_compiled_graph_delivers_model_questions_without_fact_writer_or_search(monkeypatch):
    questions = ['请问你要查询哪个校区？', '你希望查询明天还是后天？']
    async def decide(messages, tools):
        return {'type': 'tool_calls', 'calls': [{'name': 'request_input', 'arguments': {'questions': questions}}]}
    async def forbidden(*args, **kwargs):
        pytest.fail('clarification must not execute tools or call writer')
    monkeypatch.setattr(model.llm_client, 'chat_with_tools', decide)
    monkeypatch.setattr(model, 'compose', forbidden)
    monkeypatch.setattr(graph, 'execute_tools', forbidden)
    async def drive():
        return [e async for e in graph.run_stream([], '能帮我查查吗？')]
    events = asyncio.run(drive())
    answer = next(v['answer'] for k, v in events if k == 'done')
    assert answer['status'] == 'needs_input'
    assert answer['questions'] == questions
    assert answer['text'] == '\n'.join(questions)
    assert ''.join(v for k, v in events if k == 'delta') == answer['text']
    assert answer['contexts'] == []


@pytest.mark.parametrize('arguments', [{'questions': []}, {'questions': '哪个校区？'},
    {'questions': ['']}, {'questions': ['哪个校区？'], 'answer': '毕业学分为148'}])
def test_malformed_interaction_is_not_silently_treated_as_business_tool(monkeypatch, arguments):
    async def decide(messages, tools):
        return {'type': 'tool_calls', 'calls': [{'name': 'request_input', 'arguments': arguments}]}
    monkeypatch.setattr(model.llm_client, 'chat_with_tools', decide)
    with pytest.raises(ValueError):
        asyncio.run(model.decide([{'role': 'user', 'content': '查一下'}]))


def test_input_request_defers_mixed_business_calls_without_executing_them(monkeypatch):
    async def decide(messages, tools):
        return {'type': 'tool_calls', 'calls': [
            {'name': 'request_input', 'arguments': {'questions': ['哪个校区？']}},
            {'name': 'query_course', 'arguments': {'major': '人工智能', 'course': '操作系统'}}]}
    monkeypatch.setattr(model.llm_client, 'chat_with_tools', decide)
    result = asyncio.run(model.decide([]))
    assert result['type'] == 'clarification'
    assert result['questions'] == ['哪个校区？']
    assert [call['name'] for call in result['deferred_calls']] == ['query_course']


def test_later_planning_round_can_request_input_without_entering_writer(monkeypatch):
    questions = ['还需要你补充具体校区。', '方便说明查询日期吗？']
    async def stream(messages, tools=None, tool_choice=None):
        assert tools[-1] == model.INPUT_REQUEST and tool_choice == 'auto'
        yield ('calls', [{'name': 'request_input', 'arguments': {'questions': questions}}])
    async def forbidden(*args, **kwargs):
        pytest.fail('input request must bypass fact writer')
    monkeypatch.setattr(model.llm_client, 'stream_chat', stream)
    monkeypatch.setattr(model, 'compose', forbidden)
    result = asyncio.run(graph.agent_decide({'question': '帮我查询一下', 'rounds_done': 1}))
    assert result['answer']['status'] == 'needs_input'
    assert result['answer']['questions'] == questions
    assert result['tool_calls'] == []


def test_explicit_legacy_planning_does_not_offer_or_accept_input_action(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, 'llm_api_config_path', '')
    async def decide(messages, tools):
        assert tools == registry.schemas()
        return {'type': 'tool_calls', 'calls': [{'name': 'request_input', 'arguments': {'questions': ['哪个校区？']}}]}
    monkeypatch.setattr(model.llm_client, 'chat_with_tools', decide)
    with pytest.raises(ValueError):
        asyncio.run(model.decide([]))


def test_model_interpreted_conversion_retains_gaps_without_extending_shortcut_regex(monkeypatch):
    from conversations import conversation_context as CC
    from entities import entity_prior
    from agent.core.task import interpret_task
    question = '把英语考试结果折成大学英语认定分，帮我算一算。'
    assert interpret_task(question).operation == 'investigate'
    context = CC.ConversationContext(conversation_id='1', revision=1,
        prior_context=entity_prior.PriorContext(conversation_id='1'))
    async def decide(messages, tools):
        return {'type': 'tool_calls', 'calls': [{'name': 'request_input', 'arguments': {
            'questions': ['你想换算四级还是六级？', '具体考了多少分？'], 'operation': 'score_conversion'}}]}
    monkeypatch.setattr(model.llm_client, 'chat_with_tools', decide)
    async def drive():
        return [e async for e in graph.run_stream([], question, conversation_id=1, conversation_context=context)]
    done = next(v for k, v in asyncio.run(drive()) if k == 'done')
    assert done['answer']['status'] == 'needs_input'
    pending = done['answer_audit']['routing']['context_action']['set_pending']
    assert pending['task_type'] == 'score_conversion' and set(pending['missing_fields']) == {'exam', 'score'}
