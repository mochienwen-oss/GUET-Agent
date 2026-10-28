"""Pure observation renderer for compare_course."""
import re

from agent.core import citations

from agent.core import ToolResult


def render(result: ToolResult) -> str:
    data = result.data
    rows = data.get("rows") or []
    if not rows:
        return "【对比】没有找到可对比的专业。"
    out = [
        "【跨专业对比】%s" % data["course_query"], "",
        "| 专业 | 开课情况 | 学分 | 总学时 | 开课学期 | 课程性质 | 课程名 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    source_lines = []
    for row in rows:
        if not row.get("found"):
            out.append("| %s | 未开设 | — | — | — | — | — |" % row["major"])
        else:
            course = row["course"]
            out.append("| %s | 开设 | %s | %s | %s | %s | %s |" % (
                row["major"], course.get("credits"), course.get("hours"),
                course.get("semester") or "—", course.get("nature") or "—",
                course.get("name")))
        source = row.get("sources") or {}
        if source.get("file"):
            filename = re.sub(r"\.md$", "", citations.short_title(source["file"]))
            source_lines.append("- %s：《%s》%s" % (
                row["major"], filename,
                "第 %s 行" % source["line"] if source.get("line") else ""))
    if source_lines:
        out.extend(["", "各专业来源：", *source_lines])
    if data.get("unresolved_majors"):
        out.extend(["", "以下专业名没对上：%s" % "、".join(
            item["major"] for item in data["unresolved_majors"])])
    return "\n".join(out)
