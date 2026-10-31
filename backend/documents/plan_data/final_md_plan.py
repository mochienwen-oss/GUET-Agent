"""Extract plan facts from current structural Markdown, without old row fixes.

HTML spans establish columns and physical course rows. Raw cell identities are
retained; unknown optional values remain null, never copied from an older DB.
"""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from documents.catalog.document_chunks import HTML_TABLE, table_grid
from agent.core.rulings import ruling_for
from .normalize import join_key
from . import plan_source
from .build_plan_db import parse_core, parse_module_credits, split_sections

VERSION = "final-md-plan/2"
FIELD_RULINGS = Path(__file__).resolve().parents[2] / "data/plan_field_rulings.json"
FIELDS = ("课程代码", "课程名称", "课程类别", "课程性质", "学分", "总学时",
          "理论学时", "实验学时", "实践学时", "开课学期", "是否必修", "开课部门")
CODE = re.compile(r"(?:\d{6}|\d{7}X\d)\Z")
SECTIONS = re.compile(r"^##\s+([一二三四五六七八九十]+)、[^\n]*", re.M)


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def sections(text):
    headings = list(SECTIONS.finditer(text))
    return {m[1]: (m.end(), text[m.end():headings[i+1].start() if i+1 < len(headings) else len(text)])
            for i, m in enumerate(headings)}


def column_cells(grid, column, start, stop):
    seen, cells = set(), []
    for row in range(start, stop):
        cell = grid.get((row, column))
        if cell and cell["cell_id"] not in seen:
            seen.add(cell["cell_id"])
            cells.append(cell)
    return cells


def cell_text(cells):
    return "".join(cell["text"] for cell in cells)


def headers(grid, width, stop):
    mapping = {}
    for col in range(width):
        value = compact(cell_text(column_cells(grid, col, 0, stop)))
        for field in FIELDS:
            if value.startswith(field):
                if field in mapping:
                    raise ValueError("表头字段重复：" + field)
                mapping[field] = col
    return mapping


def number(value):
    value = compact(value)
    return float(value) if re.fullmatch(r"\d+(?:\.\d+)?", value) else None


def apply_reviewed_credits(path, source_sha, courses, issues):
    if not FIELD_RULINGS.exists():
        return
    data = json.loads(FIELD_RULINGS.read_text("utf-8"))
    if data.get("version") != "plan-field-rulings/1":
        raise ValueError("不支持的培养方案字段裁决版本")
    seen = set()
    for ruling in data["corrections"]:
        key = (ruling["file"], ruling["course_code"], ruling["field"])
        if key in seen:
            raise ValueError("培养方案字段裁决重复")
        seen.add(key)
        if (ruling["field"] != "credits" or not ruling["reviewed_by"].startswith("human:")
                or type(ruling["value"]) not in (int, float) or not 0 <= ruling["value"] <= 30):
            raise ValueError("未批准或非法的学分裁决")
        if ruling["file"] != path.name or ruling["source_md_sha256"] != source_sha:
            continue
        matches = [course for course in courses if course["code"] == ruling["course_code"]
                   and course["name"] == ruling["course_name"]]
        if len(matches) != 1:
            raise ValueError("学分裁决未唯一绑定当前源文件的课程")
        course = matches[0]
        raw = course["src"]["raw_fields"]["学分"]
        if compact(raw) != ruling["source_value"]:
            raise ValueError("学分源值已变化，不沿用旧裁决")
        course["credits"] = float(ruling["value"])
        course["corrections"].append({"field": "credits", "from": number(raw), "to": ruling["value"],
                                      "authority": "human_review", "by": ruling["reviewed_by"],
                                      "date": ruling["decision_date"], "reason": ruling["reason"],
                                      "source_md_sha256": source_sha})
        reasons = course["confidence"]["reasons"]
        if "credits_unverified" in reasons:
            reasons.remove("credits_unverified")
        course["confidence"]["level"] = "medium" if reasons else "high"
        for issue in issues:
            if (issue.get("course_code") == course["code"] and issue.get("course") == course["name"]
                    and "credits_unverified" in issue.get("fields", [])):
                issue["fields"].remove("credits_unverified")
    issues[:] = [item for item in issues if item.get("code") != "optional_fields_unverified" or item["fields"]]


