# -*- coding: utf-8 -*-
"""培养方案工具：课程定位卡 / 跨专业对比。

**纯函数、确定性、不碰 LLM。** 这是本项目最贵的一课落成的规矩 ——
学分、学时、指标点全部由这里算出来，模型只负责选工具、传参数、翻人话。
（事故背景：模型心算把「3 门」判成「没到黄线」。）

数据源：`backend/data/plan_db.json`（离线批处理产物，运行时只读）。
"""
import difflib
import json
import os
from pathlib import Path

from .normalize import join_key

DB_PATH = str(Path(__file__).resolve().parents[4] / "data" / "plan_db.json")
_DB = None

COURSE_FIELDS = ("name", "code", "nature", "credits", "hours", "theory", "lab",
                 "practice", "semester", "required", "dept", "module", "module_path",
                 "is_core", "is_practice")


def _majors():
    global _DB
    if _DB is None:
        with open(DB_PATH, encoding="utf-8") as f:
            _DB = json.load(f)
    return _DB["majors"]


def _major_name(m):
    return m.get("major") or m["file"]


def _nearest(query, names, n=3):
    """编辑距离最近的候选。`SequenceMatcher` 按字符算，中文可用。"""
    return difflib.get_close_matches(query, names, n=n, cutoff=0.3)


class AmbiguousLookupError(LookupError):
    """命中**多个**候选 —— 判为歧义，**绝不取第一项**。

    ★ 为什么用异常而不是返回 `{"error": ...}`：`query_course_result` 把
      `data["error"]` 映射成 `no_evidence`，而「歧义」**不是**「学校没有规定」。
      异常经 Registry 的既有边界转成 `tool_failed`（`ok=False`），
      **不新增 ToolStatus、不改 Registry**。

    ★ 正常路径上到不了这里：Task 4.3 的图级 `validate_calls` 会在**调用工具之前**
      用 `entity_calls.validate_call` 拦下未唯一确认的提及；本异常是**绕过图的兜底守卫**
      —— 谁直接调工具，谁就必须先给出唯一实体。
    """

    def __init__(self, field, query, candidates):
        self.field = field
        self.query = query
        self.candidates = list(candidates)
        shown = "、".join(self.candidates[:5])
        if len(set(self.candidates)) < len(self.candidates):
            shown += "（含同一专业内的同名重复记录）"
        super().__init__(
            "%s 参数 %r 命中 %d 个候选：%s —— 判为歧义，绝不取第一项；"
            "请给出完整且唯一的名称。" % (field, query, len(self.candidates), shown))


def pick_major(matches, query):
    """**恰 1 个**才返回；**多个**抛歧义。0 个由调用方先行处理。"""
    if len(matches) == 1:
        require_plan_source(matches[0])
        return matches[0]
    raise AmbiguousLookupError("major", query, [_major_name(m) for m in matches])


def require_plan_source(record):
    from documents.document_availability import require_source, SourceUnavailable
    source_sha = (record.get("provenance") or {}).get("sha256")
    if not source_sha:
        raise SourceUnavailable("结构化培养方案缺少可核验的来源身份")
    require_source(record["file"], source_sha)


def pick_course(hits, query):
    """**恰 1 个**才返回；**多个**（含同专业内同名多实例）抛歧义。"""
    if len(hits) == 1:
        return hits[0]
    raise AmbiguousLookupError("course", query, [c["name"] for c in hits])


def find_major(name):
    """专业模糊匹配。返回 (命中列表, 未命中时的候选)。"""
    k = join_key(name)
    ms = _majors()
    if k:
        exact = [m for m in ms if join_key(_major_name(m)) == k]
        if exact:
            return exact, []
        hits = [m for m in ms if k in join_key(_major_name(m))]
        if hits:
            return hits, []
    return [], _nearest(name, [_major_name(m) for m in ms])


def match_course(major, course):
    """课程模糊匹配。返回 (命中列表已排序, 匹配方式)。

    顺序：精确 → 前缀（**后缀最短优先**：`数据库系统原理` 先落 `…A` 而不是 `…实验`）
    → 子串 → 空。后两类命中数 >1 时，调用方应把 `alternatives` 一并给模型，好让它反问。
    """
    k = join_key(course)
    cs = major["courses"]
    if not k:
        return [], "none"
    for how, pred in (("exact", lambda c: join_key(c["name"]) == k),
                      ("prefix", lambda c: join_key(c["name"]).startswith(k)),
                      ("substring", lambda c: k in join_key(c["name"]))):
        hits = [c for c in cs if pred(c)]
        if hits:
            hits.sort(key=lambda c: len(c["name"]))          # 后缀最短优先
            return hits, how
    return [], "none"


def _card(major, course, how, hits):
    return {
        "major": _major_name(major),
        "college": major.get("college"),
        "duration": major.get("duration"),
        "degree": major.get("degree"),
        "total_credits": major.get("total_credits"),
        "module_credits": major.get("module_credits"),
        "course": {k: course.get(k) for k in COURSE_FIELDS},
        "indicators": [{"id": i, "text": major["indicator_text"].get(i, "")}
                       for i in course.get("indicators", [])],
        "match": how,
        "alternatives": [c["name"] for c in hits[1:5]],
        "sources": {"file": major["file"], "line": course.get("src_line")},
    }


def query_course(major, course):
    """课程定位卡。**找不到就返回候选，不返回空** —— 反问是本项目辅导员形态的核心。"""
    ms, near = find_major(major)
    if not ms:
        return {"error": "major_not_found", "query": major, "candidates": near}
    m = pick_major(ms, major)
    hits, how = match_course(m, course)
    if not hits:
        return {"error": "course_not_found", "major": _major_name(m), "query": course,
                "candidates": _nearest(course, [c["name"] for c in m["courses"]])}
    return _card(m, pick_course(hits, course), how, hits)


def compare_course(course, majors=None):
    """跨专业对比。**没开这门课的专业也要出一行标「未开设」，不静默丢行** ——
    丢行会让「这门课只有 3 个专业有」被误读成「5 个专业都一样」。"""
    if majors:
        rows, unresolved = [], []
        for name in majors:
            ms, near = find_major(name)
            if not ms:
                unresolved.append({"major": name, "candidates": near})
                continue
            rows.append(_row(pick_major(ms, name), course))
    else:
        rows = [r for r in (_row(m, course) for m in _majors()) if r.get("found")]
        unresolved = []
    hits = [r for r in rows if r.get("found")]
    out = {"course_query": course, "found_in": len(hits), "rows": rows,
           "sources": [r["sources"] for r in hits]}
    if unresolved:
        out["unresolved_majors"] = unresolved
    if not hits:
        out["error"] = "course_not_found"
    return out


def _row(major, course):
    """一个专业一行。未开设也保留这一行。"""
    require_plan_source(major)
    hits, how = match_course(major, course)
    if not hits:
        return {"major": _major_name(major), "college": major.get("college"), "found": False}
    c = pick_course(hits, course)
    return {"major": _major_name(major), "college": major.get("college"), "found": True,
            "course": {k: c.get(k) for k in ("name", "credits", "hours", "semester",
                                             "nature", "module", "is_core")},
            "match": how, "sources": {"file": major["file"], "line": c.get("src_line")}}
