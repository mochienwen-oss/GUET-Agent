import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.core import Source, EvidenceLedger, ToolResult
from agent.core.scope import RequestScope, request_scope, scope_conflict, tool_scope_conflict
from agent.core import citations
from agent.runtime import grounded_answer as G, graph


def ledger(text="A、B卷之间题目不能有重复。", title="考试管理规定"):
    obj = EvidenceLedger()
    item = obj.add("search_regulations", {"query": "q"}, Source(title=title, text=text))
    return {item.evidence_id: item}


def draft_for(packet, text, evidence_ids, *, slot="conclusion", claim_type="conclusion",
              supplement="required", claim_id="c1"):
    """Build the grounded-draft/5 shape for renderer contract tests."""
    units = [unit["unit_id"] for unit in packet.get("evidence_units") or []
             if unit["evidence_id"] in evidence_ids]
    claims = [{"claim_id": claim_id, "claim_type": claim_type,
               "question_slot": slot, "text": text, "unit_ids": units,
               "supplement_class": supplement}]
    slots = list((packet.get("question_brief") or {}).get("required_slots") or [])
    coverage = []
    for item in slots:
        if item == slot:
            coverage.append({"slot": item, "status": "covered",
                             "claim_ids": [claim_id], "reason": ""})
        else:
            coverage.append({"slot": item, "status": "not_applicable",
                             "claim_ids": [], "reason": "test fixture"})
    return {"status": "answer", "claims": claims, "slot_coverage": coverage,
            "blocks": [{"claim_ids": [claim_id]}], "missing": []}


def test_explicit_scope_cannot_be_overridden_by_assistant_history():
    s = request_scope("那本科呢？", history=[{"role": "assistant", "content": "研究生"},
                                               {"role": "user", "content": "研究生要求"}])
    assert s.level == "undergraduate" and s.provenance["level"] == "current_user"
    # Owner-approved default replaces unknown, but not with assistant-invented identity.
    assert request_scope("那学分呢？", history=[{"role": "assistant", "content": "研究生"}]).level == "undergraduate"
    assert request_scope("食堂在哪里？", history=[{"role": "user", "content": "研究生要求"}]).level == "undergraduate"


def test_undergraduate_tool_rejects_graduate_without_catalog_bypass():
    assert tool_scope_conflict(RequestScope(level="graduate"), "query_major_requirements")
    assert tool_scope_conflict(RequestScope(level="graduate"), "search_regulations") is None
    assert scope_conflict(RequestScope(cohort="2024"), {"cohort": "2025"})


def test_scope_filter_removes_undergraduate_evidence_for_graduate():
    state = {"question": "研究生毕业学分", "request_scope": RequestScope(level="graduate"),
             "evidence": ledger("毕业学分148", "2025级人工智能专业培养方案(主修)")}
    packet = G.evidence_packet(state)
    assert packet["evidence"] == [] and packet["scope_rejections"][0]["reason"] == "scope_mismatch:level"


def test_foreign_missing_and_self_written_citations_are_rejected():
    p = G.evidence_packet({"evidence": ledger()})
    for draft in [draft_for(p, "答案", ["invented"]),
                  draft_for(p, "答案", []),
                  draft_for(p, "资料来源：《伪造文件》", [p["evidence"][0]["evidence_id"]])]:
        with pytest.raises(ValueError):
            G.render_draft(draft, p)


def test_verified_ids_render_sources_even_without_title_in_body():
    p = G.evidence_packet({"evidence": ledger()})
    text, contexts = G.render_draft(draft_for(
        p, "A、B卷之间不能有重复。", [p["evidence"][0]["evidence_id"]]), p)
    final, _ = citations.finalize_answer(text, contexts)
    assert "资料来源：《考试管理规定》" in final


def test_evidence_units_are_exact_substrings_and_carry_provenance():
    p = G.evidence_packet({"evidence": ledger("第七条 公示3天。另需审核。")})
    assert p["evidence_units"]
    for unit in p["evidence_units"]:
        source = next(row["text"] for row in p["evidence"] if row["evidence_id"] == unit["evidence_id"])
        assert source[unit["start"]:unit["end"]] == unit["text"]
        assert unit["unit_id"].startswith(unit["evidence_id"] + ":u")


