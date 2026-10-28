"""Update/delete identity tests use an isolated temporary catalog, never real data."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from documents import document_metadata as M


def catalog(tmp_path, monkeypatch):
    path = tmp_path / "documents.json"
    item = {"document_id": 1, "title": "本科生办理规定", "chunks": 2,
            "source_sha256": hashlib.sha256("旧正文".encode()).hexdigest(),
            "scope": {"level": "undergraduate", "subject": "student", "college": "旧学院"}}
    path.write_text(json.dumps({"documents": [item], "points": 2}), encoding="utf-8")
    monkeypatch.setattr(M.settings, "active_documents_path", str(path))
    return path


def test_same_title_new_source_drops_old_body_derived_scope(tmp_path, monkeypatch):
    path = catalog(tmp_path, monkeypatch)
    M.record_document(1, "本科生办理规定", "新正文", "规章制度", 3)
    data = json.loads(path.read_text("utf-8"))
    assert data["documents"][0]["scope"] == {"level": "undergraduate", "subject": "student"}
    assert data["documents"][0]["source_sha256"] == hashlib.sha256("新正文".encode()).hexdigest()
    assert data["points"] == 3 and not path.with_suffix(".staging").exists()


def test_unchanged_source_retains_reviewed_scope(tmp_path, monkeypatch):
    path = catalog(tmp_path, monkeypatch)
    M.record_document(1, "本科生办理规定", "旧正文", "规章制度", 2)
    assert json.loads(path.read_text("utf-8"))["documents"][0]["scope"]["college"] == "旧学院"


def test_current_plan_update_restores_verified_undergraduate_scope(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "final-md/培养方案/2025级人工智能专业培养方案(主修).md"
    text = source.read_text(encoding="utf-8")
    path = catalog(tmp_path, monkeypatch)
    M.record_document(4, source.stem, text, "培养方案", 237)
    row = next(r for r in json.loads(path.read_text("utf-8"))["documents"] if r["document_id"] == 4)
    assert row["scope"]["level"] == "undergraduate" and row["scope"]["subject"] == "student"
    assert row["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row["indexed_source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert M._verified_plan_identity(source.stem, text + "\n已修改正文") is None


def test_title_alone_never_proves_an_undergraduate_plan():
    assert M._verified_plan_identity("2025级未知专业培养方案(主修)", "本科生") is None


def test_delete_forgets_active_identity_and_recounts(tmp_path, monkeypatch):
    path = catalog(tmp_path, monkeypatch)
    M.forget_document(1)
    assert json.loads(path.read_text("utf-8")) == {"documents": [], "points": 0}


def test_ingest_failure_is_committed_not_rolled_back_on_close(monkeypatch, tmp_path):
    import pytest
    from sqlalchemy import create_engine, Text
    from sqlalchemy.orm import sessionmaker
    from database import db
    monkeypatch.setattr(M.settings, "active_documents_path", str(tmp_path / "documents.json"))
    from agent.tools.search_regulations.scripts import indexing
    engine = create_engine("sqlite:///:memory:")
    # SQLite adapter only; the real ORM/session lifecycle stays in use.
    monkeypatch.setattr(db.Document.__table__.c.content, "type", Text())
    db.Document.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "SessionLocal", sessions)
    with sessions() as session:
        session.add(db.Document(title="测试规定", source_type="md", content="旧正文", status="processing", chunk_count=2))
        session.commit()
    def fail(*a, **kw):
        raise RuntimeError("test-only index failure")
    monkeypatch.setattr(indexing, "_index_document", fail)
    try:
        with pytest.raises(RuntimeError, match="test-only index failure"):
            indexing.add_document("测试规定", "新正文", "md", "规章制度")
        with sessions() as session:
            item = session.query(db.Document).filter_by(title="测试规定").one()
            assert item.status == "failed" and item.content == "旧正文"
    finally:
        engine.dispose()
