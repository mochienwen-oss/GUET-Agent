"""Targeted answer-boundary checks; fixtures are synthetic, not historical drafts."""
import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.core import EvidenceLedger, Source, ToolResult
from agent.runtime import grounded_answer as G, graph
from test_grounded_answers import draft_for, ledger


def course_state(*, status="success", major="人工智能"):
    facts = {"major": major, "college": "人工智能学院",
             "course": {"name": "计算机网络", "credits": 3},
             "indicators": [{"id": i, "text": text} for i, text in [
                 ("2.1", "识别问题。判断关键环节；保留归属。"),
                 ("3.1", "设计模块。"), ("5.1", "选择工具。")]]}
    source = Source(title="2025级人工智能专业培养方案(主修)",
                    text=json.dumps(facts, ensure_ascii=False, sort_keys=True))
    book = EvidenceLedger()
    book.add("query_course", {"major": major, "course": "计算机网络"}, source)
    result = ToolResult(tool="query_course", ok=True, status=status,
                        data=facts, sources=[source])
    return {"question": "人工智能专业的计算机网络支撑哪些毕业要求指标？",
            "evidence": {x.evidence_id: x for x in book.items()}, "tool_results": [result]}


def test_structured_indicator_records_survive_punctuation():
    packet = G.evidence_packet(course_state())
    records = [u for u in packet["evidence_units"] if u.get("record_id")]
    assert [u["record_id"] for u in records] == ["2.1", "3.1", "5.1"]
    assert records[0]["record"]["text"] == "识别问题。判断关键环节；保留归属。"
    assert all(u["owner"] == {"major": "人工智能", "course": "计算机网络"} for u in records)
    parent = packet["evidence"][0]["text"]
    assert all(parent[u["start"]:u["end"]] == u["text"] for u in records)
    assert all(u["text"] != parent for u in packet["evidence_units"])


def test_record_ownership_and_partial_source_do_not_cross_majors():
    first = course_state()
    second = course_state(status="partial_evidence", major="软件工程")
    packet = G.evidence_packet({**first, "evidence": {**first["evidence"], **second["evidence"]},
                               "tool_results": first["tool_results"] + second["tool_results"]})
    assert len(packet["enumerations"]) == 1
    records = [u for u in packet["evidence_units"] if u.get("record_id") == "2.1"]
    assert {u["owner"]["major"] for u in records} == {"人工智能", "软件工程"}
    assert records[0]["unit_id"] != records[1]["unit_id"]


def test_complete_list_is_delivered_once_with_each_record_reference():
    packet = G.evidence_packet(course_state())
    ids = [packet["evidence"][0]["evidence_id"]]
    d = draft_for(packet, "支撑2.1、3.1、5.1。", ids)
    assert "5.1" in G.render_draft(d, packet)[0]
    d["claims"].append(dict(d["claims"][0], claim_id="c2", text="另有2.1。"))
    d["blocks"].append({"claim_ids": ["c2"]})
    with pytest.raises(ValueError, match="完整枚举"):
        G.render_draft(d, packet)


def test_complete_indicator_list_cannot_be_replaced_by_false_missing():
    packet = G.evidence_packet(course_state())
    d = draft_for(packet, "仅能确认5.1，2.1和3.1缺少依据。", [packet["evidence"][0]["evidence_id"]])
    d["claims"][0]["unit_ids"] = [u["unit_id"] for u in packet["evidence_units"]
                                    if u.get("record_id") == "5.1"]
    with pytest.raises(ValueError, match="完整枚举"):
        G.render_draft(d, packet)


def test_partial_result_does_not_assert_complete_enumeration():
    assert G.evidence_packet(course_state(status="partial_evidence"))["enumerations"] == []


def test_static_policy_number_is_not_an_executed_calculation():
    packet = G.evidence_packet({"evidence": ledger("学生所获成绩为以上各项总和，超过100分以100分计。")})
    d = draft_for(packet, "超过100分以100分计。", [packet["evidence"][0]["evidence_id"]],
                  claim_type="calculation")
    assert G.render_draft(d, packet)[0] == "超过100分以100分计。"


def test_credit_lines_do_not_require_unasked_course_counts():
    brief = G.question_brief({"question": "学业预警分哪几档，各自的挂科学分线是多少？"})
    assert "conditions" not in brief["required_slots"]


def test_general_rule_and_personal_eligibility_are_distinct():
    assert G.question_brief({"question": "人工智能专业允许几年内完成学业？"})["application_mode"] == "general_rule"
    assert G.question_brief({"question": "我是2024级学生，我能申请吗？"})["application_mode"] == "personal"


def test_structure_repair_receives_original_and_cannot_rewrite_facts(monkeypatch):
    obj = ledger("公示3天。")
    seen = []
    async def answer(messages, packet, **kwargs):
        seen.append(kwargs)
        d = draft_for(packet, "公示3天。", list(obj))
        if len(seen) == 1:
            d["slot_coverage"][0]["claim_ids"] = ["foreign"]
        else:
            d["claims"][0]["text"] = "公示5天。"
        return d
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph._grounded_final({"question": "公示多久？", "evidence": obj}, [], 0, None))
    assert seen[1]["previous_draft"]["claims"][0]["text"] == "公示3天。"
    assert seen[1]["repair_mode"] == "structure"
    assert out["final_answer"] == G.INVALID_ANSWER
    assert len(out["answer_audit"]["draft_attempts"]) == 2
    assert "改变" in out["answer_audit"]["draft_attempts"][1]["error"]


def test_json_failure_is_regeneration_not_fake_local_repair(monkeypatch):
    obj = ledger("公示3天。")
    seen = []
    async def answer(messages, packet, **kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise ValueError("JSON错误")
        return draft_for(packet, "公示3天。", list(obj))
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph._grounded_final({"question": "公示多久？", "evidence": obj}, [], 0, None))
    assert out["final_answer"] == "公示3天。"
    assert seen[1]["repair_mode"] == "regenerate" and seen[1]["previous_draft"] is None


@pytest.mark.parametrize("first,second,accepted", [
    ("2025级允许3至6年。", "2025 级允许 3 至 6 年。", True),
    ("公示3天。", "公示5天。", False),
    ("not able", "notable", False),
    ("分值1 0分。", "分值10分。", False),
])
def test_structure_repair_ignores_only_layout_not_changed_tokens(monkeypatch, first, second, accepted):
    obj = ledger(first)
    calls = []
    async def answer(messages, packet, **kwargs):
        calls.append(kwargs)
        d = draft_for(packet, first if len(calls) == 1 else second, list(obj))
        if len(calls) == 1:
            d["slot_coverage"][0]["claim_ids"] = ["foreign"]
        return d
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph._grounded_final({"question": "规则是什么？", "evidence": obj}, [], 0, None))
    assert out["final_answer"] == (second if accepted else G.INVALID_ANSWER)
