import json
from pathlib import Path

from agent.tools.search_regulations.scripts.querying.query_quality import (
    audit_query_args,
    load_lexicon,
)


def _codes(issues):
    return {item.code for item in issues}


def test_reviewed_alias_can_expand_to_canonical_term():
    lexicon = {"entries": [{
        "canonical": "转专业", "aliases": ["换专业", "换个专业"],
        "domain": "student_status", "reviewed": True,
    }]}
    issues = audit_query_args("我想换个专业", "转专业有什么要求", ["转专业"], lexicon)
    assert issues == []


def test_unreviewed_or_unknown_expansion_is_reported():
    issues = audit_query_args("我想换个专业", "转专业有什么要求", ["录取名额"],
                              {"entries": []})
    assert "unknown_expansion" in _codes(issues)


def test_answer_side_exact_document_title_is_reported():
    issues = audit_query_args(
        "补考没过怎么办", "补考没过怎么办",
        ["桂林电子科技大学普通高等教育本科学生学籍管理规定"],
        {"entries": []},
    )
    assert "answer_title_leak" in _codes(issues)


def test_title_is_allowed_when_user_explicitly_names_it():
    title = "桂林电子科技大学普通高等教育本科学生学籍管理规定"
    issues = audit_query_args(f"{title}怎么规定补考？", "补考怎么规定", [title],
                              {"entries": []})
    assert "answer_title_leak" not in _codes(issues)


def test_numeric_fact_not_present_in_user_is_reported():
    issues = audit_query_args("博士后一年能拿多少", "博士后薪酬标准", ["每年23万元"],
                              {"entries": []})
    assert "numeric_fact_expansion" in _codes(issues)


def test_blank_query_and_too_many_keywords_are_reported_independently():
    issues = audit_query_args("原问句", "  ", ["词%d" % i for i in range(13)],
                              {"entries": []})
    assert {"blank_semantic_query", "too_many_keywords"} <= _codes(issues)


def test_checked_in_lexicon_has_only_reviewed_well_formed_entries():
    lexicon = load_lexicon()
    assert lexicon["version"] == "query_expansions/1"
    assert lexicon["entries"]
    assert all(row["reviewed"] is True for row in lexicon["entries"])
    assert all(row["canonical"] and row["aliases"] and row["domain"]
               for row in lexicon["entries"])
    # 文件必须也是普通 JSON，方便数据编译器和人工审计复用。
    path = Path(__file__).parents[1] / "data" / "query_expansions.json"
    assert json.loads(path.read_text(encoding="utf-8")) == lexicon
