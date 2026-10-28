"""Scope writeback validation only; no real metadata writes."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "_work/phase8/scope-backfill/apply_scope_review.py"
spec = importlib.util.spec_from_file_location("scope_review_apply", PATH)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def inputs():
    source = json.dumps({"documents": [{"document_id": 1, "source_sha256": "source", "scope": {}}]}).encode()
    decisions = {"catalog_sha256": hashlib.sha256(source).hexdigest(), "decisions": [{
        "document_id": 1, "source_sha256": "source", "review_status": "approved",
        "reviewed_by": "human:owner", "scope": {"level": "undergraduate", "subject": "student"}}]}
    return source, decisions


def test_review_is_bound_to_source_and_does_not_mutate_input():
    source, decisions = inputs()
    output = review.prepare(source, decisions)
    assert output["documents"][0]["scope"]["subject"] == "student"
    assert json.loads(source)["documents"][0]["scope"] == {}
    decisions["decisions"][0]["source_sha256"] = "changed"
    with pytest.raises(ValueError):
        review.prepare(source, decisions)


def test_pending_and_validity_fields_are_not_accepted():
    source, decisions = inputs()
    decisions["decisions"][0]["review_status"] = "pending"
    with pytest.raises(ValueError):
        review.prepare(source, decisions)
    decisions["decisions"][0]["review_status"] = "approved"
    decisions["decisions"][0]["scope"]["effective_from"] = "2026-10-20"
    with pytest.raises(ValueError):
        review.prepare(source, decisions)
