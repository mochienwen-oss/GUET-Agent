"""Keep the active document identity catalog in step with online uploads."""
import hashlib
import json
import os
from pathlib import Path
from threading import Lock

from core.config import settings
from core.paths import PLAN_DB_PATH, PROJECT_ROOT
from agent.core.scope import document_scope

_LOCK = Lock()


def _verified_plan_identity(title, content):
    """Only the current source-bound undergraduate dataset may supply its scope."""
    from agent.core.citations import short_title
    plans = json.loads(PLAN_DB_PATH.read_text(encoding="utf-8"))
    if plans.get("meta", {}).get("source_dir") != "final-md/培养方案":
        return None
    matches = [row for row in plans["majors"]
               if short_title(row["file"]) == short_title(title)]
    if len(matches) != 1:
        return None
    plan = matches[0]
    root = PROJECT_ROOT / "final-md/培养方案"
    path = (root / plan["file"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != plan.get("provenance", {}).get("sha256"):
        return None
    if raw.decode("utf-8-sig").replace("\r\n", "\n") != content.replace("\r\n", "\n"):
        return None
    return {"sha256": digest, "scope": {"level": "undergraduate", "subject": "student"}}


def record_document(document_id, title, content, category, chunks):
    path = Path(settings.active_documents_path)
    if not path.exists():
        return  # Legacy/local deployments without an active migration catalog.
    with _LOCK:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {x["document_id"]: x for x in data["documents"]}
        old = entries.get(document_id) or {}
        indexed_sha = hashlib.sha256(content.encode()).hexdigest()
        verified = _verified_plan_identity(title, content)
        source_sha = verified["sha256"] if verified else indexed_sha
        same_source = old.get("title") == title and old.get("source_sha256") == source_sha
        scope = document_scope(title, metadata=old.get("scope") if same_source else None)
        if verified:
            scope.update(verified["scope"])
        entries[document_id] = {"document_id": document_id, "title": title,
                                "source_sha256": source_sha,
                                "indexed_source_sha256": indexed_sha,
                                "category": category, "chunks": chunks, "scope": scope}
        data.update(documents=list(entries.values()), points=sum(x["chunks"] for x in entries.values()))
        stage = path.with_suffix(".staging")
        stage.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, path)


def forget_document(document_id):
    path = Path(settings.active_documents_path)
    if not path.exists():
        return
    with _LOCK:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["documents"] = [x for x in data["documents"] if x["document_id"] != document_id]
        data["points"] = sum(x["chunks"] for x in data["documents"])
        stage = path.with_suffix(".staging")
        stage.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, path)
