"""Explicit request/document scope; unknown metadata never proves a match."""
import re
import json
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, field

REFERENCE_DESCRIPTION = r"介绍|说明|解释|概述|查询|列出"


@dataclass(frozen=True)
class RequestScope:
    level: str | None = None
    cohort: str | None = None
    majors: tuple[str, ...] = ()
    college: str | None = None
    subject: str | None = None
    provenance: dict = field(default_factory=dict)
    reference_cohorts: tuple[str, ...] = ()
    reference_levels: tuple[str, ...] = ()


def level_roles(text):
    """Separate explicit self-identification from the level of cited material."""
    marker = r"本科(?:生)?|研究生|硕士(?:研究生)?|博士(?:研究生)?"
    def name(value):
        return "undergraduate" if value.startswith("本科") else "graduate"
    correction = re.search(r"(?:我|本人)?不是(?:" + marker + r")[，,\s]*(?:而)?是(" + marker + r")", text)
    identities = {name(m[1]) for m in re.finditer(
        r"(?:(?:我|本人)(?:是|为|属于)|我也是|[，,]也是)\s*(?:一名|20\d{2}级)?(" + marker + r")", text)}
    if correction:
        identities = {name(correction[1])}
    references = set()
    for match in re.finditer(marker, text):
        tail = re.split(r"[，,。；;？?]", text[match.end():])[0][:24]
        if not re.search(marker, tail) and re.search(r"方案|材料|规定|文件|手册", tail):
            references.add(name(match[0]))
    all_levels = {name(m[0]) for m in re.finditer(marker, text)}
    if not identities and len(all_levels) > 1 and re.search(r"方案|材料|规定|文件|手册", text):
        return set(), all_levels, "current_user_reference"
    return (identities or (all_levels - references), references,
            "current_user_identity" if identities else "current_user")


def cohort_roles(text):
    """Separate bounded explicit identity/correction from cited plan years."""
    years = set(re.findall(r"(20\d{2})\s*级", text))
    correction = re.search(r"不是\s*20\d{2}\s*级[，,\s]*(?:而)?是\s*(20\d{2})\s*级", text)
    if correction:
        return {correction[1]}, set(), "current_user_identity"
    identities = set(re.findall(r"(?:我(?:是|为|属于)?|本人(?:是|为)?|更正为|改为)\s*(20\d{2})\s*级", text))
    identities.update(re.findall(r"(?:我也是|[，,]也是)\s*(20\d{2})\s*级(?:学生|本科生|研究生)", text))
    references = set(re.findall(r"(?:按(?:照)?|参照|依据|参考|套用|采用|使用|" + REFERENCE_DESCRIPTION +
                               r")\s*(20\d{2})\s*级[^。？?；;]{0,24}(?:方案|规定|政策)", text))
    if identities:
        return identities, references, "current_user_identity"
    if len(years) > 1 and re.search(r"区别|对比|比较|不同|一样|和|与", text):
        return set(), years, "current_user_reference"
    return years - references, references, "current_user"


@lru_cache(maxsize=1)
def _colleges():
    path = Path(__file__).resolve().parents[2] / "data/plan_db.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(sorted({x["college"] for x in data["majors"] if x.get("college")}))


