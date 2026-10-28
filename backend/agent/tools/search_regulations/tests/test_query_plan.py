from agent.tools.search_regulations.scripts.query_plan import build_query_plan


def test_no_keywords_preserves_one_query_for_all_retrieval_paths():
    plan = build_query_plan("补考有什么规定", [], None)
    assert plan.semantic_query == "补考有什么规定"
    assert plan.sparse_query == "补考有什么规定"
    assert plan.keywords == ()


def test_keywords_only_change_sparse_query():
    plan = build_query_plan("补考没过还能再考吗", ["补考", "课程考核"], None)
    assert plan.semantic_query == "补考没过还能再考吗"
    assert plan.sparse_query == "补考 课程考核"
    assert plan.keywords == ("补考", "课程考核")


def test_previous_turn_enriches_semantic_query_but_not_explicit_keywords():
    plan = build_query_plan("我大二，自动化专业", ["转专业", "申请条件"], "转专业有什么要求")
    assert plan.semantic_query == "转专业有什么要求 我大二，自动化专业"
    assert plan.sparse_query == "转专业 申请条件"


def test_previous_turn_is_used_by_sparse_fallback_when_keywords_are_absent():
    plan = build_query_plan("我大二，自动化专业", [], "转专业有什么要求")
    assert plan.sparse_query == plan.semantic_query


def test_previous_turn_is_trimmed_to_existing_120_character_boundary():
    plan = build_query_plan("补充", [], "甲" * 150)
    assert plan.semantic_query == ("甲" * 120) + " 补充"
