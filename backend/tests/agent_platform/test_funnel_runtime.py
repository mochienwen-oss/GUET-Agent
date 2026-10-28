"""Funnel ownership and recovery; no model, server or external classifier."""
import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversations import conversation_context as CC
from entities import entity_prior
from entities import entity_resolver
from agent.runtime import entity_validation, funnel, graph


def pending_context(tool="query_major_requirements", missing=("major",)):
    pending = CC.PendingTask(task_type=tool, missing_fields=missing,
                             created_turn=1, last_updated_turn=1)
    return CC.ConversationContext(
        conversation_id="1", revision=2, pending_task=pending,
        prior_context=entity_prior.PriorContext(conversation_id="1"))


def test_cancel_is_a_complete_operation_not_a_keyword_match():
    context = pending_context()
    assert funnel.rule_route("取消", context).action == "cancel"
    for text in ("不要取消", "取消之后会怎样", "取消，改查学位条件"):
        assert funnel.rule_route(text, context).action == "agent"
    assert funnel.rule_route("取消", None).action == "agent"


def test_context_completes_only_the_confirmed_pending_task():
    resolver = entity_resolver.build_production_resolver()
    context = pending_context()
    q = "计算机科学与技术"
    envelope = entity_resolver.resolve_question(resolver, q)
    proposal = funnel.context_route(q, context, envelope)
    assert proposal.action == "tool" and proposal.layer == "context"
    assert proposal.arguments == {"major": "计算机科学与技术"}
    for text in ("人工智能", "人工智能和软件工程", "人工智能能不能转专业", "不是人工智能", "取消，人工智能"):
        result = funnel.context_route(text, context, entity_resolver.resolve_question(resolver, text))
        assert result.action == "agent"


def test_context_does_not_guess_the_other_missing_argument():
    resolver = entity_resolver.build_production_resolver()
    q = "人工智能"
    context = pending_context("query_course", ("course",))
    proposal = funnel.context_route(q, context, entity_resolver.resolve_question(resolver, q))
    assert proposal.action == "agent"
    stale = context.model_copy(update={"revision": 4})
    assert funnel.context_route(q, stale, entity_resolver.resolve_question(resolver, q)).action == "agent"


def test_graph_cancel_never_calls_model_and_emits_one_done(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("cancel must not invoke model")
    monkeypatch.setattr(graph.model, "decide", forbidden)
    context = pending_context()
    async def drive():
        return [item async for item in graph.run_stream(
            [], "取消", conversation_context=context, pending_task=context.pending_task)]
    events = asyncio.run(drive())
    done = [value for kind, value in events if kind == "done"]
    assert len(done) == 1 and done[0]["calls"] == []
    routing = done[0]["answer_audit"]["routing"]
    assert routing["layer"] == "rule"
    assert routing["context_action"] == {"clear_pending": True, "expected_revision": 2}


def test_context_route_keeps_skill_validation(monkeypatch):
    from types import SimpleNamespace
    resolver = entity_resolver.build_production_resolver()
    q = "计算机科学与技术"
    context = pending_context()
    proposal = funnel.context_route(q, context, entity_resolver.resolve_question(resolver, q))
    state = {"question": q, "resolution": entity_resolver.resolve_question(resolver, q),
             "skill_manifest": SimpleNamespace(skill_id="narrow", version="1", allowed_tools=[])}
    state.update(funnel.commit_route(proposal, context))
    monkeypatch.setattr(entity_validation, "validate_skill_call", lambda *args: (_ for _ in ()).throw(ValueError("blocked")))
    assert graph.validate_entity_calls(state)["validation_decision"] == "reject"


def test_context_lookup_uses_real_validation_and_skips_planning(monkeypatch):
    from test_grounded_answers import draft_for
    context = pending_context()
    async def forbidden(*args, **kwargs):
        pytest.fail("confirmed pending lookup must not need model planning")
    async def answer(messages, packet, **kwargs):
        assert messages[0]["role"] == "system"
        assert any(m["role"] == "user" and m["content"] == "计算机科学与技术" for m in messages)
        assert any(m["role"] == "tool" for m in messages)
        return draft_for(packet, "按该专业培养方案说明要求。", [r["evidence_id"] for r in packet["evidence"]])
    monkeypatch.setattr(graph.model, "decide", forbidden)
    monkeypatch.setattr(graph, "_stream_round", forbidden)
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    async def drive():
        return [item async for item in graph.run_stream(
            [], "计算机科学与技术", conversation_context=context, pending_task=context.pending_task)]
    events = asyncio.run(drive())
    done = next(payload for kind, payload in events if kind == "done")
    assert [c["name"] for c in done["calls"]] == ["query_major_requirements"]
    assert done["call_validations"][0]["decision"] == "allow"
    assert done["answer_audit"]["routing"]["layer"] == "context"
