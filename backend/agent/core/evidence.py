# -*- coding: utf-8 -*-
"""证据身份与追加式台账。"""
import hashlib
import json

from .contracts import Evidence


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def chunk_id_of(document_id, chunk_idx) -> str | None:
    if document_id is None or chunk_idx is None:
        return None
    return "%s:%s" % (document_id, chunk_idx)


def args_hash(args: dict) -> str:
    raw = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def evidence_status(packet, results=()):
    """Execution, obtained records and bounded completeness are separate facts."""
    executions = [{"tool": result.tool, "ok": result.ok, "status": result.status} for result in results]
    complete = [{"kind": "enumeration", "evidence_id": item["evidence_id"],
                 "record_ids": [record["record_id"] for record in item["records"]]}
                for item in packet.get("enumerations") or []]
    records = [result.data["target_record"] for result in results if result.data.get("target_record")]
    gaps = [{"kind": "record", "exam": record["exam"], "score": record["score"],
             "reason": record.get("reason"), "origin": "retrieval"}
            for record in records if record.get("status") != "obtained"]
    gaps.extend({"kind": "policy", "field": "linked_applicability", "origin": "linked_context",
                 "affects": "personal_applicability", "evidence_id": row["evidence_id"],
                 "relation_id": link["id"], "reason": link["status"]}
                for row in packet.get("evidence") or []
                for link in (row.get("evidence_unit") or {}).get("context_requirements", [])
                if link.get("required_for") == "applicability" and link.get("status") != "obtained")
    return {"execution": executions, "state": "available" if packet.get("evidence") else "not_obtained",
            "confirmed_complete": complete, "target_records": records,
            "gaps": gaps,
            "source_revision": packet.get("source_revision"),
            "meaning": "available与success只说明取得或执行状态；完整性仅限confirmed_complete所列集合，非任意问题充分度。"}


def identity(source) -> str:
    """身份优先级：chunk_id → text_hash → title + line + heading_path。"""
    if source.chunk_id:
        return "c:" + source.chunk_id
    if (source.text or "").strip():
        return "t:" + (source.text_hash or text_hash(source.text))
    return "l:%s|%s|%s" % (source.title, source.line,
                            "/".join(source.heading_path or []))


def evidence_id(tool: str, ahash: str, source) -> str:
    raw = "%s|%s|%s" % (tool, ahash, identity(source))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class EvidenceLedger:
    """去重键为 `(tool, args_hash, identity(source))` 的追加式台账。"""

    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._keys: set = set()

    def add(self, tool: str, args: dict, source, snippet: str = "",
            retrieved_at: str = "") -> Evidence | None:
        ah = args_hash(args)
        key = (tool, ah, identity(source))
        if key in self._keys:
            return None
        self._keys.add(key)
        eid = evidence_id(tool, ah, source)
        ev = Evidence(evidence_id=eid, source=source, tool=tool,
                      args=dict(args or {}), snippet=snippet or source.text,
                      retrieved_at=retrieved_at)
        self._items[eid] = ev
        return ev

    def add_many(self, tool: str, args: dict, sources, retrieved_at: str = "") -> list:
        out = []
        for source in sources or []:
            item = self.add(tool, args, source, retrieved_at=retrieved_at)
            if item is not None:
                out.append(item)
        return out

    def get(self, eid: str):
        return self._items.get(eid)

    def items(self) -> list:
        return list(self._items.values())

    def ids(self) -> list:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, eid: str) -> bool:
        return eid in self._items
