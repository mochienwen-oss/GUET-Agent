"""Read major-level requirements from the structured plan database."""
from agent.tools.query_course.tool import resolve_major
import json
from pathlib import Path


def query_major_requirements(major: str) -> dict:
    record = resolve_major(major)
    if record.get("error"):
        return record
    result = {
        "major": record.get("major") or record["file"],
        "college": record.get("college"),
        "duration": record.get("duration"),
        "study_duration": record.get("study_duration"),
        "degree": record.get("degree"),
        "total_credits": record.get("total_credits"),
        "total_credits_source": record.get("total_credits_source"),
        "module_credits": record.get("module_credits") or {},
        "core_courses": record.get("core_courses") or [],
        "practice_courses": record.get("practice_courses") or [],
        "sources": {"file": record["file"]},
    }
    if (record.get("provenance") or {}).get("strategy") == "html_grid/source_cells":
        # The new extraction already contains section-7/9/11 facts. Old
        # annotations were name-bound against the old DB and must not override it.
        result["unavailable_fields"] = [key for key in ("core_courses", "practice_courses")
                                        if not result.get(key)]
        return result
    annotations = Path(__file__).resolve().parents[4] / "data/plan_annotations.json"
    if annotations.exists():
        data = json.loads(annotations.read_text(encoding="utf-8"))
        if data.get("version") != "plan-annotations/1":
            raise ValueError("不支持的培养方案标注版本")
        override = data["majors"].get(record["file"])
        if override:
            from documents.document_availability import require_source
            require_source(record["file"], override["provenance"]["sha256"])
            for key in ("core_courses", "practice_courses", "total_credits", "total_credits_source", "study_duration"):
                if key in override:
                    result[key] = override[key]
            result["annotation_provenance"] = override["provenance"]
            result["list_semantics"] = "第七节原文列示；列表标签不是新增的规范课程实体"
            result["unavailable_fields"] = [key for key in ("core_courses", "practice_courses")
                                            if not result.get(key)]
    return result
