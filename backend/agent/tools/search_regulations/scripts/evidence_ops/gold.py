# -*- coding: utf-8 -*-
"""Validation and loading for the retrieval-only gold set."""
import json
from pathlib import Path


GATE_STATUSES = frozenset({"supported", "ambiguous", "irrelevant", "empty"})


def validate_case(row: dict) -> dict:
    required = {"id", "query", "answerable", "expected_tool",
                "expected_docs", "expected_evidence",
                "acceptable_gate", "source_dataset", "source_id", "review_status"}
    missing = sorted(required - set(row))
    if missing:
        raise ValueError("retrieval gold missing fields: %s" % missing)
    if not str(row["id"]).strip() or not str(row["query"]).strip():
        raise ValueError("retrieval gold id/query must be nonblank")
    if not isinstance(row["answerable"], bool):
        raise ValueError("answerable must be bool: %s" % row["id"])
    if row["expected_tool"] not in {"search_regulations", "query_course"}:
        raise ValueError("invalid expected_tool: %s" % row["id"])
    docs = row["expected_docs"]
    evidence = row["expected_evidence"]
    if not isinstance(docs, list) or not isinstance(evidence, list):
        raise ValueError("expected_docs/evidence must be lists: %s" % row["id"])
    if row["answerable"] and (not docs or not evidence):
        raise ValueError("answerable case requires docs and evidence: %s" % row["id"])
    if not row["answerable"] and (docs or evidence):
        raise ValueError("negative case cannot carry expected evidence: %s" % row["id"])
    gates = row["acceptable_gate"]
    if not isinstance(gates, list) or not gates or not set(gates) <= GATE_STATUSES:
        raise ValueError("invalid acceptable_gate: %s" % row["id"])
    return row


def load_gold(path: str | Path) -> list[dict]:
    rows = []
    seen = set()
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = validate_case(json.loads(line))
        if row["id"] in seen:
            raise ValueError("duplicate retrieval gold id at line %d: %s" %
                             (lineno, row["id"]))
        seen.add(row["id"])
        rows.append(row)
    if not rows:
        raise ValueError("retrieval gold is empty")
    return rows
