"""Public list_courses capability."""
import json

from langchain_core.tools import tool

from agent.core import Source, ToolResult
from agent.core.evidence import text_hash

from .schemas import ListCoursesArgs
from .scripts.query import list_courses


DESCRIPTION = (
    "列出某一个专业符合条件的多门课程，可按开课学期、课程模块、必修/选修、核心课程筛选。"
    "用户问「有哪些课」「第几学期学什么」「某模块课程清单」时用它；查询单门课程详情时用 query_course。"
)


def _sources(data: dict) -> list[Source]:
    raw = data.get("sources") or {}
    if not raw.get("file"):
        return []
    facts = {key: data.get(key) for key in ("major", "filters", "courses")}
    text = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    return [Source(title=raw["file"], text=text, text_hash=text_hash(text))]


def list_courses_result(major: str, semester: str | None = None,
                        module: str | None = None, required: bool | None = None,
                        core_only: bool = False) -> ToolResult:
    data = list_courses(major, semester=semester, module=module, required=required,
                        core_only=core_only)
    status = "no_evidence" if data.get("error") or not data.get("courses") else "success"
    return ToolResult(tool="list_courses", ok=True, status=status, data=data,
                      sources=_sources(data))


@tool("list_courses", args_schema=ListCoursesArgs, description=DESCRIPTION)
def list_courses_tool(major: str, semester: str | None = None,
                      module: str | None = None, required: bool | None = None,
                      core_only: bool = False) -> ToolResult:
    """Return courses in one major after applying all supplied filters."""
    return list_courses_result(major, semester, module, required, core_only)
