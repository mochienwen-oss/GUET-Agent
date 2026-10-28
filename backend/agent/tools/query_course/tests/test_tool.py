from agent.runtime import registry


def test_exact_course_returns_complete_card_and_source():
    result = registry.call("query_course", {
        "major": "计算机科学与技术", "course": "数据库系统原理A",
    })
    assert result.status == "success"
    assert result.data["course"]["name"] == "数据库系统原理A"
    assert result.data["course"]["credits"] == 3.0
    assert result.data["indicators"]
    assert result.sources and result.sources[0].line


def test_course_miss_returns_candidates_as_no_evidence():
    result = registry.call("query_course", {
        "major": "计算机科学与技术", "course": "量子力学导论",
    })
    assert result.status == "no_evidence"
    assert result.data["error"] == "course_not_found"
    assert isinstance(result.data["candidates"], list)
    assert result.sources == []


def test_blank_argument_is_rejected_before_repository_call():
    result = registry.call("query_course", {"major": " ", "course": "数据库系统原理A"})
    assert result.status == "tool_failed"
    assert result.error.code == "invalid_args"
