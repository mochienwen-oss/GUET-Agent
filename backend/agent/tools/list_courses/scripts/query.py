"""Deterministic filters over the structured training-plan database."""
import re

from agent.tools.query_course.tool import normalize_plan_text, resolve_major


COURSE_LIST_FIELDS = (
    "name", "code", "nature", "credits", "hours", "semester", "required",
    "module", "module_path", "is_core", "is_practice", "src_line",
)


def _semester_matches(value, requested: str) -> bool:
    terms = [part.strip() for part in re.split(r"[,，、/]", str(value or ""))]
    return requested in terms


def _module_matches(course: dict, requested: str) -> bool:
    key = normalize_plan_text(requested)
    values = [course.get("module") or "", *(course.get("module_path") or [])]
    return any(key in normalize_plan_text(value) for value in values)


def list_courses(major: str, *, semester: str | None = None,
                 module: str | None = None, required: bool | None = None,
                 core_only: bool = False) -> dict:
    record = resolve_major(major)
    if record.get("error"):
        return record

    filters = {
        "semester": semester,
        "module": module,
        "required": required,
        "core_only": core_only,
    }
    courses = []
    for course in record["courses"]:
        if semester is not None and not _semester_matches(course.get("semester"), semester):
            continue
        if module is not None and not _module_matches(course, module):
            continue
        if required is not None and course.get("required") is not required:
            continue
        if core_only and not course.get("is_core"):
            continue
        courses.append({field: course.get(field) for field in COURSE_LIST_FIELDS})

    data = {
        "major": record.get("major") or record["file"],
        "college": record.get("college"),
        "filters": filters,
        "count": len(courses),
        "courses": courses,
        "sources": {"file": record["file"]},
    }
    if not courses:
        active = {key: value for key, value in filters.items() if value not in (None, False)}
        data["unresolved"] = "没有符合筛选条件的课程：%s" % active
    return data
