import asyncio
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.core.calculations import calculate
from agent.core.task import interpret_task
from agent.core import Source, EvidenceLedger
from agent.runtime import composition as C, graph, grounded_answer as G, answer_service as A
from agent.tools.search_regulations.scripts.target_records import select_target_record
from test_grounded_answers import ledger

TITLE = '桂林电子科技大学“大学英语”课程替换及成绩认定办法(试行)'
QUERY = "四级考了450分，大学英语能认定多少分？"


def row(exam="CET4", interval="425 ≤ A < 567", result="A × 0.1764", chunk="cet4", **changes):
    value = {"document_id": 2, "source_sha256": "sha", "chunking_version": "v1", "parent_id": "table1",
             "section_parent_id": "section1", "block_type": "table", "chunk_id": chunk, "chunk_idx": 1,
             "source": TITLE, "heading_path": ["认定标准"], "score": .8, "rerank_score": .8,
             "text": f"类别：{exam}\n成绩 A：{interval}\n大学英语成绩：{result}\n\n同节正文／表外说明：\n折算后四舍五入取整。"}
    value.update(changes)
    return value


def test_select_specific_row_before_irrelevant_rows_without_widening_topk():
    target, other = row(), row("CET6", "A ≥ 500", "100", "cet6")
    rows, audit = select_target_record(QUERY, [other, target], [other], top_k=1, max_chars=2000)
    assert [item["chunk_id"] for item in rows] == ["cet4"]
    assert audit["status"] == "obtained" and not audit["recovery_used"]


def test_one_same_table_indexed_read_and_source_identity_preserved():
    anchor, target, calls = row("CET6", "A ≥ 500", "100", "cet6"), row(), []
    def fetch(seed):
        calls.append(seed)
        return [target]
    rows, audit = select_target_record(QUERY, [anchor], [anchor], top_k=2, max_chars=2000, fetch_table=fetch)
    assert len(calls) == 1 and audit["recovery_used"]
    assert rows[0]["text"] == target["text"] and rows[0]["source_sha256"] == "sha"
    assert rows[0]["score"] is None and rows[0]["rerank_score"] is None


@pytest.mark.parametrize("change", [{"source_sha256": "other"}, {"parent_id": "other"},
                                    {"document_id": 8}, {"source": "创新创业拓展成绩认定"}])
def test_recovery_does_not_cross_identity_or_business(change):
    anchor = row("CET6", "A ≥ 500", "100", "cet6")
    rows, audit = select_target_record(QUERY, [anchor], [anchor], top_k=2, max_chars=2000,
                                       fetch_table=lambda seed: [row(**change)])
    assert audit["status"] == "missing" and rows == [anchor]


def test_consumed_recovery_budget_prevents_another_read():
    anchor = row("CET6", "A ≥ 500", "100", "cet6")
    def forbidden(seed):
        pytest.fail("recovery cannot stack")
    _, audit = select_target_record(QUERY, [anchor], [anchor], top_k=2, max_chars=2000,
                                    fetch_table=forbidden, recovery_available=False)
    assert not audit["recovery_used"]


@pytest.mark.parametrize("score,expected", [("425", "linear"), ("566", "linear"), ("567", "constant")])
def test_target_interval_boundaries(score, expected):
    linear, constant = row(chunk="linear"), row("CET4", "A ≥ 567", "100", "constant")
    query = f"四级考了{score}分，大学英语能认定多少分？"
    rows, audit = select_target_record(query, [linear, constant], [linear], top_k=1, max_chars=2000)
    assert rows[0]["chunk_id"] == expected and audit["status"] == "obtained"


def test_budget_shortage_and_ambiguous_rows_do_not_fabricate_a_match():
    anchor, target = row("CET6", "A ≥ 500", "100", "cet6"), row()
    _, audit = select_target_record(QUERY, [anchor, target], [anchor], top_k=1, max_chars=20)
    assert audit["reason"] == "target_exceeds_budget"
    _, audit = select_target_record(QUERY, [target, row(chunk="duplicate")], [target], top_k=2, max_chars=2000)
    assert audit["reason"] == "matching_row_ambiguous"


def test_body_schema_never_asks_model_for_status_missing_or_coverage():
    packet = G.evidence_packet({"evidence": ledger("修业年限3至6年。")})
    _, schema = C.body_contract(packet)
    assert set(schema["properties"]) == {"paragraphs"}