def test_no_evidence_draft_may_explain_unknown_slots_without_facts():
    p = G.evidence_packet({"evidence": ledger("无关正文。")})
    draft = {"status": "no_evidence", "claims": [], "blocks": [],
             "slot_coverage": [{"slot": "conclusion", "status": "unknown",
                                "claim_ids": [], "reason": "证据未提及该事项。"}],
             "missing": []}
    audit = {}
    text, contexts = G.render_draft(draft, p, audit=audit)
    assert text == G.NO_EVIDENCE and contexts == []
    assert audit["slot_coverage"]["conclusion"]["status"] == "unknown"


def test_question_brief_is_type_specific_and_allows_explicit_slots():
    p = G.evidence_packet({"question": "考试A卷和B卷的题目重复率最高允许多少？",
                           "evidence": ledger()})
    brief = p["question_brief"]
    assert brief["answer_type"] == "comparison"
    assert "relation" in brief["required_slots"]
    assert "objects" in brief["required_slots"]


def test_comparison_relation_must_cover_both_question_objects():
    p = G.evidence_packet({
        "question": "考试A卷和B卷的题目重复率最高允许多少？",
        "evidence": ledger("必修课程提交A、B两套试卷，A、B卷之间题目不能有重复。每套试卷与近两年试卷重复率不得高于30%。")})
    units = p["evidence_units"]
    bad = {"status": "answer", "claims": [
        {"claim_id": "c1", "claim_type": "comparison", "question_slot": "relation",
         "text": "每套试卷与近两年试卷重复率不得高于30%。",
         "unit_ids": [units[1]["unit_id"]],
         "supplement_class": "required"}],
        "slot_coverage": [
            {"slot": "conclusion", "status": "covered", "claim_ids": ["c1"], "reason": ""},
            {"slot": "objects", "status": "covered", "claim_ids": ["c1"], "reason": ""},
            {"slot": "relation", "status": "covered", "claim_ids": ["c1"], "reason": ""},
            {"slot": "exceptions", "status": "not_applicable", "claim_ids": [], "reason": "无"}],
        "blocks": [{"claim_ids": ["c1"]}],
        "missing": []}
    audit = {}
    with pytest.raises(ValueError, match="共同原文依据"):
        G.render_draft(bad, p, audit=audit)


def test_calculation_classification_does_not_reject_policy_transcription():
    p = G.evidence_packet({"question": "竞赛获奖加分怎么计算？",
                           "evidence": ledger("同一项目不能累加。各单项按参加人数平均计算。")})
    p["question_brief"]["required_slots"] = ["conclusion", "calculation"]
    units = p["evidence_units"]
    draft = {"status": "answer", "claims": [
        {"claim_id": "c1", "claim_type": "conclusion", "question_slot": "conclusion",
         "text": "可以加分。", "unit_ids": [units[0]["unit_id"]],
         "supplement_class": "required"},
        {"claim_id": "c2", "claim_type": "calculation", "question_slot": "calculation",
         "text": "同一项目不能累加。", "unit_ids": [units[0]["unit_id"]],
         "supplement_class": "required"}],
        "slot_coverage": [
            {"slot": "conclusion", "status": "covered", "claim_ids": ["c1"], "reason": ""},
            {"slot": "calculation", "status": "covered", "claim_ids": ["c2"], "reason": ""}],
        "blocks": [{"claim_ids": ["c1", "c2"]}],
        "missing": []}
    audit = {}
    assert "同一项目不能累加" in G.render_draft(draft, p, audit=audit)[0]


