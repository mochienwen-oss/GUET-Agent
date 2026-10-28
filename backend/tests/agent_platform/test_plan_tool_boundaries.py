"""Phase 5B-2 role boundaries for the four structured plan tools."""
from agent.runtime import registry
from agent.tools.compare_course.schemas import PARAMETERS as COMPARE_PARAMETERS
from agent.tools.list_courses.schemas import PARAMETERS as LIST_PARAMETERS
from agent.tools.query_course.schemas import PARAMETERS as COURSE_PARAMETERS
from agent.tools.query_major_requirements.schemas import PARAMETERS as REQUIREMENTS_PARAMETERS


def test_four_plan_tools_have_no_mode_parameter():
    for parameters in (
        COURSE_PARAMETERS, COMPARE_PARAMETERS, LIST_PARAMETERS, REQUIREMENTS_PARAMETERS,
    ):
        assert "mode" not in parameters["properties"]


def test_each_tool_keeps_its_own_answer_shape():
    card = registry.call("query_course", {
        "major": "计算机科学与技术", "course": "数据库系统原理A",
    })
    listed = registry.call("list_courses", {"major": "计算机科学与技术"})
    requirements = registry.call(
        "query_major_requirements", {"major": "计算机科学与技术"})

    assert "course" in card.data and "courses" not in card.data
    assert "courses" in listed.data
    assert all("indicators" not in course for course in listed.data["courses"])
    assert "courses" not in requirements.data and "total_credits" in requirements.data


def test_compare_still_keeps_a_row_for_a_major_without_the_course():
    result = registry.call("compare_course", {
        "course": "数据库系统原理A",
        "majors": ["计算机科学与技术", "英语"],
    })
    assert len(result.data["rows"]) == 2
    assert any(row["found"] is False for row in result.data["rows"])

