"""Bounded CET row selection within a real, versioned policy table."""
from decimal import Decimal
from types import SimpleNamespace

from agent.core.score_evidence import conversion_rules
from agent.core.task import interpret_task
from agent.tools.search_regulations.scripts.recall.linked_context import restore_headers


def _identity(row):
    keys = ("document_id", "source_sha256", "chunking_version", "parent_id")
    if (row.get("block_type") != "table" or not row.get("chunk_id")
            or row.get("document_id") is None
            or any(not row.get(key) for key in keys[1:])):
        return None
    return tuple(row[key] for key in keys)


def _policy(row):
    title = row.get("source") or ""
    return "大学英语" in title and "成绩认定" in title and "创新创业" not in title


def _matches(row, exam, score):
    rules = conversion_rules({"row": SimpleNamespace(snippet=row.get("text") or "")}, row.get("source_sha256"))
    for rule in rules:
        if rule["exam"] != exam:
            continue
        case = rule["case"]
        low, high = Decimal(case["low"]), Decimal(case["high"])
        if ((score >= low if case["low_inclusive"] else score > low)
                and (score <= high if case["high_inclusive"] else score < high)):
            return True
    return False


def select_target_record(query, ranked, selected, *, top_k, max_chars,
                         fetch_table=None, fetch_related=None, recovery_available=True, task=None):
    task = task or interpret_task(query)
    if task.operation != "score_conversion":
        return selected, None
    exam, score = task.parameter("exam"), Decimal(task.parameter("score"))
    anchors = {_identity(row): row for row in selected if _policy(row) and _identity(row)}
    audit = {"version": "target-record/1", "exam": exam, "score": str(score),
             "status": "missing", "recovery_used": False, "scope": "one_versioned_policy_table"}
    if len(anchors) != 1:
        audit["reason"] = "table_not_unique" if anchors else "table_not_located"
        return selected, audit
    identity, anchor = next(iter(anchors.items()))
    audit["table_identity"] = list(identity)
    header_cache = {}
    def prepare(rows):
        def cached_fetch(seed, selector):
            key = tuple(sorted(selector.items()))
            if key not in header_cache:
                header_cache[key] = fetch_related(seed, selector)
            return header_cache[key]
        return [restore_headers(row, cached_fetch) if fetch_related else row for row in rows
                if _identity(row) == identity and _policy(row)]
    pool = prepare(ranked)
    matched = [row for row in pool if _matches(row, exam, score)]
    if not matched and fetch_table is not None and recovery_available:
        audit["recovery_used"] = True
        try:
            rows = fetch_table(anchor)
        except (InterruptedError, TimeoutError):
            raise
        except Exception as error:
            audit["reason"] = "table_read_failed:" + type(error).__name__
            return selected, audit
        pool = prepare(rows)
        matched = [row for row in pool if _matches(row, exam, score)]
    # Different matching rows are not resolved by the model or a relevance score.
    matched = {row["chunk_id"]: row for row in matched}
    if len(matched) != 1:
        audit["reason"] = "matching_row_ambiguous" if matched else "matching_row_not_obtained"
        return selected, audit
    target = next(iter(matched.values()))
    cost = len(target["text"]) + len(target.get("source") or "") + sum(map(len, target.get("heading_path") or [])) + 32
    if top_k <= 0 or cost > max_chars:
        audit["reason"] = "target_exceeds_budget"
        return selected, audit
    target = dict(target, target_record=True)
    if target["chunk_id"] not in {row.get("chunk_id") for row in ranked}:
        target = dict(target, score=None, rerank_score=None, evidence_relation="target_record")
    result = [target] + [row for row in selected if row.get("chunk_id") != target["chunk_id"]][:max(0, top_k - 1)]
    audit.update(status="obtained", reason="matching_indexed_row", selected_chunk_id=target["chunk_id"])
    return result, audit
