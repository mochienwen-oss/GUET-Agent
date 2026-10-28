from agent.runtime import registry


def test_create_ppt_requires_goal_and_audience():
    missing = registry.call("create_ppt", {"goal": "做一份培养方案介绍"})
    blank = registry.call("create_ppt", {"goal": " ", "audience": "新生"})
    assert missing.error.code == "invalid_args"
    assert blank.error.code == "invalid_args"


def test_create_ppt_returns_structured_handoff_without_generating_file():
    result = registry.call("create_ppt", {
        "goal": "介绍计算机科学与技术培养方案", "audience": "2026级新生",
        "page_count": 10, "style": "简洁", "constraints": ["突出课程结构"],
    })
    assert result.status == "success"
    assert result.data["handoff"] == "ppt_subgraph"
    assert result.data["brief"]["goal"] == "介绍计算机科学与技术培养方案"
    assert result.data["brief"]["audience"] == "2026级新生"
    assert result.sources == []
