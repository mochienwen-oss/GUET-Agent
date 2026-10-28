from agent.runtime import registry


def test_renderer_is_a_course_list_not_a_course_position_card():
    result = registry.call("list_courses", {
        "major": "计算机科学与技术", "semester": "3", "core_only": True,
    })
    text = registry.render(result)
    assert "【课程清单】" in text
    assert "【课程定位卡】" not in text
    assert "课程代码" in text


def test_empty_result_renderer_keeps_the_filter_explanation():
    result = registry.call("list_courses", {
        "major": "计算机科学与技术", "semester": "99",
    })
    text = registry.render(result)
    assert "没有符合筛选条件" in text
    assert "99" in text

