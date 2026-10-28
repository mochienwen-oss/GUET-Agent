"""Review counterexamples: local gaps and body-specific bounded repair."""
import asyncio
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.core.task import interpret_task
from agent.runtime import answer_service as A, composition as C, graph, grounded_answer as G
from test_grounded_answers import ledger

MIXED = "我是2024级学生，请参考2025级人工智能培养方案，介绍该方案的修业年限，并说明是否能直接适用于我。"


def mixed_state():
    return {"question": MIXED, "answer_protocol": C.PROTOCOL,
            "evidence": ledger("修业年限为3至6年。", "2025级人工智能专业培养方案(主修)")}


def paragraph(packet, text="修业年限为3至6年。"):
    return {"paragraphs": [{"text": text, "unit_ids": [packet["evidence_units"][0]["unit_id"]],
                            "calculation_ids": []}]}


def test_reference_description_and_personal_gap_are_not_an_all_or_nothing_choice():
    state = mixed_state()
    task = interpret_task(MIXED)
    packet = G.evidence_packet({**state, "task": task})
    assert packet["evidence"][0]["applicability"] == "reference_only"
    action = A.decide_next(task, packet)
    assert action.action == "compose" and action.status == "partial"
    assert action.gaps[0]["affects"] == "personal_applicability"


def test_irrelevant_identity_conflict_does_not_block_general_rule():
    q = "我是2024级，也是2025级学生。学校学业预警分哪几档？"
    task = interpret_task(q)
    assert task.unresolved  # Retain the conflict; do not silently choose an identity.
    packet = G.evidence_packet({"question": q, "task": task,
        "evidence": ledger("学业预警分为黄色、橙色和红色。", "学业预警管理规定")})
    action = A.decide_next(task, packet)
    assert action.action == "compose" and action.status == "answer" and not action.gaps


def test_identity_conflict_still_blocks_dependent_personal_judgment():
    q = "我是2024级，也是2025级学生。我是否适用2025级培养方案？"
    task = interpret_task(q)
    action = A.decide_next(task, {"evidence": []})
    assert action.action == "deliver" and action.status == "needs_input"


def test_local_input_gap_also_keeps_independent_reference_description():
    q = "我是2024级，也是2025级学生。介绍2025级培养方案的修业年限，并说明它是否适用于我。"
    task = interpret_task(q)
    packet = G.evidence_packet({"question": q, "task": task,
        "evidence": ledger("修业年限为3至6年。", "2025级人工智能专业培养方案(主修)")})
    action = A.decide_next(task, packet)
    assert action.action == "compose" and action.status == "partial"
    assert action.gaps[0]["kind"] == "input"


@pytest.mark.parametrize("input_gap", [False, True])
def test_partial_body_keeps_reference_facts_sources_and_confirmed_limitation(monkeypatch, input_gap):
    calls = []
    async def compose(messages, packet, **kwargs):
        calls.append(packet)
        return paragraph(packet, "所引2025级方案的修业年限为3至6年。")
    monkeypatch.setattr(graph.model, "compose", compose)
    state = mixed_state()
    if input_gap:
        state["question"] = "我是2024级，也是2025级学生。介绍2025级培养方案的修业年限，并说明它是否适用于我。"
    result = asyncio.run(graph._grounded_final(state, [], 0, None))
    answer = result["answer"]
    assert len(calls) == 1
    assert answer["status"] == "partial" and answer["contexts"]
    assert "3至6年" in answer["text"] and ("尚不唯一" if input_gap else "尚缺少") in answer["text"]
    assert answer["limitations"] and answer["gaps"][0]["affects"] == "personal_applicability"
    assert answer["model_suggestions"] == [] and bool(answer["questions"]) == input_gap
    assert calls[0]["delivery_constraints"]["status"] == "partial"
    assert result["final_answer"] in answer["text"]


