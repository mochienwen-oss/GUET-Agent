from agent.runtime import registry


def test_requirements_returns_major_level_facts_and_source():
    result = registry.call("query_major_requirements", {"major": "计算机科学与技术"})
    assert result.status == "success"
    assert result.data["total_credits"] == 170.0
    assert result.data["module_credits"]
    assert result.data["core_courses"]
    assert result.data["practice_courses"]
    assert result.sources and result.sources[0].title.endswith(".md")


def test_requirements_major_miss_returns_candidates():
    result = registry.call("query_major_requirements", {"major": "完全不存在专业XYZ"})
    assert result.status == "no_evidence"
    assert result.data["error"] == "major_not_found"
    assert isinstance(result.data["candidates"], list)


def test_requirements_does_not_return_a_full_course_table():
    result = registry.call("query_major_requirements", {"major": "计算机科学与技术"})
    assert "courses" not in result.data
    assert "indicators" not in result.data

