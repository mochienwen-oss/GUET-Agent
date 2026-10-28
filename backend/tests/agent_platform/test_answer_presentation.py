"""Presentation boundaries; synthetic inputs, not holdout answer replay."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.runtime import grounded_answer as G
from llm import llm_client


def row(eid, college=None):
    return {"evidence_id": eid, "title": (college or "校级") + "办事办法",
            "heading_path": [], "text": "填写附件1申请表，审核后公示。",
            "scope": {"college": college} if college else {}}


def packet_for(rows):
    packet = {"evidence": rows, "question_brief": {"required_slots": ["conclusion"]}}
    packet["evidence_units"] = [unit for row in rows
                                for unit in G.evidence_units(row["evidence_id"], row["text"], row)]
    return packet


def new_draft(packet, blocks):
    claims, rendered = [], []
    covered = []
    for index, block in enumerate(blocks, 1):
        cid = "c%d" % index
        unit_ids = [unit["unit_id"] for unit in packet["evidence_units"]
                    if unit["evidence_id"] in block["evidence_ids"]]
        claims.append({"claim_id": cid, "claim_type": "conclusion",
                       "question_slot": "conclusion", "text": block["text"],
                       "unit_ids": unit_ids,
                       "supplement_class": "required"})
        rendered.append({"claim_ids": [cid]})
        covered.append(cid)
    return {"status": "answer", "claims": claims,
            "slot_coverage": [{"slot": "conclusion", "status": "covered",
                               "claim_ids": covered, "reason": ""}],
            "blocks": rendered, "missing": []}


def render(rows, blocks):
    packet = packet_for(rows)
    return G.render_draft(new_draft(packet, blocks), packet)


def test_same_college_consecutive_blocks_get_one_scope_notice():
    text, contexts = render([row("a", "甲学院")], [
        {"text": "先申请。", "evidence_ids": ["a"]},
        {"text": "再审核。", "evidence_ids": ["a"]},
        {"text": "最后公示。", "evidence_ids": ["a"]}])
    assert text.count("以下规定仅适用于甲学院") == 1
    assert text.startswith("以下规定仅适用于甲学院")
    assert len(contexts) == 1


def test_exact_code_owned_notice_in_model_text_is_not_duplicated():
    text, _ = render([row("a", "甲学院")], [{
        "text": "以下规定仅适用于甲学院，其他学院以各自规定为准。\n\n先申请。",
        "evidence_ids": ["a"]}])
    assert text.count("以下规定仅适用于甲学院") == 1
    assert "先申请。" in text


def test_college_transition_restates_notice_without_scope_union():
    text, _ = render([row("a", "甲学院"), row("b", "乙学院")], [
        {"text": "甲先申请。", "evidence_ids": ["a"]},
        {"text": "乙先审核。", "evidence_ids": ["b"]},
        {"text": "甲再公示。", "evidence_ids": ["a"]}])
    assert text.count("仅适用于甲学院") == 2
    assert text.count("仅适用于乙学院") == 1
    assert "适用于甲学院、乙学院" not in text


def test_school_scope_transition_is_not_silently_under_college_notice():
    text, _ = render([row("a", "甲学院"), row("s")], [
        {"text": "甲申请。", "evidence_ids": ["a"]},
        {"text": "学校审核。", "evidence_ids": ["s"]},
        {"text": "甲公示。", "evidence_ids": ["a"]}])
    assert text.count("仅适用于甲学院") == 2


def test_necessary_attachment_instruction_and_evidence_are_preserved():
    source = row("s")
    text, contexts = render([source], [{"text": "填写附件1申请表后提交。", "evidence_ids": ["s"]}])
    assert text == "填写附件1申请表后提交。"
    assert contexts[0]["text"] == source["text"]


def test_runtime_final_path_keeps_one_notice_and_verified_context(monkeypatch):
    from agent.runtime import graph
    packet = {"evidence": [row("a", "甲学院")], "scope_rejections": [], "calculations": []}
    packet.update(packet_for(packet["evidence"]))
    monkeypatch.setattr(G, "evidence_packet", lambda state: packet)
    async def answer(messages, evidence, **kwargs):
        return new_draft(evidence, [
            {"text": "先申请。", "evidence_ids": ["a"]},
            {"text": "再审核。", "evidence_ids": ["a"]}])
    monkeypatch.setattr(llm_client, "answer_with_evidence", answer)
    result = asyncio.run(graph.wrap_up({"question": "如何申请？"}))
    assert result["final_answer"].count("以下规定仅适用于甲学院") == 1
    assert result["contexts"][0]["citation_verified"] is True
    assert result["answer_audit"]["selected_evidence_ids"] == ["a"]


def test_real_final_model_request_limits_attachment_and_scope_prose(monkeypatch):
    captured = {}
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
                {"status": "answer", "claims": [], "slot_coverage": [],
                 "blocks": [], "missing": []})}}]}
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, *, json):
            captured.update(json)
            return Response()
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", Client)
    packet = {"question": "如何申请？", "evidence": [row("s")]}
    result = asyncio.run(llm_client.answer_with_evidence([], packet))
    instruction = captured["messages"][-2]["content"]
    assert "学院范围提示和资料来源由渲染器统一添加" in instruction
    assert "不得以“详见附件”代替回答" in instruction
    assert "用户明确询问附件" in instruction
    assert "只回答当前问题" in instruction
    assert json.loads(captured["messages"][-1]["content"]) == llm_client._final_input(packet)
    assert "填写附件1申请表" not in instruction  # evidence stays data, not system instructions
    assert captured["temperature"] == 0 and result["status"] == "answer"
