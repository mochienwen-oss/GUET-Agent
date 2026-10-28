from agent.runtime import registry


MAJOR = "计算机科学与技术"


def test_no_filter_returns_every_course_for_the_major():
    result = registry.call("list_courses", {"major": MAJOR})
    assert result.status == "success"
    assert result.data["count"] == len(result.data["courses"]) > 50
    assert all("indicators" not in course for course in result.data["courses"])


def test_each_filter_and_combined_filters_are_applied():
    core = registry.call("list_courses", {"major": MAJOR, "core_only": True})
    assert core.data["courses"]
    assert all(course["is_core"] for course in core.data["courses"])

    semester = registry.call("list_courses", {"major": MAJOR, "semester": "3"})
    assert semester.data["courses"]
    assert all("3" in course["semester"].replace("，", ",").split(",")
               for course in semester.data["courses"])

    module = registry.call("list_courses", {"major": MAJOR, "module": "学科基础课程"})
    assert module.data["courses"]
    assert all("学科基础课程" in course["module_path"] for course in module.data["courses"])

    combined = registry.call("list_courses", {
        "major": MAJOR, "semester": "3", "module": "学科基础课程",
        "required": True, "core_only": True,
    })
    assert all(
        course["required"] and course["is_core"]
        and "学科基础课程" in course["module_path"]
        and "3" in course["semester"].replace("，", ",").split(",")
        for course in combined.data["courses"]
    )


def test_empty_filter_result_is_explained_without_execution_error():
    result = registry.call("list_courses", {"major": MAJOR, "semester": "99"})
    assert result.ok is True and result.status == "no_evidence"
    assert result.data["courses"] == []
    assert result.data["unresolved"]


def test_major_miss_returns_candidates():
    result = registry.call("list_courses", {"major": "完全不存在专业XYZ"})
    assert result.status == "no_evidence"
    assert result.data["error"] == "major_not_found"
    assert isinstance(result.data["candidates"], list)

