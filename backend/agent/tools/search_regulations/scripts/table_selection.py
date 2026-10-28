"""Bounded selection groups, not fabricated merged chunks or whole-table recall."""
import math
import re
import unicodedata

from .rerank_scores import SCORE_VERSION

_ENUMERATION = re.compile(r"哪些|哪几|分别|列出|全部|所有")
_GENERIC_VALUES = {"课程", "课程名称", "支撑课程", "备注", "类别", "项目", "合计", "小计", "必修", "选修"}


def _norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _key(row):
    return row.get("document_id"), row.get("chunk_idx")


def _identity(row):
    return row["document_id"], row["source_sha256"], row["parent_id"]


def _subjects(row, query, min_score):
    if (row.get("block_type") != "table" or not row.get("chunking_version")
            or row.get("document_id") is None or row.get("chunk_idx") is None
            or not row.get("source_sha256") or not row.get("parent_id")
            or not isinstance(row.get("row_number"), int)
            or not row.get("table_fields") or row.get("rerank_score_version") != SCORE_VERSION):
        return set()
    score = row.get("rerank_score")
    if not isinstance(score, (int, float)) or not math.isfinite(score) or score < min_score:
        return set()
    terms = set()
    for field in row["table_fields"]:
        for line in field.get("text", "").splitlines():
            value = _norm(line)
            if (2 <= len(value) <= 80 and value not in _GENERIC_VALUES
                    and re.search(r"[一-鿿a-zA-Z]", value) and value in query):
                terms.add(value)
    return terms


def select_table_rows(query, ranked, baseline, *, top_k, per_doc, max_chars, min_score):
    """Count exact-subject, high-score rows of one physical table as one slot.

    Only already-reranked rows can enter. Document/hash/table boundaries remain
    separate; original row objects, scores, text and source identities survive.
    Completeness telemetry covers this candidate pool, not the entire corpus.
    """
    if not _ENUMERATION.search(query) or not baseline or top_k <= 0 or max_chars <= 0:
        return baseline, None
    query = _norm(query)
    matches = {_key(r): _subjects(r, query, min_score) for r in ranked}
    # A shorter name inside a longer source value must not be a second subject.
    vocabulary = set().union(*matches.values()) if matches else set()
    longest = {v for v in vocabulary if not any(v != other and v in other for other in vocabulary)}
    matches = {key: terms & longest for key, terms in matches.items()}
    anchors = {}
    for row in baseline:
        if matches.get(_key(row)):
            doc = _identity(row)[:2]
            anchors.setdefault(doc, set()).update(matches[_key(row)])
    groups = {}
    for row in ranked:
        if not matches[_key(row)]:
            continue
        identity = _identity(row)
        if matches[_key(row)] & anchors.get(identity[:2], set()):
            groups.setdefault(identity, []).append(row)
    if sum(map(len, groups.values())) < 2:
        return baseline, None

    selected, used, counts, seen, audits = [], 0, {}, set(), []

    def append(row):
        nonlocal used
        if _key(row) in seen:
            return True
        cost = len(row.get("text", "")) + len(row.get("source", "")) + sum(map(len, row.get("heading_path") or [])) + 32
        if len(selected) >= top_k or used + cost > max_chars:
            return False
        selected.append(row)
        used += cost
        seen.add(_key(row))
        return True

    for identity, members in groups.items():
        # groups retain the best member's rerank order; rows retain source order
        # whenever the full group fits. If bounded, preserve its best member first.
        doc = identity[0]
        enough_rows = len(selected) + len(members) <= top_k
        ordered = sorted(members, key=lambda r: (r["row_number"], r["chunk_idx"])) if enough_rows else [members[0]] + sorted(members[1:], key=lambda r: r["row_number"])
        group_cost = sum(len(r["text"]) + len(r.get("source", "")) + sum(map(len, r.get("heading_path") or [])) + 32 for r in members)
        if used + group_cost > max_chars:
            ordered = [members[0]] + [r for r in ordered if r is not members[0]]
        allowed = per_doc <= 0 or counts.get(doc, 0) < per_doc
        added = []
        if allowed:
            for row in ordered:
                if append(row):
                    added.append(_key(row))
            if added:
                counts[doc] = counts.get(doc, 0) + 1
        audits.append({"source_identity": list(identity), "candidate_chunk_ids": [list(_key(r)) for r in members],
                       "selected_chunk_ids": [list(k) for k in added], "document_limit_blocked": not allowed})

    grouped_keys = {_key(r) for members in groups.values() for r in members}
    for row in baseline:
        if _key(row) in grouped_keys:
            continue
        doc = row.get("document_id")
        if per_doc <= 0 or counts.get(doc, 0) < per_doc:
            if append(row):
                counts[doc] = counts.get(doc, 0) + 1
    omitted = [list(_key(r)) for members in groups.values() for r in members if _key(r) not in seen]
    if not selected:
        raise ValueError("完整表格证据行超出返回预算，未截断或伪造空检索结果")
    return selected, {"version": "table-selection/1", "scope": "qualified_rerank_candidates",
                      "subjects": sorted(set().union(*anchors.values())), "groups": audits,
                      "omitted_chunk_ids": omitted, "used_chars": used, "max_chars": max_chars,
                      "max_rows": top_k, "per_document_group_limit": per_doc}
