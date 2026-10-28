"""Observation renderer for list_courses."""
import re

from agent.core import ToolResult, citations


def render(result: ToolResult) -> str:
    data = result.data
    if data.get("error"):
        return "【未找到专业】%s\n候选：%s" % (
            data.get("query") or "—", "、".join(data.get("candidates") or []) or "（无）")
    courses = data.get("courses") or []
    lines = ["【课程清单】%s（%s 门）" % (data["major"], len(courses))]
    filters = {key: value for key, value in (data.get("filters") or {}).items()
               if value not in (None, False)}
    if filters:
        lines.append("筛选条件：" + "；".join("%s=%s" % item for item in filters.items()))
    if not courses:
        lines.append(data.get("unresolved") or "没有符合筛选条件的课程。")
    else:
        lines.extend([
            "| 课程代码 | 课程名称 | 学分 | 学期 | 性质 | 模块 |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for course in courses:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                course.get("code") or "—", course.get("name") or "—",
                course.get("credits"), course.get("semester") or "—",
                course.get("nature") or "—", course.get("module") or "—"))
    source = data.get("sources") or {}
    if source.get("file"):
        filename = re.sub(r"\.md$", "", citations.short_title(source["file"]))
        lines.append("出处：《%s》（培养方案）" % filename)
    return "\n".join(lines)
