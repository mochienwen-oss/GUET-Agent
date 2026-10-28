"""Public query_major_requirements capability."""
import json

from langchain_core.tools import tool

from agent.core import Source, ToolResult
from agent.core.evidence import text_hash

from .schemas import QueryMajorRequirementsArgs
from .scripts.query import query_major_requirements


DESCRIPTION = (
    "查询某一个专业整体培养要求：毕业总学分、各模块学分、核心课程、主要实践课程、学制和学位。"
    "用户问专业整体要修多少学分、核心课或实践环节时用它；不要用它查询单门课程详情。"
)


def _sources(data: dict) -> list[Source]:
    raw = data.get("sources") or {}
    if not raw.get("file"):
        return []
    facts = {key: data.get(key) for key in (
        "major", "college", "duration", "degree", "total_credits",
        "total_credits_source", "module_credits", "core_courses", "practice_courses", "study_duration",
    )}
    text = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    return [Source(title=raw["file"], text=text, text_hash=text_hash(text))]


def query_major_requirements_result(major: str) -> ToolResult:
    data = query_major_requirements(major)
    status = ("no_evidence" if data.get("error") else
              "partial_evidence" if data.get("unavailable_fields") else "success")
    return ToolResult(tool="query_major_requirements", ok=True, status=status, data=data,
                      sources=_sources(data))


@tool("query_major_requirements", args_schema=QueryMajorRequirementsArgs,
      description=DESCRIPTION)
def query_major_requirements_tool(major: str) -> ToolResult:
    """Return one major's overall training requirements."""
    return query_major_requirements_result(major)
