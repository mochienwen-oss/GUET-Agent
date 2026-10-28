"""Pure observation renderer for query_course."""
import re

from agent.core import citations

from agent.core import ToolResult


def _sem(value) -> str:
    text = str(value or "").strip()
    return "第%s学期" % text if text.isdigit() else (text or "—")


def render(result: ToolResult) -> str:
    data = result.data
    if data.get("error"):
        return "【未找到】%s\n候选：%s" % (
            data["error"], "、".join(data.get("candidates") or []) or "（无）")
    course = data["course"]
    hours = " / ".join(
        "—" if course.get(key) is None else str(course[key])
        for key in ("theory", "lab", "practice")
    )
    path = " › ".join(course.get("module_path") or []) or (course.get("module") or "—")
    lines = [
        "【课程定位卡】%s · %s" % (course["name"], data["major"]),
        "课程代码 %s｜课程性质 %s｜课程地位 %s" % (
            course.get("code") or "—", course.get("nature") or "—",
            "核心课程" if course.get("is_core") else
            ("主要实践环节" if course.get("is_practice") else "—")),
        "学分 %s｜总学时 %s｜理论/实验/实践 %s" % (
            course["credits"], course["hours"], hours),
        "开课学期 %s｜开课部门 %s｜所属模块 %s" % (
            _sem(course.get("semester")), course.get("dept") or "—", path),
        "所在专业：%s｜学制 %s｜%s｜毕业总学分 %s" % (
            data.get("college") or "—", data.get("duration") or "—",
            data.get("degree") or "—", data.get("total_credits", "—")),
    ]
    module_credits = data.get("module_credits") or {}
    if module_credits:
        lines.append("各模块学分明细：" + "、".join(
            "%s %s" % (key, value) for key, value in module_credits.items()))
    indicators = data.get("indicators") or []
    if indicators:
        lines.append("支撑的毕业要求指标点：")
        lines.extend("  %s %s" % (item["id"], item["text"]) for item in indicators)
    if data.get("alternatives"):
        lines.append("（另有同名课程：%s）" % "、".join(data["alternatives"]))
    source = data.get("sources") or {}
    if source.get("file"):
        filename = re.sub(r"\.md$", "", citations.short_title(source["file"]))
        lines.append("出处：《%s》（培养方案）%s" % (
            filename, "第 %s 行" % source["line"] if source.get("line") else ""))
    return "\n".join(lines)
