from agent.core import ToolError, ToolResult
from agent.runtime import registry


def _compare(course, majors):
    return registry.call("compare_course", {"course": course, "majors": majors})


def test_partial_renderer_shows_every_major_and_real_source():
    result = registry.call("compare_course", {
        "course": "交通工程学", "majors": ["交通工程", "计算机科学与技术"],
    })
    text = registry.render(result)
    assert "交通工程 | 开设" in text
    assert "计算机科学与技术 | 未开设" in text
    assert "各专业来源：" in text


# ★ 课程性质正是一直缺的那一列：状态是 success/partial_evidence，但 observation 里
#   看不到性质，teacher 只能猜。性质的真值只有一处来源 —— 结构化课程记录的 `nature`。
def test_both_majors_found_show_each_major_nature():
    result = _compare("航天技术概论", ["导航工程", "微电子科学与工程"])
    assert result.status == "success"
    text = registry.render(result)
    assert "课程性质" in text, text
    for row in result.data["rows"]:
        nature = row["course"]["nature"]
        assert nature, row
        assert "| %s | 开设" % row["major"] in text, text
        assert nature in text.split(row["major"], 1)[1].split("\n", 1)[0], text


def test_partial_shows_nature_on_found_side_and_dash_on_missing_side():
    result = _compare("大数据财务分析与可视化实训", ["会计学", "财务管理"])
    assert result.status == "partial_evidence"
    text = registry.render(result)
    found = [r for r in result.data["rows"] if r["found"]][0]
    line = next(l for l in text.splitlines() if l.startswith("| %s |" % found["major"]))
    assert found["course"]["nature"] in line, line
    missing = [r for r in result.data["rows"] if not r["found"]][0]
    missing_line = next(l for l in text.splitlines() if l.startswith("| %s |" % missing["major"]))
    assert missing_line.count("—") >= 4, missing_line


def test_existing_columns_are_unchanged():
    result = _compare("大学英语1(艺术)", ["产品设计", "动画"])
    text = registry.render(result)
    for row in result.data["rows"]:
        course = row["course"]
        line = next(l for l in text.splitlines() if l.startswith("| %s |" % row["major"]))
        assert "| %s |" % course["credits"] in line, line
        assert "| %s |" % course["hours"] in line, line
        assert "| %s |" % (course["semester"] or "—") in line, line
        assert course["name"] in line, line
    assert "各专业来源：" in text
    assert "《2025级产品设计专业培养方案(主修)》" in text


def test_tool_failed_rendering_is_untouched():
    failed = ToolResult(tool="compare_course", ok=False, status="tool_failed",
                        error=ToolError("invalid_args", "majors: 至少两个专业"))
    text = registry.render(failed)
    assert "课程性质" not in text
    assert "未检索到" not in text
    assert "不代表学校没有相关规定" in text


def test_tq_013_observation_shows_the_required_nature():
    """tq-013：会计学侧的课程性质必须直接可见 —— rubric 要求保留「必修」。"""
    text = registry.render(_compare("大数据财务分析与可视化实训", ["会计学", "财务管理"]))
    line = next(l for l in text.splitlines() if l.startswith("| 会计学 |"))
    assert "必修" in line, line
    assert "未开设" in text


def test_tq_020_observation_shows_both_natures():
    """tq-020：两个专业各自的课程性质都要直接可见。"""
    result = _compare("航天技术概论", ["导航工程", "微电子科学与工程"])
    text = registry.render(result)
    for major in ("导航工程", "微电子科学与工程"):
        line = next(l for l in text.splitlines() if l.startswith("| %s |" % major))
        assert result.data["rows"][0]["course"]["nature"] in line or \
            result.data["rows"][1]["course"]["nature"] in line, line