def request_scope(question: str, *, resolution=None, history=()) -> RequestScope:
    """Owner default: undergraduate student; explicit users override defaults.

    Only anaphoric follow-ups inherit user scopes. Assistant prose never supplies
    identity. Ambiguous explicit scopes remain unknown, not silently defaulted.
    """
    def followup(text):
        return bool(re.match(r"^(那|它|这个|该专业|刚才|上面|继续)", text.strip()))

    def explicit(text):
        values, ambiguous = {}, set()
        levels, _, _ = level_roles(text)
        if len(levels) == 1:
            values["level"] = levels.pop()
        elif levels:
            ambiguous.add("level")
        years, _, _ = cohort_roles(text)
        if len(years) == 1:
            values["cohort"] = years.pop()
        elif years:
            ambiguous.add("cohort")
        colleges = {name for name in _colleges() if name in text}
        if len(colleges) == 1:
            values["college"] = colleges.pop()
        elif colleges:
            ambiguous.add("college")
        staff = bool(re.search(r"教职工|职工|教师(?:进修|培训|职称)|我(?:是|作为)(?:一名)?(?:教师|老师)", text))
        student = bool(re.search(r"本科|研究生|学生|新生|硕士|博士", text))
        if re.search(r"全体师生|全校师生|教职工和学生", text):
            values["subject"] = "all"
        elif staff and student:
            ambiguous.add("subject")
        elif staff or student:
            values["subject"] = "staff" if staff else "student"
        return values, ambiguous

    values, ambiguous = explicit(question)
    provenance = {key: "current_user" for key in values}
    provenance.update({key: "current_user_ambiguous" for key in ambiguous})
    _, references, cohort_origin = cohort_roles(question)
    _, reference_levels, level_origin = level_roles(question)
    if values.get("level") and level_origin == "current_user_identity":
        provenance["level"] = level_origin
    elif level_origin == "current_user_reference":
        provenance["level"] = level_origin
    if values.get("cohort") and cohort_origin == "current_user_identity":
        provenance["cohort"] = cohort_origin
    if followup(question):
        for row in reversed(history or []):
            if row.get("role") != "user":
                continue
            text = row.get("content") or ""
            prior, prior_ambiguous = explicit(text)
            if (provenance.get("subject") == "current_user" and prior.get("subject")
                    and values["subject"] != prior["subject"]):
                break
            for key in ("level", "cohort", "college", "subject"):
                if key in values or key in ambiguous:
                    continue
                if key == "level" and values.get("subject") in {"staff", "all"}:
                    continue
                if key == "subject" and "level" in values:
                    continue
                if key in prior:
                    values[key], provenance[key] = prior[key], "prior_user"
                elif key in prior_ambiguous:
                    ambiguous.add(key)
                    provenance[key] = "prior_user_ambiguous"
            if not followup(text):
                break
    if "subject" not in values and "subject" not in ambiguous:
        values["subject"], provenance["subject"] = "student", "owner_default"
    if ("level" not in values and "level" not in ambiguous and values.get("subject") == "student"
            and level_origin != "current_user_reference"):
        values["level"], provenance["level"] = "undergraduate", "owner_default"
    majors = tuple(sorted({m.selected.canonical_name for m in
                           getattr(resolution, "mentions", ())
                           if m.selected is not None and m.selected.entity_type == "major"}))
    if majors:
        provenance["majors"] = "catalog_resolution"
    return RequestScope(values.get("level"), values.get("cohort"), majors,
                        college=values.get("college"), subject=values.get("subject"),
                        provenance=provenance, reference_cohorts=tuple(sorted(references)),
                        reference_levels=tuple(sorted(reference_levels)))


def document_scope(title: str, *, metadata=None) -> dict:
    """Prefer ingested metadata; only explicit title markers are fallbacks."""
    out = dict(metadata or {})
    if "本科" in title:
        out.setdefault("level", "undergraduate")
    elif re.search(r"研究生|硕士|博士", title):
        out.setdefault("level", "graduate")
    match = re.search(r"(20\d{2})级(.+?)专业培养方案", title)
    if match:
        out.setdefault("cohort", match.group(1))
        out.setdefault("major", match.group(2))
    college = re.search(r"([\u4e00-\u9fff]{2,16}学院)本科生", title)
    if college:
        out.setdefault("college", college.group(1))
    if re.search(r"教职工|教师|职工", title) and "学生" not in title:
        out.setdefault("subject", "staff")
    elif re.search(r"本科生|研究生|学生", title):
        out.setdefault("subject", "student")
    regions = _exchange_regions(title)
    if len(regions) == 1:
        out.setdefault('exchange_region', next(iter(regions)))
    return out


def scope_conflict(request: RequestScope, document: dict) -> str | None:
    for key in ("level", "cohort", "college", "subject"):
        wanted, actual = getattr(request, key), document.get(key)
        if key == "subject" and "all" in (wanted, actual):
            continue
        if wanted and actual and wanted != actual:
            return "scope_mismatch:" + key
    if len(request.majors) == 1 and document.get("major") and document["major"] != request.majors[0]:
        return "scope_mismatch:major"
    return None


