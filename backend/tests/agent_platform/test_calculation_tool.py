"""Source-bound arithmetic and real graph execution without network/model calls."""
import asyncio
import re
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.core import EvidenceLedger, Source, ToolResult
from agent.core.calculations import conversion_rules, calculate
from agent.runtime import grounded_answer as G, graph, registry
from test_grounded_answers import draft_for, ledger


def policy():
    return ledger("类别：CET4\n成绩A：425 ≤ A < 567\n大学英语成绩：A × 0.1764\n四舍五入取整", "测试英语认定办法")


def context(question="CET4考了450分，大学英语认定多少分？"):
    return G.calculation_context({"evidence": policy(), "question": question})


@pytest.mark.parametrize("score,expected", [("425", "75"), ("450", "79"), ("566", "100")])
def test_explicit_rule_and_rounding(score, expected):
    ctx = context(f"CET4考了{score}分，换算多少？")
    result = registry.call("calculate", {"rule_id": ctx["rules"][0]["rule_id"], "inputs": {"score": score}}, context=ctx)
    assert result.status == "success" and result.sources == []
    assert result.data["calculation"]["final_result"] == expected
    assert result.data["calculation"]["source_refs"] == list(policy())


@pytest.mark.parametrize("question,score", [("CET4 450或500分", "450"),
                                           ("CET4 450分", "500"), ("CET6 450分", "450"),
                                           ("CET4 800分", "800"), ("CET4 400分", "400")])
def test_wrong_ambiguous_or_out_of_range_input_fails(question, score):
    ctx = context(question)
    result = registry.call("calculate", {"rule_id": ctx["rules"][0]["rule_id"], "inputs": {"score": score}}, context=ctx)
    assert result.status == "tool_failed"


def test_no_context_and_invented_rule_fail():
    assert registry.call("calculate", {"rule_id": "foreign", "inputs": {"score": "450"}}).status == "tool_failed"
    assert registry.call("calculate", {"rule_id": "foreign", "inputs": {"score": "450"}}, context=context()).status == "tool_failed"
    assert registry.call("calculate", {"rule_id": "x", "inputs": {"score": "450", "multiplier": "0.3"}}).error.code == "invalid_args"


def test_conflicting_policy_cannot_be_selected_by_model():
    evidence = policy()
    evidence.update(ledger("CET4|425≤A<567|A×0.2|四舍五入取整", "另一测试办法"))
    rules = conversion_rules(evidence, "initial")
    with pytest.raises(ValueError, match="冲突"):
        calculate(rules[0]["rule_id"], {"score": "450"}, rules=rules, question="CET4 450分", history=[])


def test_assistant_history_cannot_supply_score():
    rules = context()["rules"]
    with pytest.raises(ValueError, match="缺少"):
        calculate(rules[0]["rule_id"], {"score": "450"}, rules=rules,
                  question="CET4能认定多少？", history=[{"role": "assistant", "content": "450分"}])


def test_changing_exam_without_score_does_not_reuse_another_exams_score():
    from agent.core.calculations import user_score
    with pytest.raises(ValueError, match="另一考试"):
        user_score("那六级认定多少？", [{"role": "user", "content": "四级450分"}], "CET6")
    assert user_score("那500分呢？", [{"role": "user", "content": "六级450分"}], "CET6")[0] == 500


@pytest.mark.parametrize("question", ["六级备考花了500元，认定政策是什么？",
                                     "六级500分以上的认定政策是什么？",
                                     "四级换算多少？"])
def test_non_score_or_new_question_cannot_borrow_history(question):
    from agent.core.calculations import user_score
    with pytest.raises(ValueError):
        user_score(question, [{"role": "user", "content": "四级450分"}], "CET4")


def test_general_policy_with_threshold_does_not_require_calculation():
    from agent.core.calculations import calculation_task
    packet = G.evidence_packet({"evidence": policy(), "question": "四级450分以上的认定政策是什么？"})
    assert calculation_task(packet)["status"] == "not_required"
    d = draft_for(packet, "该政策按相应区间换算并四舍五入取整。", list(policy()))
    assert G.render_draft(d, packet)[0]


def test_server_failure_wording_is_a_confirmed_constant_conversion_task():
    from agent.core.calculations import calculation_task
    evidence = ledger("CET6|A≥500|100|四舍五入取整", "测试英语认定办法")
    question = "那六级考到500分，大学英语能认定成多少分？"
    packet = G.evidence_packet({"evidence": evidence, "question": question})
    task = calculation_task(packet)
    assert task["status"] == "pending" and task["can_skip_planning"]
    call = task["call"]
    result = registry.call(call["name"], call["arguments"],
                           context=G.calculation_context({"evidence": evidence, "question": question}))
    assert result.status == "success" and result.data["calculation"]["final_result"] == "100"


def test_no_evidence_cannot_bypass_known_pending_calculation():
    packet = G.evidence_packet({"evidence": policy(), "question": "四级450分换算多少？"})
    with pytest.raises(ValueError, match="未调用计算工具"):
        G.render_draft({"status": "no_evidence", "claims": [], "blocks": [],
                        "slot_coverage": [], "missing": []}, packet)