def test_premise_words_cannot_upgrade_unknown_coverage():
    p = G.evidence_packet({"question": "考试A卷和B卷的题目重复率最高允许多少？",
                           "evidence": ledger("A、B卷之间题目不能有重复。每套试卷与近两年试卷重复率不得高于30%。")})
    units = p["evidence_units"]
    draft = {"status": "partial", "claims": [
        {"claim_id": "c1", "claim_type": "comparison", "question_slot": "relation",
         "text": "A/B之间不能有重复。", "unit_ids": [units[0]["unit_id"]],
         "supplement_class": "required"},
        {"claim_id": "c2", "claim_type": "comparison", "question_slot": "relation",
         "text": "每套与近两年比较不超过30%。", "unit_ids": [units[1]["unit_id"]],
         "supplement_class": "required"}],
        "slot_coverage": [
            {"slot": "conclusion", "status": "unknown", "claim_ids": [], "reason": "问题前提不成立"},
            {"slot": "objects", "status": "covered", "claim_ids": ["c1"], "reason": ""},
            {"slot": "relation", "status": "unknown", "claim_ids": [], "reason": "问题前提不成立"},
            {"slot": "exceptions", "status": "not_applicable", "claim_ids": [], "reason": "未问"}],
        "blocks": [{"claim_ids": ["c1", "c2"]}],
        "missing": []}
    audit = {}
    text, _ = G.render_draft(draft, p, audit=audit)
    assert "A/B之间不能有重复" in text
    assert audit["slot_coverage"]["relation"]["status"] == "unknown"
    assert "relation" in audit["gap_slots"]
    assert audit["hedge_applied"] is False
    assert audit["semantic_verification"] == "not_performed"


def test_partial_without_asked_gap_has_no_hedge():
    p = G.evidence_packet({"evidence": ledger("公示3天。")})
    draft = draft_for(p, "公示3天。", [p["evidence"][0]["evidence_id"]])
    draft["status"] = "partial"
    audit = {}
    text, _ = G.render_draft(draft, p, audit=audit)
    assert text == "公示3天。"
    assert audit["model_status"] == "partial"
    assert audit["effective_status"] == "answer"
    assert audit["hedge_applied"] is False


def test_non_asked_advisory_slots_do_not_make_a_partial():
    brief = G.question_brief({"question": "申请学士学位的学分绩需要达到多少分？"})
    assert "exceptions" not in brief["required_slots"]
    assert "exceptions" in brief["advisory_slots"]


def test_absent_coverage_is_audit_only_not_a_factual_gap():
    p = G.evidence_packet({"question": "申请学士学位的学分绩需要达到多少分？",
                           "evidence": ledger("学分绩达到70分。")})
    draft = draft_for(p, "70。", [p["evidence"][0]["evidence_id"]])
    draft["slot_coverage"] = [item for item in draft["slot_coverage"]
                              if item["slot"] != "unit"]
    audit = {}
    text, _ = G.render_draft(draft, p, audit=audit)
    assert text == "70。"
    assert "unit" in audit["gap_slots"]
    assert audit["effective_status"] == "answer"
    assert audit["hedge_applied"] is False


def test_concrete_missing_information_is_preserved_for_permission():
    p = G.evidence_packet({"question": "我能申请吗？",
                           "evidence": ledger("须满足毕业条件才可申请。")})
    draft = draft_for(p, "只有满足毕业条件才能申请，目前无法判断你是否符合。",
                      [p["evidence"][0]["evidence_id"]])
    draft.update(status="partial", missing=["你是否已经满足毕业条件"])
    audit = {}
    text, _ = G.render_draft(draft, p, audit=audit)
    assert "尚需确认：你是否已经满足毕业条件。" in text
    assert audit["effective_status"] == "partial"
    assert audit["reported_missing"] == draft["missing"]


@pytest.mark.parametrize("missing", [["无"], ["待确认"]])
def test_placeholder_missing_never_adds_a_generic_disclaimer(missing):
    p = G.evidence_packet({"evidence": ledger()})
    draft = draft_for(p, "A、B卷之间不能有重复。", [p["evidence"][0]["evidence_id"]])
    draft["missing"] = missing
    audit = {}
    text, _ = G.render_draft(draft, p, audit=audit)
    assert text == "A、B卷之间不能有重复。"
    assert audit["model_missing_ignored"] or audit["unresolved_missing"]
    assert audit["effective_status"] == ("answer" if missing == ["无"] else "partial")