def scoring_domains(text: str) -> set[str]:
    """Explicit scoring systems only; this is not general semantic grading."""
    found = set()
    if re.search(r"学分绩|学位", text):
        found.add("degree")
    if re.search(r"综合素质测评|综合测评|综测", text):
        found.add("comprehensive_assessment")
    if re.search(r"拓展(?:课程|学分)|创新创业(?:拓展|学分)", text):
        found.add("innovation_course_credit")
    return found


def scoring_scope_conflict(question: str, title: str, history=()) -> str | None:
    wanted = scoring_domains(question)
    if not wanted and re.match(r"^(那|它|这个|该|刚才|上面|继续)", question.strip()):
        for row in reversed(history or []):
            if row.get("role") == "user":
                wanted = scoring_domains(row.get("content") or "")
                break
    actual = set()
    if "学士学位授予" in title:
        actual.add("degree")
    if re.search(r"综合素质测评|综合测评", title):
        actual.add("comprehensive_assessment")
    if "创新创业拓展课程" in title:
        actual.add("innovation_course_credit")
    if wanted and actual and wanted.isdisjoint(actual):
        return "scope_mismatch:scoring_system"
    return None


def _exchange_regions(text: str) -> set[str]:
    text = re.sub(r'[()（）\s]', '', text)
    if not re.search(r'交流|交换', text):
        return set()
    return {region for region, pattern in (
        ('overseas', r'国境外|境外|国外|出国'), ('domestic', r'国内'))
        if re.search(pattern, text)}


def _transfer_topics(text: str) -> set[str]:
    return {topic for topic, pattern in (('transfer_school', r'转学(?!分)'),
                                         ('transfer_major', r'转专业'))
            if re.search(pattern, text)}


def _requested_transfers(question, history):
    wanted = _transfer_topics(question)
    if not wanted and re.match(r'^(那|它|这个|该|刚才|上面|继续)', question.strip()):
        for row in reversed(history or []):
            if row.get('role') == 'user':
                return _transfer_topics(row.get('content') or '')
    return wanted


def _document_transfers(title, heading_path):
    # A specific section may narrow a document covering both businesses.
    for heading in reversed(heading_path or []):
        topics = _transfer_topics(heading)
        if topics:
            return topics
    return _transfer_topics(title)


def business_scope_conflict(question: str, title: str, history=(), *, heading_path=()) -> str | None:
    """Reject explicit business conflicts, not unknown or general provisions."""
    wanted = _exchange_regions(question)
    if not wanted and re.match(r'^(那|它|这个|该|刚才|上面|继续)', question.strip()):
        for row in reversed(history or []):
            if row.get('role') == 'user':
                wanted = _exchange_regions(row.get('content') or '')
                break
    actual = _exchange_regions(title)
    if len(wanted) == 1 and actual and wanted.isdisjoint(actual):
        return 'scope_mismatch:exchange_region'
    wanted = _requested_transfers(question, history)
    actual = _document_transfers(title, heading_path)
    if len(wanted) == len(actual) == 1 and wanted.isdisjoint(actual):
        return 'scope_mismatch:transfer_business'
    return None


def evidence_use_role(question: str, title: str, heading_path=(), history=()) -> tuple[str, str]:
    """Bounded explicit matches; a retrieved hit is not automatically a basis."""
    wanted = _requested_transfers(question, history)
    actual = _document_transfers(title, heading_path)
    if wanted:
        if len(actual) == 1 and actual <= wanted:
            return 'primary', 'explicit_transfer_business'
        return 'background', 'transfer_business_unconfirmed'
    if (not scoring_domains(question) and re.search(r'课程|必修|选修', question)
            and re.search(r'认定|替代|转换', question)):
        if '课程认定' in title:
            return 'primary', 'explicit_course_recognition_document'
        if '培养方案' in title:
            return 'background', 'plan_not_general_recognition_rule'
    return 'candidate', 'retrieval_candidate_not_confirmed_basis'


PLAN_TOOLS = frozenset({"query_course", "query_major_requirements", "list_courses", "compare_course"})


def tool_scope_conflict(request: RequestScope, tool: str) -> str | None:
    # Current catalog/plan_db is explicitly an undergraduate training-plan dataset.
    if tool in PLAN_TOOLS and request.level == "graduate":
        return "scope_mismatch:level"
    return None