def test_body_prompt_distinguishes_business_record_ids_from_internal_trace_ids():
    packet = G.evidence_packet({"evidence": ledger("修业年限3至6年。")})
    instruction, schema = C.body_contract(packet)
    assert "业务编号record_id" in instruction
    assert "在引用其unit_id的正文中保留" in instruction
    assert "内部追踪ID" in instruction
    assert "不得输出内部编号" not in instruction
    assert set(schema["properties"]) == {"paragraphs"}


def test_body_has_its_own_renderer_and_binds_real_units(monkeypatch):
    packet = G.evidence_packet({"evidence": ledger("修业年限为3至6年。")})
    monkeypatch.setattr(G, "render_draft", lambda *a, **k: pytest.fail("body must not convert to legacy claims"))
    draft = {"paragraphs": [{"text": "修业年限为3至6年。", "unit_ids": [packet["evidence_units"][0]["unit_id"]], "calculation_ids": []}]}
    audit = {}
    text, contexts = C.render_body(draft, packet, audit=audit)
    assert text == "修业年限为3至6年。" and contexts
    assert "slot_coverage" not in audit and audit["semantic_verification"] == "not_performed"
    bad = copy.deepcopy(draft)
    bad["paragraphs"][0]["unit_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="本轮"):
        C.render_body(bad, packet)


def test_body_reuses_actual_linear_calculation_and_rejects_fake_input():
    evidence = ledger(row()["text"], TITLE)
    packet = G.evidence_packet({"question": QUERY, "evidence": evidence})
    rules = packet["calculation_rules"]
    record = calculate(rules[0]["rule_id"], {"score": "450"}, rules=rules, question=QUERY, history=[])
    packet["calculations"] = [record]
    draft = {"paragraphs": [{"text": "你的成绩为450分，可认定79分。", "unit_ids": [unit["unit_id"] for unit in packet["evidence_units"]],
                             "calculation_ids": [record["calculation_id"]]}]}
    assert C.render_body(draft, packet)[0] == draft["paragraphs"][0]["text"]
    draft["paragraphs"][0]["text"] = "你的成绩为425分，可认定79分。"
    with pytest.raises(ValueError, match="输入成绩"):
        C.render_body(draft, packet)


def test_graph_uses_explicit_body_candidate_and_application_answer(monkeypatch):
    async def compose(messages, packet, **kwargs):
        assert kwargs["protocol"] == C.PROTOCOL
        return {"paragraphs": [{"text": "修业年限为3至6年。", "unit_ids": [packet["evidence_units"][0]["unit_id"]], "calculation_ids": []}]}
    monkeypatch.setattr(graph.model, "compose", compose)
    result = asyncio.run(graph._grounded_final({"question": "修业年限？", "evidence": ledger("修业年限为3至6年。"),
                                               "answer_protocol": C.PROTOCOL}, [], 0, None))
    assert result["answer"]["status"] == "answer"
    assert result["answer_audit"]["composition_protocol"] == C.PROTOCOL


def test_fixed_lookup_requires_full_positive_form_and_canonical_entities():
    from entities import entity_resolver
    resolver = entity_resolver.build_production_resolver()
    q = "人工智能专业的计算机网络课程支撑哪些毕业要求分解指标？"
    resolution = entity_resolver.resolve_question(resolver, q)
    task = interpret_task(q, resolution=resolution)
    call = A.fixed_lookup(task, resolution)
    assert call and call["name"] == "query_course"
    for question in [q + "并比较其他专业", "不是" + q]:
        resolution = entity_resolver.resolve_question(resolver, question)
        task = interpret_task(question, resolution=resolution)
        assert A.fixed_lookup(task, resolution) is None


def test_fixed_lookup_uses_real_registry_and_never_open_planning(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("supported fixed lookup should not need model planning")
    async def compose(messages, packet, **kwargs):
        units = [unit for unit in packet["evidence_units"] if unit.get("record_id")]
        return {"paragraphs": [{"text": "\n".join(unit["record_id"] + "：" + unit["record"]["text"] for unit in units),
                                "unit_ids": [unit["unit_id"] for unit in units], "calculation_ids": []}]}
    monkeypatch.setattr(graph.model, "decide", forbidden)
    monkeypatch.setattr(graph, "_stream_round", forbidden)
    monkeypatch.setattr(graph.model, "compose", compose)
    async def drive():
        return [event async for event in graph.run_stream([], "人工智能专业的计算机网络课程支撑哪些毕业要求分解指标？",
                                                          answer_protocol=C.PROTOCOL)]
    events = asyncio.run(drive())
    done = next(payload for kind, payload in events if kind == "done")
    assert done["calls"][0]["name"] == "query_course" and done["calls"][0]["status"] == "success"
    assert done["answer"]["status"] == "answer"
    assert all(value in done["answer"]["text"] for value in ["2.1", "3.1", "5.1"])
    assert done["answer_audit"]["routing"]["reason"] == "explicit_supported_lookup"


def test_fixed_lookup_does_not_bypass_skill_whitelist(monkeypatch):
    from types import SimpleNamespace
    async def forbidden(*args, **kwargs):
        pytest.fail("rejected fixed lookup must not call model")
    monkeypatch.setattr(graph.model, "decide", forbidden)
    monkeypatch.setattr(graph, "load_skill_for_context", lambda context: SimpleNamespace(
        skill_id="empty", version="1", allowed_tools=(), instructions="", required_inputs=(), side_effect_level="none"))
    monkeypatch.setattr(graph, "skill_tool_schemas", lambda manifest: [])
    monkeypatch.setattr(graph, "skill_instructions_message", lambda manifest: None)
    async def drive():
        return [event async for event in graph.run_stream([], "人工智能专业的计算机网络课程支撑哪些毕业要求分解指标？",
                                                          skill_context=object())]
    done = next(payload for kind, payload in asyncio.run(drive()) if kind == "done")
    assert not done["calls"] and done["decision"] == "clarify"
    assert "Skill" in done["answer"]["text"]


def test_real_source_chunk_projection_reaches_actual_linear_calculator():
    from documents.document_chunks import build_chunks
    root = Path(__file__).resolve().parents[3]
    source = next(path for path in (root / "final-md").rglob("*.md")
                  if "大学英语" in path.name and "成绩认定" in path.name)
    units = [dict(unit, document_id=2, source=TITLE) for unit in build_chunks(source.read_text(encoding="utf-8"))]
    anchor = next(unit for unit in units if unit.get("block_type") == "table" and "CET6" in unit["text"])
    def table(seed):
        return [unit for unit in units if unit.get("parent_id") == seed["parent_id"]]
    def related(seed, selector):
        return [unit for unit in table(seed) if all(unit.get(key) == value for key, value in selector.items())]
    rows, audit = select_target_record(QUERY, [anchor], [anchor], top_k=5, max_chars=6000,
                                       fetch_table=table, fetch_related=related)
    assert audit["status"] == "obtained" and audit["recovery_used"]
    book = EvidenceLedger()
    item = book.add("search_regulations", {"query": QUERY}, Source(title=TITLE, text=rows[0]["text"]))
    packet = G.evidence_packet({"question": QUERY, "evidence": {item.evidence_id: item}})
    rules = packet["calculation_rules"]
    assert len(rules) == 1 and rules[0]["exam"] == "CET4"
    result = calculate(rules[0]["rule_id"], {"score": "450"}, rules=rules, question=QUERY, history=[])
    assert result["raw_result"] == "79.3800" and result["final_result"] == "79"


@pytest.mark.parametrize("repair_mode", [None, "structure", "content", "regenerate"])
def test_compose_transport_has_no_model_status_fields(monkeypatch, repair_mode, api_config):
    import httpx
    from llm import llm_client
    from core.config import settings
    packet = G.evidence_packet({"question": "修业年限？", "evidence": ledger("修业年限为3至6年。")})
    async def post(client, url, **kwargs):
        schema = kwargs["json"]["response_format"]["json_schema"]["schema"]
        assert set(schema["properties"]) == {"paragraphs"}
        if repair_mode:
            messages = kwargs["json"]["messages"]
            body = json.loads(messages[-1]["content"])
            assert body["repair_mode"] == repair_mode and body["previous_draft"] == {"paragraphs": []}
            assert "保持所有paragraphs.text及顺序不变" in messages[0]["content"]
            assert "修复类别：" + repair_mode in messages[0]["content"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"paragraphs": [{"text": "修业年限为3至6年。", "unit_ids": [packet["evidence_units"][0]["unit_id"]], "calculation_ids": []}]})}}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    api_config(writer="writer-focus-20261024-1")
    result = asyncio.run(llm_client.answer_with_evidence([], packet, protocol=C.PROTOCOL,
        repair="local fixture" if repair_mode else None,
        previous_draft={"paragraphs": []} if repair_mode else None, repair_mode=repair_mode))
    assert set(result) == {"paragraphs"}