def test_not_applicable_metadata_cannot_reject_a_delivered_condition(monkeypatch):
    obj = ledger("可以申请。单位须为本校。")
    calls = []

    async def answer(messages, packet, **kwargs):
        calls.append(kwargs.get("repair"))
        d = draft_for(packet, "单位须为本校才能申请。", list(obj), slot="conditions")
        d["slot_coverage"].append({"slot": "conditions", "status": "not_applicable",
                                   "claim_ids": [], "reason": "用户没有单独问条件"})
        return d

    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph._grounded_final(
        {"question": "可以申请吗？", "evidence": obj}, [], 0, None))
    assert out["final_answer"] == "单位须为本校才能申请。"
    assert out["answer_audit"]["coverage_conflicts"] == ["conditions"]
    assert calls == [None] and out["contexts"]


def test_required_claim_must_appear_in_a_block():
    p = G.evidence_packet({"evidence": ledger("第七条 公示3天。")})
    draft = draft_for(p, "公示3天。", [p["evidence"][0]["evidence_id"]])
    draft["claims"].append({"claim_id": "c2", "claim_type": "condition",
                            "question_slot": "conclusion", "text": "需审核。",
                            "unit_ids": [p["evidence_units"][0]["unit_id"]],
                            "supplement_class": "required"})
    draft["slot_coverage"][0]["claim_ids"].append("c2")
    audit = {}
    with pytest.raises(ValueError, match="必要claim未交付"):
        G.render_draft(draft, p, audit=audit)


def test_selected_condition_is_delivered_not_only_claim_id():
    p = G.evidence_packet({"evidence": ledger("可以申请。单位须为本校。")})
    d = draft_for(p, "可以申请。", [p["evidence"][0]["evidence_id"]])
    d["claims"].append(dict(d["claims"][0], claim_id="c2", text="单位须为本校。"))
    d["blocks"][0]["claim_ids"].append("c2")
    d["slot_coverage"][0]["claim_ids"].append("c2")
    text, _ = G.render_draft(d, p)
    assert text == "可以申请。\n\n单位须为本校。"
    d["blocks"][0]["text"] = "可以申请。"
    with pytest.raises(ValueError, match="正文块格式"):
        G.render_draft(d, p)


def test_optional_background_may_be_omitted():
    p = G.evidence_packet({"evidence": ledger("公示3天。背景。")})
    d = draft_for(p, "公示3天。", [p["evidence"][0]["evidence_id"]])
    d["claims"].append(dict(d["claims"][0], claim_id="c2", text="背景。",
                            supplement_class="optional"))
    assert G.render_draft(d, p)[0] == "公示3天。"


def test_one_policy_formula_cannot_authorize_unexecuted_arithmetic():
    p = G.evidence_packet({"evidence": ledger("按人数平均计算。条件另定。")})
    d = draft_for(p, "按人数平均计算。", [p["evidence"][0]["evidence_id"]])
    d["claims"][0].update(claim_type="calculation", unit_ids=[p["evidence_units"][0]["unit_id"]])
    d["claims"].append(dict(d["claims"][0], claim_id="c2", text="450×0.2=90分。",
                            unit_ids=[p["evidence_units"][1]["unit_id"]]))
    d["blocks"][0]["claim_ids"].append("c2")
    with pytest.raises(ValueError, match="c2"):
        G.render_draft(d, p)


def test_lenient_flag_cannot_remove_foreign_claim_ids():
    p = G.evidence_packet({"evidence": ledger()})
    d = draft_for(p, "答案。", [p["evidence"][0]["evidence_id"]])
    d["slot_coverage"][0]["claim_ids"].append("foreign")
    with pytest.raises(ValueError, match="不存在的 claim"):
        G.render_draft(d, p, strict=False)