def test_actual_calculation_task_binds_rule_and_records():
    from agent.core.calculations import calculation_task
    packet = calculated_packet()
    assert calculation_task(packet)["status"] == "complete"
    packet["calculations"][0]["inputs"]["score"] = "425"
    assert calculation_task(packet)["status"] == "pending"


@pytest.mark.parametrize("question", ["四级450分换算多少？顺便问学位条件。",
                                     "四级450分认定多少，学位要求呢？"])
def test_calculation_does_not_short_circuit_an_explicit_compound_goal(question):
    from agent.core.calculations import calculation_task
    packet = G.evidence_packet({"evidence": policy(), "question": question})
    task = calculation_task(packet)
    assert task["status"] == "pending" and task["can_skip_planning"] is False


def test_graph_recovers_skipped_calculator_before_any_final_call(monkeypatch):
    source = next(iter(policy().values())).source
    original_call = registry.call
    def local_call(name, args, **kwargs):
        if name == "search_regulations":
            return ToolResult(tool=name, ok=True, status="success", sources=[source], meta={"args": args})
        return original_call(name, args, **kwargs)
    async def first(messages, **kwargs):
        return {"type": "tool_calls", "calls": [{"name": "search_regulations", "arguments": {"query": "英语认定"}}]}
    async def skipped(*args, **kwargs):
        pytest.fail("confirmed calculation should not need another planning round")
    async def final(messages, packet, **kwargs):
        assert len(packet["calculations"]) == 1
        record = packet["calculations"][0]
        d = draft_for(packet, "四级450分可认定为79分。", record["source_refs"])
        d["claims"][0]["calculation_ids"] = [record["calculation_id"]]
        return d
    monkeypatch.setattr(registry, "call", local_call)
    monkeypatch.setattr(graph.model, "decide", first)
    monkeypatch.setattr(graph, "_stream_round", skipped)
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", final)
    async def drive():
        return [item async for item in graph.run_stream([], "四级450分换算多少？")]
    events = asyncio.run(drive())
    done = next(value for kind, value in events if kind == "done")
    assert [call["name"] for call in done["calls"]] == ["search_regulations", "calculate"]
    assert done["answer_audit"]["calculation_preflight"] == "executed"
    assert "79分" in "".join(value for kind, value in events if kind == "delta")


def calculated_packet():
    state = {"evidence": policy(), "question": "CET4考了450分，换算多少？"}
    ctx = G.calculation_context(state)
    result = registry.call("calculate", {"rule_id": ctx["rules"][0]["rule_id"], "inputs": {"score": "450"}}, context=ctx)
    return G.evidence_packet({**state, "calculations": [result.data["calculation"]]})


def test_model_prose_is_kept_but_wrong_result_is_rejected():
    packet = calculated_packet()
    d = draft_for(packet, "你的四级450分按该规则认定为79分，采用四舍五入取整。", list(policy()))
    d["claims"][0]["calculation_ids"] = [packet["calculations"][0]["calculation_id"]]
    assert G.render_draft(d, packet)[0] == d["claims"][0]["text"]
    d["claims"][0]["text"] = "你的成绩认定为80分。"
    with pytest.raises(ValueError, match="计算结果"):
        G.render_draft(d, packet)


def test_policy_boundary_cannot_replace_the_users_input():
    packet = calculated_packet()
    d = draft_for(packet, "你的四级425分按规则认定为79分。", list(policy()))
    d["claims"][0]["calculation_ids"] = [packet["calculations"][0]["calculation_id"]]
    with pytest.raises(ValueError, match="输入成绩"):
        G.render_draft(d, packet)


@pytest.mark.parametrize("stop", ["budget", "failure"])
def test_preflight_stops_without_final_generation_on_incomplete_execution(monkeypatch, stop):
    from core.config import settings
    calls = []
    def fail(name, args, **kwargs):
        calls.append(name)
        return ToolResult(tool=name, ok=False, status="tool_failed")
    async def forbidden(*args, **kwargs):
        pytest.fail("unfinished calculation cannot enter final model generation")
    monkeypatch.setattr(registry, "call", fail)
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", forbidden)
    state = {"evidence": policy(), "question": "四级450分换算多少？"}
    if stop == "budget":
        state["tool_calls_used"] = settings.agent_max_tool_calls
    out = asyncio.run(graph._grounded_final(state, graph.init_messages([], state["question"]), 1, None))
    assert out["answer_audit"]["calculation_preflight"] == "blocked"
    assert calls == ([] if stop == "budget" else ["calculate"])
    assert "79" not in out["final_answer"]


def test_missing_tool_call_is_not_hidden_by_automatic_calculation():
    packet = G.evidence_packet({"evidence": policy(), "question": "CET4 450分换算多少？"})
    assert packet["calculations"] == []
    with pytest.raises(ValueError, match="未调用计算工具"):
        G.render_draft(draft_for(packet, "可认定为79分。", list(policy())), packet)


