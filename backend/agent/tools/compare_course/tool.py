"""Public compare_course capability composed from query_course's public API."""
from langchain_core.tools import tool

from agent.core import Source, ToolResult
from agent.core.evidence import identity
from agent.tools.query_course.tool import query_course_result

from .schemas import CompareCourseArgs


# ★ 冻结：逐字等于 LoRA 训练列里的 description（与 schemas.py 的 PARAMETERS 同一份来源）。
DESCRIPTION = (
    "对比**两个及以上专业**里同一门课程的学分、学时、开课学期、课程性质差异。"
    "问句里出现**多个专业名**，或问「哪个先开」「一样吗」「差多少」「对比」「都是必修吗」时用它 —— "
    "不要退回 [query_course] 只查其中一个专业。"
)


def _row(requested_major: str, child: ToolResult) -> dict:
    data = child.data
    if data.get("error"):
        return {
            "major": data.get("major") or requested_major,
            "found": False,
            "candidates": data.get("candidates") or [],
        }
    course = data["course"]
    return {
        "major": data["major"],
        "college": data.get("college"),
        "found": True,
        "course": {key: course.get(key) for key in (
            "name", "credits", "hours", "semester", "nature", "module", "is_core")},
        "match": data.get("match"),
        "sources": data.get("sources") or {},
    }


def compare_course_result(course: str, majors: list[str]) -> ToolResult:
    children = [(major, query_course_result(major, course)) for major in majors]
    rows = [_row(major, child) for major, child in children]
    found = [row for row in rows if row.get("found")]
    unresolved = [
        {"major": major, "candidates": child.data.get("candidates") or []}
        for major, child in children if child.data.get("error") == "major_not_found"
    ]
    data = {
        "course_query": course,
        "found_in": len(found),
        "rows": rows,
        "sources": [row["sources"] for row in found],
    }
    if unresolved:
        data["unresolved_majors"] = unresolved
    if not found:
        data["error"] = "course_not_found"
        status = "no_evidence"
    elif len(found) != len(rows):
        status = "partial_evidence"
    else:
        status = "success"
    sources: list[Source] = []
    seen = set()
    for _, child in children:
        for source in child.sources:
            key = identity(source)
            if key not in seen:
                seen.add(key)
                sources.append(source)
    return ToolResult(tool="compare_course", ok=True, status=status, data=data, sources=sources)


@tool("compare_course", args_schema=CompareCourseArgs, description=DESCRIPTION)
def compare_course_tool(course: str, majors: list[str]) -> ToolResult:
    """Compare one course across every requested major."""
    return compare_course_result(course, majors)