def test_partial_constraint_does_not_publish_invalid_body(monkeypatch):
    async def compose(messages, packet, **kwargs):
        draft = paragraph(packet)
        draft["paragraphs"][0]["unit_ids"] = ["foreign"]
        return draft
    monkeypatch.setattr(graph.model, "compose", compose)
    result = asyncio.run(graph._grounded_final(mixed_state(), [], 0, None))
    assert result["answer"]["status"] == "failed" and not result["answer"]["contexts"]
    assert "3至6年" not in result["answer"]["text"]


@pytest.mark.parametrize("failure,expected_mode", [("structure", "structure"), ("unusable", "regenerate"),
                                                  ("foreign_reference", "content")])
def test_body_repair_uses_its_own_draft_and_error_category(monkeypatch, failure, expected_mode):
    calls = []
    async def compose(messages, packet, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        good = paragraph(packet)
        if len(calls) == 1:
            if failure == "structure":
                del good["paragraphs"][0]["calculation_ids"]
            elif failure == "unusable":
                return {"paragraphs": []}
            else:
                good["paragraphs"][0]["unit_ids"] = ["foreign"]
            return good
        assert kwargs["repair_mode"] == expected_mode
        return good
    monkeypatch.setattr(graph.model, "compose", compose)
    result = asyncio.run(graph._grounded_final({"question": "修业年限？", "answer_protocol": C.PROTOCOL,
        "evidence": ledger("修业年限为3至6年。")}, [], 0, None))
    assert len(calls) == 2 and result["answer"]["status"] == "answer"
    assert calls[1]["previous_draft"] is not None


def test_structure_repair_cannot_change_body_or_publish_second_failure(monkeypatch):
    calls = []
    async def compose(messages, packet, **kwargs):
        calls.append(kwargs)
        draft = paragraph(packet)
        if len(calls) == 1:
            draft["extra"] = "invalid field"
        else:
            assert kwargs["repair_mode"] == "structure"
            draft["paragraphs"][0]["text"] = "修业年限可以为3至6年。"
        return draft
    monkeypatch.setattr(graph.model, "compose", compose)
    result = asyncio.run(graph._grounded_final({"question": "修业年限？", "answer_protocol": C.PROTOCOL,
        "evidence": ledger("修业年限为3至6年。")}, [], 0, None))
    assert len(calls) == 2 and result["answer"]["status"] == "failed"
    assert "结构修复改变了事实正文" in result["answer_audit"]["validation_error"]
    assert result["answer"]["contexts"] == []




def test_tool_partial_evidence_alone_does_not_declare_partial_answer(monkeypatch):
    from agent.core import ToolResult
    async def compose(messages, packet, **kwargs):
        return paragraph(packet)
    monkeypatch.setattr(graph.model, "compose", compose)
    result = asyncio.run(graph._grounded_final({"question": "修业年限？", "answer_protocol": C.PROTOCOL,
        "evidence": ledger("修业年限为3至6年。"),
        "tool_results": [ToolResult(tool="search_regulations", ok=True, status="partial_evidence")]}, [], 0, None))
    assert result["answer"]["status"] == "answer" and not result["answer"]["gaps"]


def test_body_keeps_complete_enumeration_and_current_source_checks():
    from documents.document_availability import SourceUnavailable
    packet = G.evidence_packet({"evidence": ledger("修业年限为3至6年。")})
    draft = paragraph(packet)
    packet["source_revision"] = "definitely-not-the-current-revision"
    with pytest.raises(SourceUnavailable):
        C.render_body(draft, packet)
    result = graph.registry.call("query_course", {"major": "人工智能", "course": "计算机网络"})
    assert result.status == "success"
    state = {"question": "人工智能专业的计算机网络课程支撑哪些毕业要求分解指标？", "tool_results": [result]}
    state.update(graph.append_evidence(state))
    packet = G.evidence_packet(state)
    assert packet["enumerations"]
    records = [unit for unit in packet["evidence_units"] if unit.get("record_id")]
    draft = {"paragraphs": [{"text": records[0]["record_id"] + "：" + records[0]["record"]["text"],
              "unit_ids": [records[0]["unit_id"]], "calculation_ids": []}]}
    with pytest.raises(ValueError, match="完整枚举"):
        C.render_body(draft, packet)
