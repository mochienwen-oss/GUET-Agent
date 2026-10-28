"""Approved audience defaults, trusted follow-ups and per-block college limits."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import EvidenceLedger, Source
from agent.core.scope import request_scope, scope_conflict, RequestScope
from agent.runtime import grounded_answer as G


def test_default_student_scope_does_not_invent_college_cohort_or_major():
    scope = request_scope("国境外交流能自行延期或转去另一个国家学习吗？")
    assert (scope.level, scope.subject) == ("undergraduate", "student")
    assert scope.college is scope.cohort is None and scope.majors == ()
    assert scope.provenance == {"subject": "owner_default", "level": "owner_default"}


def test_explicit_staff_or_graduate_overrides_default():
    scope = request_scope("我是教师，进修期限是什么？")
    assert scope.subject == "staff" and scope.level is None
    scope = request_scope("硕士研究生的学位条件？")
    assert scope.level == "graduate" and scope.subject == "student"


def test_assistant_identity_is_ignored_and_new_topic_does_not_inherit():
    history = [{"role": "user", "content": "我是研究生"},
               {"role": "assistant", "content": "你是教职工"}]
    assert request_scope("那学位要求呢？", history=history).level == "graduate"
    assert request_scope("宿舍在哪？", history=history).level == "undergraduate"


def test_current_role_change_does_not_inherit_prior_cohort():
    scope = request_scope("那我是教师，进修条件呢？", history=[
        {"role": "user", "content": "2025级本科生的课程要求？"}])
    assert scope.subject == "staff" and scope.level is scope.cohort is None


def test_followup_carries_college_but_unrelated_topic_breaks_chain():
    scope = request_scope("那公示几天？", history=[
        {"role": "user", "content": "人工智能学院本科生的综合测评程序？"},
        {"role": "assistant", "content": "机电工程学院"}])
    assert scope.college == "人工智能学院" and scope.provenance["college"] == "prior_user"
    scope = request_scope("那地点呢？", history=[
        {"role": "user", "content": "研究生要求？"}, {"role": "user", "content": "食堂怎么走？"}])
    assert scope.level == "undergraduate"


def test_compared_document_levels_do_not_assign_a_personal_level():
    scope = request_scope("本科和硕士研究生的规定分别是什么？")
    assert scope.level is None and scope.provenance["level"] == "current_user_reference"
    assert set(scope.reference_levels) == {"undergraduate", "graduate"}


def test_teacher_as_process_actor_is_not_user_identity():
    assert request_scope("老师要求我提交材料，是否还需教师签字？").subject == "student"


def test_all_subject_metadata_is_compatible_but_real_conflict_stays_blocked():
    scope = request_scope("复学怎么办理？")
    assert scope_conflict(scope, {"subject": "all"}) is None
    assert scope_conflict(scope, {"subject": "staff"}) == "scope_mismatch:subject"
    assert scope_conflict(RequestScope(subject="staff"), {"subject": "all"}) is None


def test_final_packet_default_filters_staff_even_without_explicit_scope_state():
    ledger = EvidenceLedger()
    item = ledger.add("search_regulations", {}, Source(title="教职工进修管理办法", text="教职工办理进修。"))
    packet = G.evidence_packet({"question": "国境外交流可以延期吗？", "evidence": {item.evidence_id: item}})
    assert packet["evidence"] == []
    assert packet["scope_rejections"][0]["reason"] == "scope_mismatch:subject"


def test_foreign_exchange_packet_keeps_general_rules_but_not_domestic_exchange():
    ledger = EvidenceLedger()
    items = [ledger.add('search_regulations', {}, Source(title=title, text=text)) for title, text in (
        ('桂林电子科技大学赴国(境)外交流学习管理办法', '境外交流期限。'),
        ('桂林电子科技大学国内交换培养管理办法', '国内交换期限。'),
        ('桂林电子科技大学学生学籍管理规定', '一般学籍规定。'))]
    packet = G.evidence_packet({'question': '国境外交流能自行延期吗？',
                                'evidence': {item.evidence_id: item for item in items}})
    assert {r['text'] for r in packet['evidence']} == {'境外交流期限。', '一般学籍规定。'}
    assert packet['scope_rejections'] == [{'evidence_id': items[1].evidence_id,
                                           'reason': 'scope_mismatch:exchange_region'}]


def test_college_note_is_local_to_each_answer_block_not_union_of_applicability():
    packet = {"evidence": [
        {"evidence_id": "a", "title": "甲学院办法", "heading_path": [], "text": "甲的规定", "scope": {"college": "甲学院"}},
        {"evidence_id": "b", "title": "乙学院办法", "heading_path": [], "text": "乙的规定", "scope": {"college": "乙学院"}}]}
    packet["evidence_units"] = [unit for row in packet["evidence"]
                                for unit in G.evidence_units(row["evidence_id"], row["text"], row)]
    packet["question_brief"] = {"required_slots": ["conclusion"]}
    text, _ = G.render_draft({"status": "answer",
        "claims": [
            {"claim_id": "c1", "claim_type": "conclusion", "question_slot": "conclusion",
             "text": "甲流程", "unit_ids": [packet["evidence_units"][0]["unit_id"]],
             "supplement_class": "required"},
            {"claim_id": "c2", "claim_type": "conclusion", "question_slot": "conclusion",
             "text": "乙流程", "unit_ids": [packet["evidence_units"][1]["unit_id"]],
             "supplement_class": "required"}],
        "slot_coverage": [{"slot": "conclusion", "status": "covered",
                           "claim_ids": ["c1", "c2"], "reason": ""}],
        "blocks": [{"claim_ids": ["c1"]},
                   {"claim_ids": ["c2"]}], "missing": []}, packet)
    assert "仅适用于甲学院" in text and "仅适用于乙学院" in text
    assert "适用于甲学院、乙学院" not in text


def test_transfer_conflict_uses_specific_section_and_ignores_credit_conversion():
    from agent.core.scope import business_scope_conflict
    assert business_scope_conflict('能办理转学吗？', '转专业管理办法') == 'scope_mismatch:transfer_business'
    assert business_scope_conflict('能转专业吗？', '转学工作实施细则') == 'scope_mismatch:transfer_business'
    assert business_scope_conflict('转学和转专业分别怎么办？', '转专业管理办法') is None
    assert business_scope_conflict('能办理转学吗？', '学籍管理规定') is None
    assert business_scope_conflict('能办理转学吗？', '转专业与转学规定',
                                   heading_path=['转专业与转学', '转专业', '第十二条'])
    assert business_scope_conflict('课程转学分怎么办？', '转专业管理办法') is None
    assert business_scope_conflict('那什么时候申请？', '转专业管理办法', history=[
        {'role': 'user', 'content': '入学半年能转学吗？'},
        {'role': 'assistant', 'content': '你问的是转专业。'}])


def test_transfer_packet_filters_other_business_but_keeps_unknown_background():
    ledger = EvidenceLedger()
    items = [ledger.add('search_regulations', {}, Source(title=title, heading_path=path, text=text))
             for title, path, text in (
        ('转专业管理办法', ['第四章转专业'], '转专业规定。'),
        ('学籍管理规定', ['第七章转专业与转学'], '一般学籍规定。'),
        ('转学工作实施细则', ['第二章转学条件'], '转学规定。'))]
    packet = G.evidence_packet({'question': '入学还没满一学期能办理转学吗？',
                               'evidence': {x.evidence_id: x for x in items}})
    assert [(r['text'], r['evidence_role']) for r in packet['evidence']] == [
        ('转学规定。', 'primary'), ('一般学籍规定。', 'background')]
    assert packet['scope_rejections'] == [{'evidence_id': items[0].evidence_id,
                                           'reason': 'scope_mismatch:transfer_business'}]


def test_regulation_packet_does_not_promote_every_independent_hit_to_primary():
    ledger = EvidenceLedger()
    items = [ledger.add('search_regulations', {}, Source(title=title, text=text)) for title, text in (
        ('2025级人工智能专业培养方案(主修)', '培养方案通识选修模块。'),
        ('本科生课程认定暂行办法', '通识选修课不能认定为必修课。'),
        ('一般办理说明', '其他说明。'))]
    packet = G.evidence_packet({'question': '通识选修课能认定成必修课吗？',
                               'evidence': {x.evidence_id: x for x in items}})
    assert [r['evidence_role'] for r in packet['evidence']] == ['primary', 'candidate', 'background']
    assert all(r['retrieval_origin'] == 'independent' for r in packet['evidence'])
    assert len(packet['evidence_organization']['candidate_ids']) == 1
    assert len(packet['evidence_organization']['background_ids']) == 1


def test_source_rendering_preserves_all_used_locations_but_not_unused_evidence():
    from agent.core.citations import finalize_answer
    packet = {'evidence': [
        {'evidence_id': str(i), 'title': '学业预警规定', 'heading_path': [heading],
         'text': text, 'scope': {}}
        for i, (heading, text) in enumerate((('第五条', '预警等级。'),
                                              ('第十一条', '年级适用范围。'),
                                              ('第十二条', '其他事项。')))]}
    packet["evidence_units"] = [unit for row in packet["evidence"]
                                for unit in G.evidence_units(row["evidence_id"], row["text"], row)]
    packet["question_brief"] = {"required_slots": ["conclusion"]}
    body, contexts = G.render_draft({'status': 'answer',
        'claims': [{'claim_id': 'c1', 'claim_type': 'conclusion', 'question_slot': 'conclusion',
                    'text': '预警等级及年级范围。',
                    'unit_ids': [packet["evidence_units"][0]["unit_id"],
                                  packet["evidence_units"][1]["unit_id"]],
                    'supplement_class': 'required'}],
        'slot_coverage': [{'slot': 'conclusion', 'status': 'covered',
                           'claim_ids': ['c1'], 'reason': ''}],
        'blocks': [{"claim_ids": ['c1']}], 'missing': []}, packet)
    answer, _ = finalize_answer(body, contexts)
    assert answer.count('《学业预警规定》') == 1
    assert '第五条；第十一条' in answer and '第十二条' not in answer
