# -*- coding: utf-8 -*-
from agent.tools.search_regulations.scripts import intent, retrieval


def _chunk(doc, idx):
    return {"document_id": doc, "chunk_idx": idx, "source": f"文档{doc}",
            "snippet": f"文档{doc}块{idx}", "text": f"文档{doc}块{idx}",
            "heading_path": []}


def _near_chunk(doc, source, suffix):
    text = ("成果转化收益按到位技术交易金额核算，技术作价金额按照相关规定执行。"
            "申报人员应当提交完整证明材料，并由有关部门审核确认。" + suffix)
    return {"document_id": doc, "chunk_idx": 1, "source": source,
            "snippet": text, "text": text, "heading_path": []}


def test_phase5a_topics_are_general_routes_not_question_literals():
    intent.load_routes()
    cases = {
        "我导师去年去外校访学，今年还能再申请吗？": "教职工进修",
        "自然科学系列副研究员的成果转化收入怎么折算？": "职称成果转化",
        "《创新创业拓展》累计超过100分怎么算？": "创新创业拓展",
    }
    for question, family in cases.items():
        detected = intent.detect(question)
        assert family in detected["topics"]
        assert detected["aux_queries"]


def test_focus_selection_reserves_one_extra_slot_for_the_focused_document(monkeypatch):
    """聚焦文档保留 cap+1 个块；其余文档仍受原 cap 限制。"""
    monkeypatch.setattr(retrieval.settings, "max_chunks_per_doc", 2)
    ranked = [_chunk(2, 1), _chunk(2, 2), _chunk(1, 1),
              _chunk(1, 2), _chunk(3, 1), _chunk(2, 3)]

    selected = retrieval._cap_per_doc_focus(ranked, top_k=4,
                                             focus_did=2, focus_cap=4)

    assert [(x["document_id"], x["chunk_idx"]) for x in selected] == [
        (2, 1), (2, 2), (1, 1), (2, 3)]


def test_focus_selection_without_focus_is_exactly_the_old_selector(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "max_chunks_per_doc", 2)
    ranked = [_chunk(1, 1), _chunk(1, 2), _chunk(1, 3), _chunk(2, 1)]
    assert retrieval._cap_per_doc_focus(ranked, 3, None, 4) == \
        retrieval._cap_per_doc(ranked, 3)


def test_aux_template_title_match_wins_over_unrelated_top_rank(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "aux_focus_topn", 3)
    monkeypatch.setattr(retrieval.settings, "aux_focus_need", 2)
    aux_query = ("桂林电子科技大学高等学校自然科学研究系列职称评审条件 "
                 "成果转化收益 到位技术交易金额")
    aux = [
        dict(_chunk(1, 1), source="实验技术系列职称评审条件(试行)",
             aux_query=aux_query),
        dict(_chunk(2, 1), source="高等学校教师系列职称评审条件(2022年修订)",
             aux_query=aux_query),
        dict(_chunk(3, 1), source="高等学校自然科学研究系列职称评审条件(试行)",
             aux_query=aux_query),
    ]

    result = retrieval._with_aux_focus([], aux)

    assert result.focus_document_id == 3


def test_near_dedupe_never_merges_separate_regulations():
    rows = [
        _near_chunk(1, "高等学校社会科学研究系列职称评审条件(试行)", "社会科学系列"),
        _near_chunk(2, "高等学校自然科学研究系列职称评审条件(试行)", "自然科学系列"),
    ]
    for rank, row in enumerate(rows, 1):
        row["rrf"] = 1 / rank

    assert retrieval._dedupe_near(rows, .6) == rows


def test_near_dedupe_still_merges_templates_from_different_major_plans():
    rows = [
        _near_chunk(1, "2025级甲专业培养方案(主修)", "甲专业"),
        _near_chunk(2, "2025级乙专业培养方案(主修)", "乙专业"),
    ]
    for rank, row in enumerate(rows, 1):
        row["rrf"] = 1 / rank

    assert retrieval._dedupe_near(rows, .6) == [rows[0]]


def test_near_dedupe_does_not_merge_two_chunks_inside_one_plan():
    rows = [
        _near_chunk(1, "2025级甲专业培养方案(主修)", "第一章"),
        _near_chunk(1, "2025级甲专业培养方案(主修)", "第二章"),
    ]
    for rank, row in enumerate(rows, 1):
        row["rrf"] = 1 / rank

    assert retrieval._dedupe_near(rows, .6) == rows
