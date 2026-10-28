from agent.runtime import registry


def test_renderer_is_distinct_from_course_cards_and_lists():
    result = registry.call("query_major_requirements", {"major": "计算机科学与技术"})
    text = registry.render(result)
    assert "【专业培养要求】" in text
    assert "【课程定位卡】" not in text
    assert "【课程清单】" not in text
    assert "毕业总学分" in text

