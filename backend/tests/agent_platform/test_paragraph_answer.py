"""Minimal composition contract keeps prose and reuses the final boundary."""
import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.runtime import grounded_answer as G, graph
from agent.runtime.paragraph_answer import PROTOCOL, paragraph_contract
from test_grounded_answers import ledger


def paragraph(packet, text="修业年限为3至6年。"):
    return {"status": "answer", "paragraphs": [{"text": text,
            "unit_ids": [u["unit_id"] for u in packet["evidence_units"]],
            "calculation_ids": []}], "missing": []}


def test_paragraphs_preserve_natural_prose_without_claim_bookkeeping():
    packet = G.evidence_packet({"evidence": ledger("修业年限为3至6年。"), "question": "修业年限？"})
    audit = {}
    text, contexts = G.render_draft(paragraph(packet), packet, audit=audit)
    assert text == "修业年限为3至6年。" and contexts
    assert audit["composition_protocol"] == PROTOCOL
    assert audit["semantic_verification"] == "not_performed"
    assert audit["coverage_authority"] == "not_assessed"
    assert not any(row["status"] == "covered" for row in audit["slot_coverage"].values())


def test_paragraph_foreign_source_and_fake_mapping_fail():
    packet = G.evidence_packet({"evidence": ledger("修业年限为3至6年。"), "question": "修业年限？"})
    draft = paragraph(packet)
    draft["paragraphs"][0]["unit_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="本轮"):
        G.render_draft(draft, packet)
    draft = paragraph(packet)
    draft["blocks"] = []
    with pytest.raises(ValueError, match="段落"):
        G.render_draft(draft, packet)


def test_schema_has_only_one_ordered_body_and_source_binding():
    packet = {"evidence_units": [{"unit_id": "u1"}], "calculations": []}
    instruction, schema = paragraph_contract(packet)
    assert set(schema["properties"]) == {"status", "paragraphs", "missing"}
    fields = schema["properties"]["paragraphs"]["items"]["properties"]
    assert set(fields) == {"text", "unit_ids", "calculation_ids"}
    assert "共同前提" in instruction


def test_graph_can_select_candidate_without_changing_default(monkeypatch, api_config):
    import httpx
    from llm import llm_client
    from core.config import settings
    packet = G.evidence_packet({"evidence": ledger("修业年限为3至6年。"), "question": "修业年限？"})
    requests = []
    async def post(self, url, **kwargs):
        payload = kwargs["json"]
        requests.append(payload)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(paragraph(packet), ensure_ascii=False)}}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    api_config()
    async def run():
        trace = {}
        out = await llm_client.answer_with_evidence([], packet, protocol=PROTOCOL, trace=trace)
        assert trace["final_requests"][0]["protocol"] == PROTOCOL
        return out
    result = asyncio.run(run())
    assert "paragraphs" in result
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert "claims" not in schema["properties"]


def test_final_node_passes_explicit_candidate_to_model(monkeypatch, api_config):
    api_config()
    evidence = ledger("修业年限为3至6年。")
    async def answer(messages, packet, **kwargs):
        assert kwargs["protocol"] == PROTOCOL
        return paragraph(packet)
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph._grounded_final({"question": "修业年限？", "evidence": evidence,
                                           "answer_protocol": PROTOCOL}, [], 0, None))
    assert out["final_answer"] == "修业年限为3至6年。"
