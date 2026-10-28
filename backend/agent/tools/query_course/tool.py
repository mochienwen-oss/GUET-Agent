"""Public query_course capability; internal repository code stays under scripts/."""
import json

from langchain_core.tools import tool

from agent.core import Source, ToolResult
from agent.core.evidence import text_hash

from .schemas import QueryCourseArgs
from .scripts import repository


# ★ 冻结：逐字等于 LoRA 训练列里的 description（与 schemas.py 的 PARAMETERS 同一份来源）。
DESCRIPTION = (
    "查询**某一个专业**里某门课程的定位：学分、学时、理论/实验学时、开课学期、"
    "所属模块、课程性质、支撑哪些毕业要求指标点。问句里**只涉及一个专业**时用它。"
)


def resolve_major(major: str) -> dict:
    """Public read-only major lookup used by sibling structured-query tools.

    ★ 命中多个专业时抛 `repository.AmbiguousLookupError`，**不取第一项**。
    """
    matches, candidates = repository.find_major(major)
    if not matches:
        return {"error": "major_not_found", "query": major, "candidates": candidates}
    return repository.pick_major(matches, major)


def normalize_plan_text(value: str) -> str:
    """Expose the plan database's matching normalization without a scripts import."""
    return repository.join_key(value)


def _source(data: dict) -> list[Source]:
    raw = data.get("sources") or {}
    if not raw.get("file"):
        return []
    facts = {key: data.get(key) for key in ("major", "college", "course", "indicators")}
    text = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    return [Source(title=raw["file"], line=raw.get("line"), text=text,
                   text_hash=text_hash(text))]


def query_course_result(major: str, course: str) -> ToolResult:
    data = repository.query_course(major, course)
    status = "no_evidence" if data.get("error") else "success"
    return ToolResult(tool="query_course", ok=True, status=status, data=data,
                      sources=_source(data))


@tool("query_course", args_schema=QueryCourseArgs, description=DESCRIPTION)
def query_course_tool(major: str, course: str) -> ToolResult:
    """Return the structured course-position card for one major."""
    return query_course_result(major, course)
