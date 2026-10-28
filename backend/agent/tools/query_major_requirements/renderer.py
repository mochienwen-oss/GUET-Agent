"""Observation renderer for query_major_requirements."""
import re

from agent.core import ToolResult, citations


def render(result: ToolResult) -> str:
    data = result.data
    if data.get("error"):
        if data["error"] == "structured_source_stale":
            return data["detail"]
        return "【未找到专业】%s\n候选：%s" % (
            data.get("query") or "—", "、".join(data.get("candidates") or []) or "（无）")
    lines = [
        "【专业培养要求】%s" % data["major"],
        "学院 %s｜学制 %s｜授予学位 %s" % (
            data.get("college") or "—", data.get("duration") or "—",
            data.get("degree") or "—"),
        "毕业总学分：%s（来源：%s）" % (
            data.get("total_credits", "—"), data.get("total_credits_source") or "未标注"),
    ]
    modules = data.get("module_credits") or {}
    if data.get("study_duration"):
        lines.append("允许修业年限：%s（不同于基本学制）" % data["study_duration"])
    lines.append("模块学分：" + ("、".join(
        "%s %s" % item for item in modules.items()) or "（未列出）"))
    lines.append("核心课程：" + ("、".join(data.get("core_courses") or []) or "（未列出）"))
    lines.append("主要实践课程：" + ("、".join(data.get("practice_courses") or []) or "（未列出）"))
    source = data.get("sources") or {}
    if source.get("file"):
        filename = re.sub(r"\.md$", "", citations.short_title(source["file"]))
        lines.append("出处：《%s》（培养方案）" % filename)
    if data.get("unavailable_fields"):
        lines.append("部分列表未能从第七节完整提取，空列表不表示该专业没有相关课程。")
    return "\n".join(lines)
