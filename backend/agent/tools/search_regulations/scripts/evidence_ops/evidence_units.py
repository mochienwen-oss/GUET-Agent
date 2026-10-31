"""Bounded context around selected chunks, never fabricated merged evidence.

Every returned row retains its own text, document/hash/chunk identity and score.
Related rows are context, not independently reranked hits or proof of an answer.
"""
import re

from agent.core.request_control import check_cancelled
from agent.tools.search_regulations.scripts.recall.linked_context import restore_headers, links_for, resolve_link

VERSION = "evidence-units/1"


def _key(row):
    return row.get("document_id"), row.get("chunk_idx")


def _cost(row):
    return (len(row.get("text") or "") + len(row.get("source") or "")
            + sum(map(len, row.get("heading_path") or [])) + 32)


def _kind(row):
    if row.get("block_type") == "table":
        return "table"
    if row.get("block_type") == "article":
        return "article"
    if any(re.match(r"^第[一二三四五六七八九十百零〇\d]+条", part.strip())
           for part in row.get("heading_path") or []):
        return "article"
    return "section"


def _eligible(row):
    return (row.get("document_id") is not None and row.get("chunk_idx") is not None
            and isinstance(row.get("text"), str) and bool(row["text"].strip())
            and all(row.get(k) for k in ("chunk_id", "source_sha256", "chunking_version",
                                         "parent_id", "section_parent_id")))


