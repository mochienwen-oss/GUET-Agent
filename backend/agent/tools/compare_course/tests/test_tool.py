from agent.runtime import registry


def test_partial_comparison_keeps_unopened_major_row():
    result = registry.call("compare_course", {
        "course": "交通工程学", "majors": ["交通工程", "计算机科学与技术"],
    })
    assert result.status == "partial_evidence"
    assert [row["major"] for row in result.data["rows"]] == ["交通工程", "计算机科学与技术"]
    assert result.data["rows"][0]["found"] is True
    assert result.data["rows"][1]["found"] is False
    assert len(result.sources) == 1


def test_compare_requires_two_distinct_majors():
    one = registry.call("compare_course", {"course": "高等数学", "majors": ["计算机科学与技术"]})
    duplicate = registry.call("compare_course", {
        "course": "高等数学", "majors": ["计算机科学与技术", "计算机科学与技术"],
    })
    assert one.error.code == "invalid_args"
    assert duplicate.error.code == "invalid_args"
