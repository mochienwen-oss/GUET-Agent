from pathlib import Path

import pytest

from agent.tools.search_regulations.scripts.evidence_ops import build_gold
from agent.tools.search_regulations.scripts.evidence_ops.gold import load_gold, validate_case


def test_builder_uses_only_reviewed_independent_sources_and_is_deterministic():
    first = build_gold.build_rows()
    second = build_gold.build_rows()
    assert build_gold.encoded(first) == build_gold.encoded(second)
    assert len(first) == 62
    assert sum(row["answerable"] for row in first) == 48
    assert sum(not row["answerable"] for row in first) == 14
    assert sum(row["expected_tool"] == "search_regulations" for row in first) == 47
    assert sum(row["expected_tool"] == "query_course" for row in first) == 15
    assert {row["source_dataset"] for row in first} == {
        "eval_holdout_src.jsonl", "agent_acceptance.json"}


def test_checked_in_gold_matches_builder_byte_for_byte():
    path = build_gold.DEFAULT_OUT
    assert path.read_text(encoding="utf-8") == build_gold.encoded(build_gold.build_rows())
    assert len(load_gold(path)) == 62


def test_answerable_and_negative_invariants_are_strict():
    base = {
        "id": "x", "query": "q", "answerable": True,
        "expected_tool": "search_regulations",
        "expected_docs": ["甲"], "expected_evidence": ["原文"],
        "acceptable_gate": ["supported"], "source_dataset": "x.jsonl",
        "source_id": 1, "review_status": "reviewed",
    }
    assert validate_case(dict(base))["id"] == "x"
    bad = dict(base, expected_evidence=[])
    with pytest.raises(ValueError):
        validate_case(bad)
    bad = dict(base, answerable=False)
    with pytest.raises(ValueError):
        validate_case(bad)
