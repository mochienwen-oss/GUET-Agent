"""Phase 5B gate: the rebuilt plan DB remains a compatible superset.

This gate deliberately does not require every old value to stay identical: the
candidate was rebuilt to correct source data and add provenance.  It instead
locks the runtime contract and proves that no legacy course identity vanished.
"""
import json
from collections import Counter
from pathlib import Path

from agent.tools.query_course.scripts import repository
from agent.tools.query_course.scripts.normalize import join_key


BACKEND = Path(__file__).resolve().parents[4]
PROJECT = BACKEND.parent
OLD_DB = PROJECT / "_work" / "plan_db" / "plan_db.pre-phase5b.json"
CANDIDATE_DB = PROJECT / "_work" / "plan_db" / "plan_db.candidate.json"


def _load(path: Path) -> dict:
    assert path.is_file(), "missing Phase 5B artifact: %s" % path
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(major: dict, course: dict) -> tuple[str, str, str]:
    return (
        join_key(major.get("file", "")),
        join_key(course.get("code", "")),
        join_key(course.get("name", "")),
    )


def _course_counter(db: dict) -> Counter:
    return Counter(
        _identity(major, course)
        for major in db["majors"]
        for course in major["courses"]
    )


def test_candidate_keeps_every_legacy_course_identity():
    old = _load(OLD_DB)
    candidate = _load(CANDIDATE_DB)
    old_courses = _course_counter(old)
    candidate_courses = _course_counter(candidate)

    missing = {
        identity: count - candidate_courses[identity]
        for identity, count in old_courses.items()
        if candidate_courses[identity] < count
    }
    assert missing == {}, "candidate removed legacy course identities: %s" % missing
    assert len(candidate["majors"]) == len(old["majors"]) == 63


def test_candidate_keeps_the_runtime_course_contract():
    candidate = _load(CANDIDATE_DB)
    missing = []
    missing_provenance = []
    for major in candidate["majors"]:
        for course in major["courses"]:
            absent = [field for field in repository.COURSE_FIELDS if field not in course]
            if absent:
                missing.append((major.get("file"), course.get("name"), absent))
            source = course.get("src") or {}
            if (not course.get("src_line") or not source.get("file")
                    or not source.get("line") or not source.get("row_hash")):
                missing_provenance.append((major.get("file"), course.get("name")))

    assert missing == []
    assert missing_provenance == []


def test_existing_query_and_compare_paths_can_read_the_candidate(monkeypatch):
    candidate = _load(CANDIDATE_DB)
    monkeypatch.setattr(repository, "_DB", candidate)

    major = next(
        item for item in candidate["majors"]
        if any(course["code"] == "0300295X0" for course in item["courses"])
    )
    recovered = next(course for course in major["courses"] if course["code"] == "0300295X0")
    card = repository.query_course(major.get("major") or major["file"], recovered["name"])
    assert "error" not in card
    assert card["course"]["code"] == "0300295X0"
    assert card["sources"]["file"]
    assert card["sources"]["line"]

    # 课程名必须**唯一命中**：`大学英语` 在每个专业里前缀命中 4 门，
    # 以前是靠 `hits[0]` 静默取首项才「通过」的；Task 4.2 起歧义会明确拒绝。
    compared = repository.compare_course(
        "大学英语1", ["光电信息科学与工程", "环境工程"]
    )
    assert len(compared["rows"]) == 2
    assert all("major" in row and "found" in row for row in compared["rows"])