@pytest.mark.parametrize("in_pool", [True, False])
def test_real_retrieval_exit_delivers_target_before_budget(monkeypatch, in_pool):
    from types import SimpleNamespace
    from documents.document_chunks import build_chunks
    from agent.tools.search_regulations.scripts import retrieval as R
    from agent.tools.search_regulations.scripts.evidence_gate import GateDecision
    from agent.tools.search_regulations.scripts.trace import RetrievalTrace
    from documents import document_availability
    root = Path(__file__).resolve().parents[3]
    source = next(path for path in (root / "final-md").rglob("*.md")
                  if "大学英语" in path.name and "成绩认定" in path.name)
    units = [dict(unit, document_id=2, source=TITLE, snippet=unit["text"])
             for unit in build_chunks(source.read_text(encoding="utf-8"))]
    target = next(unit for unit in units if unit.get("block_type") == "table" and "CET4" in unit["text"] and "0.1764" in unit["text"])
    anchor = next(unit for unit in units if unit.get("block_type") == "table" and "CET6" in unit["text"])
    pool = [anchor, target] if in_pool else [anchor]
    def rerank(query, rows):
        for index, item in enumerate(rows): item["rerank_score"] = .99 - index * .1
    def table(seed):
        return [unit for unit in units if unit.get("parent_id") == seed["parent_id"]]
    monkeypatch.setattr(R, "ensure_collection", lambda: None)
    monkeypatch.setattr(R, "_retrieve_chunks", lambda *a, **k: list(pool))
    monkeypatch.setattr(R, "_dedupe_near", lambda rows, threshold: rows)
    monkeypatch.setattr(R, "_rerank", rerank)
    monkeypatch.setattr(R, "_expand_flow", lambda rows: rows)
    monkeypatch.setattr(R, "_follow_refs", lambda rows: rows)
    monkeypatch.setattr(R, "_absent_reference", lambda query: "")
    monkeypatch.setattr(R, "_gate_anchors", lambda: None)
    monkeypatch.setattr(R, "_missing_files", lambda rows: [])
    monkeypatch.setattr(R, "_get_client", lambda: pytest.fail("retrieval fixture must not access real Qdrant"))
    monkeypatch.setattr(R, "_table_evidence", table)
    monkeypatch.setattr(R, "_document_evidence", lambda seed: units)
    monkeypatch.setattr(R, "_related_evidence", lambda seed, selector: [unit for unit in table(seed)
        if all(unit.get(key) == value for key, value in selector.items())])
    monkeypatch.setattr(R.gate_signals, "collect", lambda **kwargs: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(R, "decide", lambda *a: GateDecision("supported", "supported_high", "test", (), False, True))
    monkeypatch.setattr(document_availability, "require_source", lambda *a, **k: None)
    rewritten = "大学英语四级成绩认定规则"
    trace = RetrievalTrace.start(rewritten)
    result = R.retrieve(rewritten, top_k=1, trace=trace, request_task=interpret_task(QUERY))
    assert result["target_record"]["status"] == "obtained"
    assert result["target_record"]["recovery_used"] is (not in_pool)
    assert "0.1764" in result["contexts"][0]["text"] and "CET4" in result["contexts"][0]["text"]
    assert trace.stages["final"][0]["physical_chunk_id"] == target["chunk_id"]


def test_registry_preserves_confirmed_task_outside_model_tool_schema(monkeypatch):
    from agent.runtime import registry
    from agent.core import ToolResult
    task = interpret_task(QUERY)
    def search(query, keywords=None, *, request_task=None):
        assert query == "大学英语四级成绩认定规则" and request_task is task
        return ToolResult(tool="search_regulations", ok=True, status="no_evidence")
    monkeypatch.setattr(registry, "search_regulations_result", search)
    result = registry.call("search_regulations", {"query": "大学英语四级成绩认定规则"}, context={"task": task})
    assert result.status == "no_evidence" and "task" not in result.meta["args"]
    schema = next(item for item in registry.schemas() if item["function"]["name"] == "search_regulations")
    assert "task" not in schema["function"]["parameters"]["properties"]
