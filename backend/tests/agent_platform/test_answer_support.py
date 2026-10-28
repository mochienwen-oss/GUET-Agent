"""Log-only support audit: block binding, explicit exceptions, unchanged output."""
import asyncio
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import Source, EvidenceLedger
from agent.core.answer_support import _verify_support, audit_blocks
from agent.runtime import grounded_answer as G, graph


def evidence(text="第七条 公示三至六年，比例不超过30%。", **extra):
    return dict(text=text, evidence_id="a", title="测试规定", heading_path=["第七条"], scope={}, **extra)


def draft_for(packet, text):
    unit_ids = [unit["unit_id"] for unit in packet.get("evidence_units") or []]
    slots = list((packet.get("question_brief") or {}).get("required_slots") or [])
    return {"status": "answer", "claims": [{
        "claim_id": "c1", "claim_type": "conclusion", "question_slot": "conclusion",
        "text": text, "unit_ids": unit_ids,
        "supplement_class": "required"}],
        "slot_coverage": [{"slot": slot,
                           "status": "covered" if slot == "conclusion" else "not_applicable",
                           "claim_ids": ["c1"] if slot == "conclusion" else [],
                           "reason": "" if slot == "conclusion" else "test fixture"}
                          for slot in slots],
        "blocks": [{"claim_ids": ["c1"]}], "missing": []}


def test_article_aliases_match_but_wrong_article_is_logged():
    result = _verify_support("第7条允许办理；第六条也适用。", [evidence()])
    assert result["unsupported_articles"] == ["第六条"]


def test_unbound_evidence_cannot_rescue_a_block():
    result = audit_blocks([{"text": "学分30学分。", "evidence": [evidence("学分：3")]},
                           {"text": "另一份30学分。", "evidence": [evidence("学分：30")]}])
    assert result["block_support"][0]["unsupported_numbers"] == ["30学分"]
    assert result["block_support"][1]["unsupported_numbers"] == []


def test_equivalent_digits_units_percent_and_range_are_recognized():
    result = _verify_support("期限3~6年，比例30.0%，学分3学分。", [evidence("期限三至六年，比例30％，学分：3")])
    assert result["unsupported_numbers"] == [] and result["soft_warnings"] == []
    assert result["support_ratio"] == 1


def test_same_number_with_wrong_unit_does_not_prove_support():
    assert _verify_support("期限3年。", [evidence("课程3学分")])["unsupported_numbers"] == ["3年"]


def test_list_indices_are_not_facts_and_empty_ratio_is_unknown():
    result = _verify_support("1. 提交材料\n2. 等待审核", [evidence("提交材料并等待审核")])
    assert result["checked_mentions"] == 0 and result["support_ratio"] is None


def test_owner_ruling_whitelist_is_field_limited():
    rows = [evidence("毕业总学分147学分。", human_ruling={"field": "total_credits", "value": 148})]
    assert _verify_support("毕业总学分148学分。", rows)["unsupported_numbers"] == []
    assert _verify_support("期限148年。", rows)["unsupported_numbers"] == ["148年"]


def test_academic_year_conversion_is_soft_not_asserted_true():
    result = _verify_support("期限12个月。", [evidence("期限一学年。")])
    assert result["unsupported_numbers"] == []
    assert result["soft_warnings"][0]["reason"] == "academic_year_conversion_unverified"
    assert result["support_ratio"] == 0


def test_code_authorization_does_not_leak_to_another_block():
    result = audit_blocks([{"text": "79分。", "evidence": [evidence("换算规则")], "authorized_text": "79分。"},
                           {"text": "99分。", "evidence": [evidence("另一规则")]}])
    assert result["block_support"][0]["unsupported_numbers"] == []
    assert result["block_support"][1]["unsupported_numbers"] == ["99分"]


def test_render_with_audit_changes_no_answer_bytes_or_citations():
    packet = G.evidence_packet({"evidence": {"a": type("Item", (), {"source": type("S", (), {"title": "测试规定", "text": "第七条 公示三至六年，比例不超过30%。", "heading_path": ["第七条"], "text_hash": "", "chunk_id": None})(), "tool": "search_regulations", "snippet": "第七条 公示三至六年，比例不超过30%。", "evidence_id": "a"})()}})
    draft = draft_for(packet, "第六条公示99天。")
    old = G.render_draft(draft, packet)
    audit = {}
    new = G.render_draft(draft, packet, audit=audit)
    assert old == new
    assert audit["unsupported_articles"] == ["第六条"] and audit["unsupported_numbers"] == ["99天"]


def test_graph_exposes_audit_but_does_not_hard_block(monkeypatch, caplog):
    obj = EvidenceLedger()
    item = obj.add("search_regulations", {}, Source(title="测试规定", text="第七条 公示3天。"))
    async def answer(messages, packet, **kwargs):
        return draft_for(packet, "第六条公示99天。")
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    with caplog.at_level(logging.INFO, logger="agent.runtime.graph"):
        out = asyncio.run(graph.wrap_up({"question": "怎么公示？", "evidence": {item.evidence_id: item}}))
    assert out["final_answer"] == "第六条公示99天。"
    assert out["answer_audit"]["unsupported_articles"] == ["第六条"]
    assert out["answer_audit"]["support_audit_mode"] == "lexical_log_only_not_semantic_validation"
    assert out["answer_audit"]["audit_id"] in caplog.text
    assert "answer_support_audit" in caplog.text
    assert item.snippet not in caplog.text  # Do not log complete source text.


def test_actual_code_calculation_is_exempt_without_authorizing_model_numbers():
    obj = EvidenceLedger()
    item = obj.add("search_regulations", {}, Source(title="成绩办法", text=
        "类别：CET4\n成绩A：425 ≤ A < 567\n大学英语成绩：A × 0.1764\n四舍五入取整"))
    packet = G.evidence_packet({"question": "CET4考了450分，能认定多少？", "evidence": {item.evidence_id: item}})
    draft = draft_for(packet, "99分。")
    audit = {}
    text, _ = G.render_draft(draft, packet, audit=audit)
    assert "79.3800" in text and "99" not in text
    assert audit["unsupported_numbers"] == [] and audit["soft_warnings"] == []