def reassemble(seeds, fetch_related, *, max_chars, max_unit_chars=4000, fetch_document=None):
    """Add a table's first-row candidate or one prev/next of the same section.

    fetch_related(seed, selector) returns a list of actual payload-derived rows.
    Missing/ambiguous links and budgets are observable, never silent truncation.
    Missing structure metadata leaves legacy chunks unchanged without I/O.
    """
    unique = {}
    for index, row in enumerate(seeds):
        if _eligible(row):
            try:
                row = restore_headers(row, fetch_related)
            except InterruptedError:
                raise
            except Exception as error:
                row = dict(row, review_flags=list(row.get('review_flags') or []) +
                           ['表头未确认：' + type(error).__name__])
        key = _key(row) if None not in _key(row) else ("unstructured", index)
        if key in unique and unique[key].get("text") != row.get("text"):
            raise ValueError("同一证据块身份出现不同正文")
        unique.setdefault(key, row)
    selected, used, omitted = {}, 0, []
    for key, row in unique.items():
        cost = _cost(row)
        if used + cost > max_chars:
            omitted.append(row.get("chunk_id") or list(key))
            continue
        selected[key] = row
        used += cost
    if unique and not selected:
        raise ValueError("完整命中证据超过上下文预算，未截断或伪造空结果")
    unique = dict(selected)
    if not any(_eligible(row) for row in unique.values()):
        return list(selected.values()), ({"version": VERSION, "groups": [],
                "used_chars": used, "max_chars": max_chars,
                "max_unit_chars": max_unit_chars, "omitted_chunk_ids": omitted,
                "incomplete": True} if omitted else None)
    groups, cache = {}, {}
    documents = {}
    for seed in unique.values():
        if not _eligible(seed):
            continue
        identity = tuple(seed[k] for k in ("document_id", "source_sha256", "parent_id"))
        group = groups.setdefault(identity, {"identity": list(identity), "kind": _kind(seed),
                                            "seed_chunk_ids": [], "added_chunk_ids": [],
                                            "warnings": [], "boundary_skips": []})
        group["seed_chunk_ids"].append(seed["chunk_id"])
        for link in links_for(seed):
            requirement = None
            if link.get('required_for'):
                requirement = {key: link[key] for key in ('id', 'from', 'to', 'quote', 'relation', 'required_for')}
                requirement.update(from_chunk_id=seed['chunk_id'], source_sha256=seed['source_sha256'],
                    reviewed_source_sha256=link.get('source_sha256'), status='pending')
                group.setdefault('context_requirements', []).append(requirement)
                if link.get('source_sha256') != seed['source_sha256']:
                    requirement['status'] = 'rule_version_mismatch'
                    group['warnings'].append('rule_version_mismatch:' + link['relation'])
                    continue
            elif fetch_document is None:
                continue
            check_cancelled()
            doc_key = seed['document_id'], seed['source_sha256']
            matches = resolve_link(seed, link, list(selected.values())) if requirement is not None else []
            if not matches and fetch_document is None:
                requirement['status'] = 'reader_unavailable'
                group['warnings'].append('relation_reader_unavailable:' + link['relation'])
                continue
            if not matches and doc_key not in documents:
                try:
                    documents[doc_key] = fetch_document(seed)
                except InterruptedError:
                    raise
                except Exception as error:
                    documents[doc_key] = []
                    group['warnings'].append('relation_read_failed:' + type(error).__name__)
            if not matches:
                matches = resolve_link(seed, link, documents[doc_key])
            relation = link['relation']
            if len(matches) != 1 or not _eligible(matches[0]):
                if requirement is not None:
                    requirement['status'] = 'missing_or_ambiguous'
                group['warnings'].append('missing_or_ambiguous:' + relation)
                continue
            linked = matches[0]
            if _key(linked) in selected:
                if requirement is not None:
                    requirement.update(status='obtained', target_chunk_id=linked['chunk_id'])
                continue
            cost = _cost(linked)
            if cost > max_unit_chars or used + cost > max_chars:
                if requirement is not None:
                    requirement['status'] = 'budget_omitted'
                group['warnings'].append('budget_omitted:' + relation)
                continue
            selected[_key(linked)] = dict(linked, evidence_relation=relation,
                evidence_unit={'kind': _kind(linked), 'relation': relation,
                               'related_to_chunk_id': seed['chunk_id'],
                               'boundary_complete': True,
                               'scope': 'linked_context_only_not_answer_sufficiency'})
            used += cost
            group['added_chunk_ids'].append(linked['chunk_id'])
            if requirement is not None:
                requirement.update(status='obtained', target_chunk_id=linked['chunk_id'])
        selectors = []
        if group["kind"] == "table":
            if seed.get("table_fields"):
                # Current field projections already carry header/rowspan context.
                if any("表头未确认" in flag or "网格存在空缺" in flag
                       for flag in seed.get("review_flags") or []):
                    group["warnings"].append("table_structure_unconfirmed")
                continue
            group["warnings"].append("header_unconfirmed")
            if seed.get("row_number") != 1:
                selectors.append(({"row_number": 1}, "table_first_row_candidate"))
        elif group["kind"] == "section":
            selectors = [({"chunk_id": seed[name]}, name) for name in ("previous_id", "next_id")
                         if seed.get(name)]
        for selector, relation in selectors:
            check_cancelled()
            lookup = (identity, tuple(selector.items()))
            if lookup not in cache:
                try:
                    cache[lookup] = fetch_related(seed, selector)
                except InterruptedError:
                    raise
                except Exception as error:
                    cache[lookup] = []
                    group["warnings"].append("relation_read_failed:" + type(error).__name__)
            rows = cache[lookup]
            if len(rows) != 1:
                group["warnings"].append("missing_or_ambiguous:" + relation)
                continue
            row = rows[0]
            if not _eligible(row) or any(row.get(k) != seed.get(k) for k in
                    ("document_id", "source_sha256", "chunking_version", "source")):
                group["warnings"].append("identity_mismatch:" + relation)
                continue
            if any(row.get(k) != v for k, v in selector.items()):
                group["warnings"].append("link_mismatch:" + relation)
                continue
            if (row.get("parent_id") != seed["parent_id"]
                    or row.get("section_parent_id") != seed["section_parent_id"]
                    or row.get("heading_path") != seed.get("heading_path")
                    or _kind(row) != group["kind"]):
                group["boundary_skips"].append(relation)
                continue
            if _key(row) in selected:
                continue
            unit_cost = sum(_cost(r) for r in selected.values() if
                            (r.get("document_id"), r.get("source_sha256"), r.get("parent_id")) == identity)
            cost = _cost(row)
            if unit_cost + cost > max_unit_chars or used + cost > max_chars:
                group["warnings"].append("budget_omitted:" + relation)
                continue
            # A linked chunk has no independent relevance score. Never copy the seed's.
            selected[_key(row)] = dict(row, evidence_relation=relation)
            used += cost
            group["added_chunk_ids"].append(row["chunk_id"])
    out, emitted = [], set()
    for seed in unique.values():
        identity = tuple(seed.get(k) for k in ("document_id", "source_sha256", "parent_id"))
        group = groups.get(identity)
        members = [row for row in selected.values() if
                   tuple(row.get(k) for k in ("document_id", "source_sha256", "parent_id")) == identity] if group else [seed]
        for row in sorted(members, key=lambda row: row.get("chunk_idx", 0)):
            if _key(row) in emitted:
                continue
            emitted.add(_key(row))
            if group:
                row = dict(row, evidence_unit={"kind": group["kind"],
                           "relation": row.get("evidence_relation") or "selected",
                           "boundary_complete": not group["warnings"],
                           "scope": "linked_context_only_not_answer_sufficiency"})
                required = [r for r in group.get('context_requirements', []) if r['from_chunk_id'] == row['chunk_id']]
                if required:
                    row['evidence_unit']['context_requirements'] = required
            out.append(row)
    audit = {"version": VERSION, "groups": list(groups.values()), "used_chars": used,
             "max_chars": max_chars, "max_unit_chars": max_unit_chars,
             "omitted_chunk_ids": omitted,
             "incomplete": bool(omitted) or any(group["warnings"] for group in groups.values())}
    # Explicit links can cross article/section boundaries; keep their own
    # source/chunk identities rather than pretending they belong to the seed.
    out.extend(row for row in selected.values() if _key(row) not in emitted)
    return out, audit