def test_second_failure_does_not_salvage_an_incomplete_permission(monkeypatch):
    obj = ledger("可以申请，单位须为本校。", "测试规定")
    calls = []
    async def answer(messages, packet, **kwargs):
        calls.append(kwargs.get("repair"))
        d = draft_for(packet, "可以申请。", list(obj))
        d["claims"].append(dict(d["claims"][0], claim_id="c2", text="单位须为本校。"))
        return d
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph._grounded_final(
        {"question": "可以申请吗？", "evidence": obj}, [], 0, None))
    assert len(calls) == 2 and "c2" in calls[1]
    assert out["final_answer"] == G.INVALID_ANSWER
    assert out["contexts"] == []


def test_unit_neighbors_preserve_exact_source_boundaries():
    source = {"heading_path": ["第五条"]}
    units = G.evidence_units("e", "可以申请。单位须为本校。", source)
    assert units[0]["next_unit_id"] == units[1]["unit_id"]
    assert units[1]["previous_unit_id"] == units[0]["unit_id"]
    assert units[1]["heading_path"] == ["第五条"]


def test_prohibited_claim_cannot_enter_the_body():
    p = G.evidence_packet({"evidence": ledger("第七条 公示3天。")})
    draft = draft_for(p, "公示3天。", [p["evidence"][0]["evidence_id"]],
                      supplement="prohibited")
    with pytest.raises(ValueError, match="禁止补充"):
        G.render_draft(draft, p)


def test_old_duplicate_necessity_field_is_not_accepted():
    p = G.evidence_packet({"evidence": ledger("第七条 公示3天。")})
    draft = draft_for(p, "公示3天。", [p["evidence"][0]["evidence_id"]],
                      supplement="prohibited")
    draft["claims"][0]["required"] = True
    audit = {}
    with pytest.raises(ValueError, match="claim 格式"):
        G.render_draft(draft, p, audit=audit)


def test_missing_slot_coverage_is_normalized_and_traced():
    p = G.evidence_packet({"question": "申请学士学位的学分绩需要达到多少分？",
                           "evidence": ledger("学分绩达到70分。")})
    draft = draft_for(p, "70。", [p["evidence"][0]["evidence_id"]])
    draft["slot_coverage"] = draft["slot_coverage"][:1]
    audit = {}
    text, _ = G.render_draft(draft, p, audit=audit)
    assert text.startswith("70。")
    assert audit["normalized_missing_slots"]
    assert audit["effective_status"] == "answer"
    assert audit["hedge_applied"] is False


def test_owner_148_ruling_is_document_specific():
    p = G.evidence_packet({"evidence": ledger("毕业总学分不低于147学分。", "2025级人工智能专业培养方案(主修)")})
    eid = p["evidence"][0]["evidence_id"]
    assert p["evidence"][0]["human_ruling"]["value"] == 148
    with pytest.raises(ValueError):
        G.render_draft(draft_for(p, "毕业总学分147学分。", [eid]), p)
    text, _ = G.render_draft(draft_for(p, "毕业总学分148学分。", [eid]), p)
    assert "148" in text
    other = G.evidence_packet({"evidence": ledger("147", "2024级人工智能专业培养方案(主修)")})
    assert "human_ruling" not in other["evidence"][0]






def test_first_round_hallucination_is_not_emitted(monkeypatch):
    async def fake(*args, **kwargs):
        return {"type": "text", "content": "学校宿舍23点熄灯。"}
    monkeypatch.setattr(graph.model, "decide", fake)
    emitted = []
    state = asyncio.run(graph.agent_decide({"question": "宿舍几点熄灯？"}, emit=emitted.append))
    assert emitted == [] and state["final_answer"] is None
    assert state["tool_calls"][0]["name"] == "search_regulations"


def test_failed_or_no_hit_tools_cannot_authorize_evidence():
    r = ToolResult(tool="search_regulations", ok=True, status="irrelevant_result",
                   sources=[Source(title="无关", text="23点")])
    assert graph.append_evidence({"tool_results": [r]})["evidence"] == {}
    assert graph.tool_contexts(r, "obs") == []


