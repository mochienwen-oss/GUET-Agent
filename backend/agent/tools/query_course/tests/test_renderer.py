from agent.runtime import registry


def test_card_renderer_contains_all_returned_location_fields():
    result = registry.call("query_course", {
        "major": "计算机科学与技术", "course": "数据库系统原理A",
    })
    text = registry.render(result)
    assert "【课程定位卡】" in text
    assert "课程代码" in text and "所属模块" in text
    assert "支撑的毕业要求指标点" in text
    assert "出处：《" in text
