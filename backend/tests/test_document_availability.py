"""Deferred acceptance: isolated files only, never the active corpus."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from documents import document_availability as A


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    path = tmp_path / "documents.active.json"
    path.write_text(json.dumps({"documents": [{"document_id": 7, "title": "测试方案",
                                              "source_sha256": "current"}]}), "utf-8")
    monkeypatch.setattr(A.settings, "active_documents_path", str(path))
    return path


def test_maintenance_blocks_reads_and_changes_generation(isolated):
    previous = A.require_ready()
    with A.writer_lock(), A.mutation(7):
        with pytest.raises(A.SourceUnavailable):
            A.require_ready()
    with pytest.raises(A.SourceUnavailable):
        A.require_ready(previous)
    A.require_source("测试方案.md", "current")


def test_failure_stays_closed_until_same_document_retry(isolated):
    with pytest.raises(RuntimeError):
        with A.writer_lock(), A.mutation(7):
            raise RuntimeError("transport failed")
    with pytest.raises(A.SourceUnavailable):
        A.require_ready()
    with A.writer_lock():
        with pytest.raises(A.SourceUnavailable):
            with A.mutation(8):
                pass
    with A.writer_lock(), A.mutation(7):
        pass
    A.require_ready()


def test_missing_or_changed_structured_source_fails_closed(isolated):
    with pytest.raises(A.SourceUnavailable):
        A.require_source("已删除的方案", "old")
    with pytest.raises(A.SourceUnavailable):
        A.require_source("测试方案", "old")


def test_raw_file_and_exact_indexed_text_identities_are_both_bound(isolated):
    catalog = json.loads(isolated.read_text("utf-8"))
    catalog["documents"][0]["indexed_source_sha256"] = "normalized-current"
    isolated.write_text(json.dumps(catalog), "utf-8")
    A.require_source("测试方案", "current")
    A.require_source("测试方案", "normalized-current")
    with pytest.raises(A.SourceUnavailable):
        A.require_source("测试方案", "old-normalized")


def test_duplicate_source_does_not_pick_first(isolated):
    catalog = json.loads(isolated.read_text("utf-8"))
    catalog["documents"].append(dict(catalog["documents"][0], document_id=8))
    isolated.write_text(json.dumps(catalog), "utf-8")
    with pytest.raises(A.SourceUnavailable):
        A.require_source("测试方案")
    A.require_source("测试方案", "current", document_id=7)
    with pytest.raises(A.SourceUnavailable):
        A.require_source("测试方案", "current", document_id=9)


def test_writer_rejects_overlap(isolated):
    with A.writer_lock():
        with pytest.raises(A.SourceUnavailable):
            with A.writer_lock():
                pass
