"""Processing availability, not publication dates or regulatory validity.

Mutations take an OS lock and close reads while SQL/vector/sparse artifacts
are being changed. The durable marker remains closed after a crash/failure.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from core.config import settings

_LOCK = Lock()


class SourceUnavailable(RuntimeError):
    pass


def _path():
    return Path(settings.active_documents_path).with_suffix(".availability.json")


def snapshot():
    path = _path()
    if not path.exists():
        return {"revision": "initial", "status": "ready", "documents": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") not in {"ready", "processing", "failed"}:
        raise SourceUnavailable("文档处理状态损坏，暂停知识库查询")
    return data


def _save(data):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_suffix(".staging")
    stage.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(stage, path)


def require_ready(expected_revision=None):
    data = snapshot()
    if data["status"] != "ready":
        raise SourceUnavailable("知识库正在更新或上次更新未完成，请待管理员处理后重试")
    if expected_revision is not None and data["revision"] != expected_revision:
        raise SourceUnavailable("本次查询期间知识库发生更新，请重新提问以使用一致的数据")
    return data["revision"]


def require_source(title, source_sha=None, *, document_id=None):
    require_ready()
    from agent.core.citations import short_title
    path = Path(settings.active_documents_path)
    if not path.exists():
        return  # Explicitly supported legacy installations without a catalog.
    rows = json.loads(path.read_text(encoding="utf-8"))["documents"]
    matches = [r for r in rows if short_title(r["title"]) == short_title(title)]
    if document_id is not None:
        matches = [r for r in matches if r["document_id"] == document_id]
    if len(matches) != 1:
        raise SourceUnavailable("文档来源缺失或不唯一，未使用残留的结构化记录")
    row = matches[0]
    state = snapshot()["documents"].get(str(row["document_id"]), "ready")
    if state != "ready":
        raise SourceUnavailable("该文档尚未完成处理，暂不可查询")
    identities = {row["source_sha256"], row.get("indexed_source_sha256", row["source_sha256"])}
    if source_sha and source_sha not in identities:
        raise SourceUnavailable("培养方案源文件已改变，结构化记录尚未重新核验")


@contextmanager
def writer_lock():
    """Single writer across workers; do not queue another update silently."""
    if not _LOCK.acquire(blocking=False):
        raise SourceUnavailable("已有文档任务正在运行，请稍后重试")
    stream = None
    locked = False
    try:
        path = _path().with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise SourceUnavailable("已有文档任务正在运行，请稍后重试") from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        if stream is not None:
            stream.close()
        _LOCK.release()


@contextmanager
def mutation(document_id):
    """Caller holds writer_lock; only the affected failed document may retry."""
    data = snapshot()
    key = str(document_id)
    if data["status"] != "ready" and data.get("updating_document") != key:
        raise SourceUnavailable("请先重试或处理上次中断的文档任务")
    data.update(status="processing", revision=uuid4().hex, updating_document=key)
    data["documents"][key] = "processing"
    _save(data)
    try:
        yield
    except BaseException:
        data.update(status="failed", revision=uuid4().hex)
        data["documents"][key] = "failed"
        _save(data)
        raise
    else:
        data.update(status="ready", revision=uuid4().hex, updating_document=None)
        data["documents"][key] = "ready"
        _save(data)


def recover_interrupted():
    """Run at startup only when no other worker holds the writer lease."""
    try:
        with writer_lock():
            from database.db import Document, SessionLocal
            with SessionLocal() as db:
                interrupted = [row.id for row in db.query(Document).filter(Document.status == "processing").all()]
                db.query(Document).filter(Document.status == "processing").update(
                    {Document.status: "failed"}, synchronize_session=False)
                db.commit()
                failed_ids = [row.id for row in db.query(Document).filter(Document.status == "failed").all()]
            data = snapshot()
            for did in failed_ids:
                data["documents"][str(did)] = "failed"
            if data["status"] == "processing":
                data.update(status="failed", revision=uuid4().hex)
            if interrupted and data["status"] == "ready":
                data.update(status="failed", revision=uuid4().hex,
                            updating_document=str(interrupted[0]))
            if failed_ids or data["status"] == "failed":
                _save(data)
    except SourceUnavailable:
        return  # Another worker is actively processing; do not mark its task failed.
