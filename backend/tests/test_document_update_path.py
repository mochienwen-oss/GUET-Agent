"""Deferred real local store path: SQLite + in-memory Qdrant, fake encoder only."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def stores(tmp_path, monkeypatch):
    import numpy as np
    from sqlalchemy import create_engine, Text
    from sqlalchemy.orm import sessionmaker
    from qdrant_client import QdrantClient, models
    from database import db
    from documents import document_availability as availability
    from agent.tools.search_regulations.scripts import indexing, bm25
    settings = indexing.settings
    monkeypatch.setattr(settings, "active_documents_path", str(tmp_path / "active.json"))
    monkeypatch.setattr(settings, "bm25_vocab_path", str(tmp_path / "bm25.json"))
    monkeypatch.setattr(settings, "qdrant_collection", "update_path")
    monkeypatch.setattr(settings, "use_sparse", True)
    Path(settings.active_documents_path).write_text(json.dumps({"documents": [], "points": 0}), "utf-8")
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(db.Document.__table__.c.content, "type", Text())
    db.Document.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "SessionLocal", sessions)
    with sessions() as session:
        session.add(db.Document(title="测试规定", content="旧正文", status="ready"))
        session.commit()
    client = QdrantClient(":memory:")
    client.create_collection("update_path", vectors_config={"dense": models.VectorParams(
        size=3, distance=models.Distance.COSINE)}, sparse_vectors_config={"sparse": models.SparseVectorParams()})
    monkeypatch.setattr(indexing, "_client", client)

    class Encoder:
        max_seq_length = 128
        def tokenizer(self, text, **kwargs):
            return {"input_ids": list(range(len(text)))}
        def encode(self, texts, **kwargs):
            return np.array([[1., 0., 0.] for _ in texts])
    monkeypatch.setattr(indexing, "_encoder", Encoder())
    monkeypatch.setattr(indexing, "_build_index_units", lambda text, category="": [{
        "chunk_idx": 0, "text": text, "heading_path": [], "block_type": "paragraph",
        "category": category, "source_sha256": hashlib.sha256(text.encode()).hexdigest()}])
    bm25.save_vocab(bm25.build_vocab(["旧正文"]))
    yield indexing, bm25, availability, sessions, client
    client.close()
    engine.dispose()


def test_upload_reserves_before_response_and_publishes_last(stores):
    indexing, bm25, availability, sessions, client = stores
    task = indexing.prepare_add_document("测试规定", "新正文增加课程学分", "md", "规章制度")
    with pytest.raises(availability.SourceUnavailable):
        availability.require_ready()
    with pytest.raises(availability.SourceUnavailable):
        indexing.prepare_add_document("测试规定", "另一份正文", "md", "规章制度")
    task()
    availability.require_source("测试规定", hashlib.sha256("新正文增加课程学分".encode()).hexdigest())
    from database.db import Document
    with sessions() as session:
        row = session.query(Document).one()
        assert row.status == "ready" and row.chunk_count == 1
    assert bm25.load_vocab()["n"] == client.count("update_path").count == 1


def test_sparse_failure_stays_closed_and_retry_recovers(stores, monkeypatch):
    indexing, _, availability, sessions, _ = stores
    refresh = indexing._refresh_sparse
    def fail():
        raise RuntimeError("sparse transport failed")
    monkeypatch.setattr(indexing, "_refresh_sparse", fail)
    task = indexing.prepare_add_document("测试规定", "新正文", "md", "规章制度")
    with pytest.raises(RuntimeError, match="sparse transport"):
        task()
    with pytest.raises(availability.SourceUnavailable):
        availability.require_ready()
    from database.db import Document
    with sessions() as session:
        row = session.query(Document).one()
        assert row.status == "failed" and row.content == "旧正文"
    monkeypatch.setattr(indexing, "_refresh_sparse", refresh)
    indexing.prepare_add_document("测试规定", "新正文", "md", "规章制度")()
    availability.require_ready()


def test_delete_invalidates_catalog_source(stores):
    indexing, _, availability, sessions, _ = stores
    did = indexing.add_document("测试规定", "新正文", "md", "规章制度")
    indexing.delete_document(did)
    with pytest.raises(availability.SourceUnavailable):
        availability.require_source("测试规定")


def test_restart_marks_interrupted_task_failed_without_reopening(stores):
    _, _, availability, sessions, _ = stores
    from database.db import Document
    with sessions() as session:
        row = session.query(Document).one()
        row.status = "processing"
        session.commit()
        did = row.id
    state = availability.snapshot()
    state.update(status="processing", updating_document=str(did), revision="crash")
    state["documents"][str(did)] = "processing"
    availability._save(state)
    availability.recover_interrupted()
    with sessions() as session:
        assert session.query(Document).one().status == "failed"
    with pytest.raises(availability.SourceUnavailable):
        availability.require_ready()