def course_rows(text, section, issues):
    offset, body = section
    output = []
    for fragment, table in enumerate(HTML_TABLE.finditer(body), 1):
        grid, cells, nr, width, _, flags = table_grid(table[0])
        anchors = [cell for cell in cells if CODE.fullmatch(compact(cell["text"]))]
        if not anchors:
            issues.append({"code": "table_without_course_codes", "table": fragment})
            continue
        first = min(cell["row"] - 1 for cell in anchors)
        mapping = headers(grid, width, first)
        if not set(FIELDS).issubset(mapping):
            raise ValueError("教学表字段不完整：" + repr(sorted(set(FIELDS) - set(mapping))))
        anchors = [cell for cell in anchors if cell["column"] - 1 == mapping["课程代码"]]
        for anchor in anchors:
            start, stop = anchor["row"] - 1, anchor["row"] - 1 + anchor["rowspan"]
            fields = {key: column_cells(grid, col, start, stop) for key, col in mapping.items()}
            raw = {key: cell_text(value) for key, value in fields.items()}
            value = {key: compact(item) for key, item in raw.items()}
            name = value["课程名称"].replace("★", "").replace("▲", "")
            if not name or name in FIELDS or re.match(r"要求(?:学分|子模块数)", name):
                raise ValueError("课程行名称为空或为汇总标签：" + repr(raw))
            credits = number(raw["学分"])
            row_issues = []
            if credits is None or not 0 <= credits <= 30:
                row_issues.append("credits_unverified")
                credits = None
            required = {"必修": True, "是": True, "选修": False, "否": False}.get(value["是否必修"])
            if required is None:
                row_issues.append("required_unverified")
            dept = value["开课部门"]
            if dept and dept != "全校" and not re.search(r"(?:学院|学部|中心|系|部|处)(?:\([^)]*\))?$", dept):
                row_issues.append("department_fragment_unverified")
                dept = None
            modules = []
            seen = set()
            for col in range(mapping["课程代码"]):
                cs = column_cells(grid, col, start, stop)
                ids = tuple(c["cell_id"] for c in cs)
                label = compact(cell_text(cs))
                if label and ids not in seen:
                    seen.add(ids)
                    modules.append(label)
            source_start = offset + table.start()
            source_row = {"table_span": [source_start, offset + table.end()],
                          "table_row": anchor["row"], "code_cell": anchor["cell_id"],
                          "raw_fields": raw,
                          "field_cells": {k: [c["cell_id"] for c in v] for k, v in fields.items()}}
            row_hash = hashlib.sha256(json.dumps(source_row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            line = text.count("\n", 0, source_start) + 1
            # Exact HTML source location rather than a synthetic Markdown row.
            course = {"code": value["课程代码"], "name": name,
                      "module": value["课程类别"] or None, "module_path": modules,
                      "nature": value["课程性质"] or None, "credits": credits,
                      "hours": number(raw["总学时"]), "theory": number(raw["理论学时"]),
                      "lab": number(raw["实验学时"]), "practice": number(raw["实践学时"]),
                      "semester": re.sub(r"\s+", " ", raw["开课学期"]).strip() or None,
                      "required": required, "dept": dept or None,
                      "is_core": "★" in raw["课程名称"], "is_practice": "▲" in raw["课程名称"],
                      "src_line": line, "src": {**source_row, "row_hash": row_hash, "line": line, "fragment": fragment},
                      "extractor": {"name": "final_md_plan", "version": VERSION, "strategy": "html_grid/source_cells"},
                      "corrections": [], "confidence": {"level": "medium" if row_issues else "high", "reasons": row_issues}}
            if row_issues:
                issues.append({"code": "optional_fields_unverified", "course": name, "course_code": course["code"],
                               "table": fragment, "row": anchor["row"], "fields": row_issues})
            output.append(course)
    if not output:
        raise ValueError("教学进程表未抽取到课程")
    return output


def unique_split(value, names):
    """Only a unique full segmentation authorizes course-to-indicator links."""
    target = join_key(value).replace(",", "").replace(";", "")
    keys = {}
    for name in names:
        keys.setdefault(join_key(name), set()).add(name)
    memo = {}
    def solve(pos):
        if pos == len(target):
            return [()]
        if pos in memo:
            return memo[pos]
        found = []
        for key, candidates in keys.items():
            if not key or len(candidates) != 1 or not target.startswith(key, pos):
                continue
            name = next(iter(candidates))
            for tail in solve(pos + len(key)):
                candidate = (name,) + tail
                if candidate not in found:
                    found.append(candidate)
                if len(found) == 2:
                    memo[pos] = found
                    return found
        memo[pos] = found
        return found
    solutions = solve(0)
    return list(solutions[0]) if len(solutions) == 1 else None


def matrix(section, names):
    texts, inverse, unresolved = {}, {}, []
    for table in HTML_TABLE.finditer(section[1]):
        grid, cells, nr, width, _, _ = table_grid(table[0])
        if width != 3:
            raise ValueError("毕业要求矩阵不是三列，不能猜测列归属")
        for cell in cells:
            if cell["column"] != 2:
                continue
            content = compact(cell["text"])
            match = re.match(r"(\d+[.\-]\d+)(.*)", content)
            if not match:
                continue
            iid = match[1].replace("-", ".")
            start, stop = cell["row"] - 1, cell["row"] - 1 + cell["rowspan"]
            support = cell_text(column_cells(grid, 2, start, stop))
            if iid in texts and texts[iid] != match[2]:
                raise ValueError("同一毕业指标具有冲突正文：" + iid)
            texts[iid] = match[2]
            bound = unique_split(support, names)
            if bound is None:
                unresolved.append({"indicator": iid, "raw_support": support, "reason": "not_unique_full_course_segmentation"})
            else:
                for name in bound:
                    inverse.setdefault(name, [])
                    if iid not in inverse[name]:
                        inverse[name].append(iid)
    return texts, inverse, unresolved


def table_credit_total(section):
    totals = set()
    for table in HTML_TABLE.finditer(section[1]):
        grid, cells, nr, width, _, _ = table_grid(table[0])
        columns = []
        for col in range(width):
            label = compact(cell_text(column_cells(grid, col, 0, min(nr, 8))))
            if label.startswith("学分数"):
                columns.append(col)
        if len(columns) != 1:
            continue
        for cell in cells:
            if compact(cell["text"]) == "合计":
                value = number(cell_text(column_cells(
                    grid, columns[0], cell["row"] - 1, cell["row"] - 1 + cell["rowspan"])))
                if value is not None:
                    totals.add(value)
    if len(totals) > 1:
        raise ValueError("统计表存在多个不同的总学分")
    return next(iter(totals)) if totals else None


def build_one(path, active_scope=None):
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    sha = hashlib.sha256(raw).hexdigest()
    sec = sections(text)
    if not {"二", "六", "七", "九", "十"}.issubset(sec):
        raise ValueError("缺少必需章节：" + path.name)
    basic = {compact(k): compact(v) for k, v in re.findall(
        r"^\([一二三四五六七八九十]+\)\s*([^:：\n]+)[:：]([^\n]+)", sec["二"][1], re.M)}
    match = re.fullmatch(r"(20\d{2})级(.+?)(?:专业)?培养方案(?:\(主修\))?", path.stem.strip())
    if not match or not basic.get("专业代码") or not basic.get("专业名称"):
        raise ValueError("专业身份缺失：" + path.name)
    issues = []
    major_name = compact(match[2])
    basic_name = re.sub(r"专业$", "", basic["专业名称"])
    if major_name != basic_name:
        if not (major_name.startswith(basic_name) or basic_name.startswith(major_name)):
            raise ValueError("文件名与正文专业名称冲突：" + path.name)
        issues.append({"code": "major_label_variant", "title_name": major_name, "basic_name": basic["专业名称"],
                       "basis": "filename_title_identity; original basic field retained"})
    courses = course_rows(text, sec["十"], issues)
    apply_reviewed_credits(path, sha, courses, issues)
    legacy_sections = split_sections(text.splitlines())
    core, practice = parse_core(legacy_sections["七"])
    modules, minimum = parse_module_credits(legacy_sections["九"], lambda c, m: issues.append({"code": c, "detail": m}))
    names = {course["name"] for course in courses}
    def confirmed(labels):
        result = set()
        for label in labels:
            candidates = {name for name in names if join_key(name) == join_key(label)}
            if not candidates:
                candidates = {name for name in names if join_key(name).startswith(join_key(label))}
            if len(candidates) == 1:
                result.update(candidates)
        return result
    core_set, practice_set = confirmed(core), confirmed(practice)
    indicators, inverse, unresolved = matrix(sec["六"], names)
    for course in courses:
        course["is_core"] |= course["name"] in core_set
        course["is_practice"] |= course["name"] in practice_set
        course["indicators"] = inverse.get(course["name"], [])
        course["src"]["file"] = path.name
    years = re.search(r"修业年限[:：]\s*([^\n。]+)", sec["二"][1])
    decision = ruling_for(path.stem.strip(), sha)
    total = decision["value"] if decision else minimum
    table_total = table_credit_total(sec["十一"]) if "十一" in sec else None
    total_source = "human_review:20261020:title_table" if decision else "section9_minimum"
    prior_decision = ruling_for(path.stem.strip())
    if decision is None and prior_decision and table_total == prior_decision["value"]:
        # The changed source has its own explicit table value. Do not rewrite or
        # pretend to reapprove the old SHA-bound ruling sidecar.
        total, total_source = table_total, "section11_table_total"
    if total is None and table_total is not None:
        total, total_source = table_total, "section11_table_total"
    if total is None:
        raise ValueError("缺少可追溯的毕业学分要求：" + path.name)
    college = (active_scope or {}).get("college")
    return {"file": path.name, "major": major_name, "code": basic["专业代码"],
            "grade": match[1], "level": "本科" if "学士" in basic.get("授予学位", "") else None,
            "degree_level": "学士" if "学士" in basic.get("授予学位", "") else None,
            "college": college, "college_source": "active_scope_metadata" if college else None,
            "degree": basic.get("授予学位"), "duration": basic.get("学制"),
            "study_duration": compact(years[1]) if years else None, "major_field": basic.get("所属专业类"),
            "basic": basic, "total_credits": total,
            "total_credits_source": total_source,
            "module_credits": modules, "core_courses": core, "practice_courses": practice,
            "courses": courses, "indicator_text": indicators, "unmatched_courses": unresolved,
            "credits": {"total_declared": total, "section9_total": minimum, "table11_total": table_total,
                        "modules_sum": sum(modules.values()),
                        "rule": "preserve_source_minimum_and_explicit_owner_ruling"},
            "quality": {"status": "needs_review" if issues or unresolved else "ok", "hard_failures": [],
                        "warnings": issues, "review_items": issues},
            "provenance": {"file": path.name, "sha256": sha, "estate8": "unavailable_by_decision",
                           "strategy": "html_grid/source_cells", "source_root": "final-md/培养方案",
                           "extractor_version": VERSION}}


def build(root, active):
    scope_by_title = {row["title"]: row.get("scope") or {} for row in active["documents"]}
    files = sorted(Path(root).glob("*.md"))
    majors = [build_one(path, scope_by_title.get(path.stem.strip())) for path in files]
    return assemble(root, majors)


def assemble(root, majors):
    return {"meta": {"source_dir": "final-md/培养方案", "docs": len(majors), "schema_version": "plan_db/v2",
                     "extractor_version": VERSION, "source_fingerprint": plan_source.source_fingerprint(root),
                     "field_rulings_sha256": hashlib.sha256(FIELD_RULINGS.read_bytes()).hexdigest() if FIELD_RULINGS.exists() else None,
                     "note": "Course facts re-extracted from current final-md; college labels use existing active scope metadata."},
            "majors": majors}