def test_search_observation_exposes_rules_and_calculation_advances_graph(monkeypatch):
    source = next(iter(policy().values())).source
    original_call = registry.call
    def local_call(name, args, **kwargs):
        if name == "search_regulations":
            return ToolResult(tool=name, ok=True, status="success", sources=[source], meta={"args": args})
        return original_call(name, args, **kwargs)
    monkeypatch.setattr(registry, "call", local_call)
    state = {"question": "CET4 450分换算多少？", "tool_calls": [
        {"name": "search_regulations", "arguments": {"query": "英语认定"}}]}
    state.update(asyncio.run(graph.execute_tools(state)))
    assert "可用计算规则" in state["observations"][0]
    state.update(graph.append_evidence(state))
    rule = G.calculation_context(state)["rules"][0]
    state["tool_calls"] = [{"name": "calculate", "arguments": {"rule_id": rule["rule_id"], "inputs": {"score": "450"}}}]
    state["no_new_evidence_rounds"] = 1
    state.update(asyncio.run(graph.execute_tools(state)))
    state.update(graph.append_evidence(state))
    assert len(state["calculations"]) == 1 and state["calculations_added"] == 1
    assert state["evidence_added"] == 0 and state["no_new_evidence_rounds"] == 0
    assert state["tool_calls_used"] == 2
    state.update(asyncio.run(graph.execute_tools(state)))
    assert state["calculations_added"] == 0 and len(state["calculations"]) == 1


def test_stale_calculation_source_is_not_accepted(monkeypatch):
    from documents import document_availability
    ctx = context()
    original = document_availability.require_ready
    def reject_revision(expected_revision=None):
        if expected_revision is not None:
            raise document_availability.SourceUnavailable("版本改变")
        return original()
    monkeypatch.setattr(document_availability, "require_ready", reject_revision)
    result = registry.call("calculate", {"rule_id": ctx["rules"][0]["rule_id"], "inputs": {"score": "450"}}, context=ctx)
    assert result.error.code == "source_unavailable"


def test_real_graph_runs_search_calculate_and_natural_final(monkeypatch):
    source = next(iter(policy().values())).source
    original_call = registry.call
    def local_call(name, args, **kwargs):
        if name == "search_regulations":
            return ToolResult(tool=name, ok=True, status="success", sources=[source], meta={"args": args})
        return original_call(name, args, **kwargs)
    async def first_round(messages, **kwargs):
        return {"type": "tool_calls", "calls": [{"name": "search_regulations", "arguments": {"query": "英语认定"}}]}
    async def later_round(messages, choice, emit, **kwargs):
        tools = [m for m in messages if m["role"] == "tool"]
        if len(tools) == 1:
            rule = re.search(r'"rule_id": "([^"]+)"', tools[0]["content"])[1]
            return "", [{"name": "calculate", "arguments": {"rule_id": rule, "inputs": {"score": "450"}}}]
        return "准备回答", []
    async def final(messages, packet, **kwargs):
        record = packet["calculations"][0]
        d = draft_for(packet, "按该规则，你的四级成绩可认定为79分，结果已四舍五入。", record["source_refs"])
        d["claims"][0]["calculation_ids"] = [record["calculation_id"]]
        return d
    monkeypatch.setattr(registry, "call", local_call)
    monkeypatch.setattr(graph.model, "decide", first_round)
    monkeypatch.setattr(graph, "_stream_round", later_round)
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", final)
    async def drive():
        return [event async for event in graph.run_stream([], "CET4 450分换算多少？")]
    events = asyncio.run(drive())
    done = next(value for kind, value in events if kind == "done")
    assert [call["name"] for call in done["calls"]] == ["search_regulations", "calculate"]
    assert "79分" in "".join(value for kind, value in events if kind == "delta")
    assert len(done["answer_audit"]["calculations"]) == 1


def test_calculation_context_does_not_leak_between_requests():
    rule = context()["rules"][0]
    empty = G.calculation_context({"question": "CET4 450分换算多少？"})
    result = registry.call("calculate", {"rule_id": rule["rule_id"], "inputs": {"score": "450"}}, context=empty)
    assert result.status == "tool_failed" and "本轮" in result.error.detail


def test_foreign_calculation_record_cannot_authorize_prose():
    packet = calculated_packet()
    d = draft_for(packet, "认定为79分。", list(policy()))
    d["claims"][0]["calculation_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="不存在的计算记录"):
        G.render_draft(d, packet)


def test_calculation_prose_cannot_add_an_unrelated_numeric_result():
    packet = calculated_packet()
    d = draft_for(packet, "认定为79分，另外还能获得88分。", list(policy()))
    d["claims"][0]["calculation_ids"] = [packet["calculations"][0]["calculation_id"]]
    with pytest.raises(ValueError, match="未授权数值"):
        G.render_draft(d, packet)


def test_cancelled_request_does_not_execute_calculator():
    from agent.core.request_control import request_scope
    ctx = context()
    with request_scope(0), pytest.raises(TimeoutError):
        registry.call("calculate", {"rule_id": ctx["rules"][0]["rule_id"], "inputs": {"score": "450"}}, context=ctx)