def test_final_path_retries_once_with_structural_feedback(monkeypatch):
    obj = EvidenceLedger()
    item = obj.add("search_regulations", {}, Source(title="测试规定", text="公示3天。"))
    calls = []

    async def answer(messages, packet, **kwargs):
        calls.append(kwargs.get("repair"))
        if len(calls) == 1:
            return {"status": "answer", "claims": [], "slot_coverage": [],
                    "blocks": [], "missing": []}
        return draft_for(packet, "公示3天。", [item.evidence_id])

    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", answer)
    out = asyncio.run(graph.wrap_up({"question": "怎么公示？",
                                     "evidence": {item.evidence_id: item}}))
    assert out["final_answer"] == "公示3天。"
    assert len(calls) == 2 and calls[0] is None and calls[1]
    assert out["answer_audit"]["validation_retry"]


def test_graduate_no_evidence_uses_scope_boundary():
    out = asyncio.run(graph.wrap_up({"question": "人工智能专业研究生的毕业学分要求是多少？",
                                     "request_scope": RequestScope(level="graduate"),
                                     "evidence": {}}))
    assert out["final_answer"].startswith("当前培养方案工具仅覆盖本科资料")
    assert out["answer_audit"]["scope_explanation"] == "graduate_tool_boundary"


def test_no_ledger_closes_without_calling_answer_model(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("无证据时不应生成事实")
    monkeypatch.setattr(graph.llm_client, "answer_with_evidence", forbidden)
    result = asyncio.run(graph.wrap_up({"question": "宿舍几点熄灯？"}))
    assert result["final_answer"] == G.NO_EVIDENCE and result["contexts"] == []


def test_meta_identity_question_is_explicit_non_fact():
    expected = G.safe_non_fact("你是谁")
    assert expected and "SAI" in expected
    for question in ("你好，你是什么模型", "你是什么模型？",
                     "你好，请问你用的什么模型", "你是啥模型"):
        assert G.safe_non_fact(question) == expected




def test_college_and_staff_conflicts_are_explicit_not_inferred():
    s = request_scope("我是人工智能学院本科生，请问适用哪些规定？")
    assert s.college == "人工智能学院" and s.subject == "student"
    assert scope_conflict(s, {"college": "机电工程学院"}) == "scope_mismatch:college"
    assert scope_conflict(s, {"subject": "staff"}) == "scope_mismatch:subject"
    assert scope_conflict(s, {}) is None


def test_duplicate_copy_is_not_two_independent_evidences():
    obj = EvidenceLedger()
    obj.add("search_regulations", {}, Source(title="办法", chunk_id="65:1", text="相同正文"))
    obj.add("search_regulations", {}, Source(title="办法", chunk_id="85:1", text="相同正文"))
    p = G.evidence_packet({"evidence": {x.evidence_id: x for x in obj.items()}})
    assert len(p["evidence"]) == 1


def test_evidence_heading_is_not_copied_into_the_answer():
    """证据正文常以自身 Markdown 标题开头（reg-12b 的 ### 第七条）。

    heading_path 已经由后端写进来源行，标题属于重复的格式标记而不是内容，
    不能被原样透传成答案的第一行。
    """
    obj = EvidenceLedger()
    item = obj.add("search_regulations", {}, Source(
        title="转学工作实施细则",
        text="### 第七条\n\n公开公示。不少于5个工作日。"))
    packet = G.evidence_packet({"evidence": {item.evidence_id: item}})
    text, _ = G.render_draft(draft_for(
        packet, "### 第七条\n\n不少于5个工作日。", [item.evidence_id]), packet)
    assert not text.lstrip().startswith("#")
    assert text.startswith("不少于5个工作日")


def test_heading_only_block_is_left_alone_rather_than_emptied():
    """整块只有一个标题时不能剥成空串 —— 那会变成「有证据但正文为空」。"""
    obj = EvidenceLedger()
    item = obj.add("search_regulations", {}, Source(title="办法", text="### 第七条"))
    packet = G.evidence_packet({"evidence": {item.evidence_id: item}})
    text, _ = G.render_draft(draft_for(
        packet, "### 第七条", [item.evidence_id]), packet)
    assert text.strip()
