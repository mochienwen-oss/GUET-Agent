# -*- coding: utf-8 -*-
"""Run retrieval-only evaluation and assign one evidence-backed failure layer."""
import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from core.config import settings

from . import gate_signals, indexing, retrieval
from .gold import load_gold
from .trace import RetrievalTrace


ROOT = Path(__file__).resolve().parents[5]
DEFAULT_GOLD = ROOT / "测试集" / "retrieval_gold.jsonl"
DEFAULT_REPORT = ROOT / "_work" / "retrieval-diagnostics-phase4.json"


def _norm_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"_\d{1,3}$", "", value)
    value = re.sub(r"\s+", "", value)
    match = re.search(r"《([^》]+)》", value)
    return match.group(1) if match else value


def same_doc(left: str, right: str) -> bool:
    left_key, right_key = _norm_title(left), _norm_title(right)
    return bool(left_key and right_key and
                (left_key in right_key or right_key in left_key))


def _norm_evidence(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", value)


def _expected_rank(case: dict, rows: list[dict], limit: int | None = None):
    for fallback_rank, item in enumerate(rows, 1):
        rank = item.get("rank") or fallback_rank
        if limit is not None and rank > limit:
            continue
        if any(same_doc(doc, item.get("title") or "")
               for doc in case["expected_docs"]):
            return rank
    return None


def _expected_evidence_rank(case: dict, rows: list[dict],
                            limit: int | None = None,
                            corpus_chunks: dict[str, str] | None = None):
    """返回全部期望证据都出现时的最末名次；任一证据缺失则返回 ``None``。"""
    evidence = case.get("expected_evidence") or []
    if not evidence:
        return _expected_rank(case, rows, limit)
    matched_ranks = []
    for expected in evidence:
        normalized = _norm_evidence(expected)
        ranks = []
        for fallback_rank, item in enumerate(rows, 1):
            rank = item.get("rank") or fallback_rank
            if limit is not None and rank > limit:
                continue
            chunk_id = item.get("chunk_id")
            text = ((corpus_chunks or {}).get(chunk_id)
                    if chunk_id is not None else None)
            text = text if text is not None else (item.get("text") or
                                                   item.get("text_preview") or "")
            if normalized and normalized in _norm_evidence(text):
                ranks.append(rank)
        if not ranks:
            return None
        matched_ranks.append(min(ranks))
    return max(matched_ranks) if matched_ranks else None


def diagnose_case(case: dict, trace: dict, *, corpus_docs: set[str] | None = None,
                  corpus_texts: dict[str, list[str]] | None = None,
                  corpus_chunks: dict[str, str] | None = None,
                  final_texts: list[str] | None = None) -> dict:
    gate = (trace.get("gate") or {}).get("status")
    if not case["answerable"]:
        return {"diagnosis": ("success" if gate in case["acceptable_gate"] else "gate"),
                "gate": gate}

    if corpus_docs is not None and not all(
            any(same_doc(doc, actual) for actual in corpus_docs)
            for doc in case["expected_docs"]):
        return {"diagnosis": "corpus_coverage", "gate": gate}

    if case.get("expected_evidence") and corpus_texts is not None:
        all_expected_text = "\n".join(
            text for title, texts in corpus_texts.items()
            if any(same_doc(doc, title) for doc in case["expected_docs"])
            for text in texts)
        normalized_corpus = _norm_evidence(all_expected_text)
        if not all(_norm_evidence(evidence) in normalized_corpus
                   for evidence in case["expected_evidence"]):
            return {"diagnosis": "chunking", "gate": gate,
                    "detail": "expected evidence absent from indexed text"}

    stages = trace.get("stages") or {}
    rank = lambda name, limit=None: _expected_evidence_rank(  # noqa: E731
        case, stages.get(name) or [], limit, corpus_chunks)
    if rank("fused") is None:
        return {"diagnosis": "candidate_pool", "gate": gate}
    if rank("rerank_pool") is None:
        return {"diagnosis": "dedupe_or_truncation", "gate": gate}
    if gate not in case["acceptable_gate"]:
        return {"diagnosis": "gate", "gate": gate}

    if case.get("expected_evidence"):
        if final_texts is None:
            final_ok = rank("final") is not None
        else:
            selected = _norm_evidence("\n".join(final_texts))
            final_ok = all(_norm_evidence(evidence) in selected
                           for evidence in case["expected_evidence"])
        if not final_ok:
            if rank("reranked", 5) is None:
                return {"diagnosis": "rerank", "gate": gate}
            return {"diagnosis": "final_selection", "gate": gate}
    return {"diagnosis": "success", "gate": gate}


def summarize(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row["case"]["answerable"]]
    negative = [row for row in rows if not row["case"]["answerable"]]

    def ratio(numerator, denominator):
        return numerator / denominator if denominator else 0.0

    candidate_hits = sum(row["candidate_rank"] is not None and
                         row["candidate_rank"] <= 120 for row in answerable)
    rerank_hits = sum(row["rerank_rank"] is not None and
                      row["rerank_rank"] <= 5 for row in answerable)
    evidence_candidate_hits = sum(row.get("candidate_evidence_rank") is not None and
                                  row["candidate_evidence_rank"] <= 120
                                  for row in answerable)
    evidence_rerank_hits = sum(row.get("rerank_evidence_rank") is not None and
                              row["rerank_evidence_rank"] <= 5
                              for row in answerable)
    false_rejects = sum(row["gate"] not in row["case"]["acceptable_gate"]
                        for row in answerable)
    false_accepts = sum(row["gate"] not in row["case"]["acceptable_gate"]
                        for row in negative)
    return {
        "counts": {"all": len(rows), "answerable": len(answerable),
                   "negative": len(negative)},
        "candidate_recall@120": ratio(candidate_hits, len(answerable)),
        "rerank_recall@5": ratio(rerank_hits, len(answerable)),
        "evidence_candidate_recall@120": ratio(evidence_candidate_hits,
                                                len(answerable)),
        "evidence_rerank_recall@5": ratio(evidence_rerank_hits,
                                          len(answerable),),
        "gate_false_reject_rate": ratio(false_rejects, len(answerable)),
        "out_of_scope_false_accept_rate": ratio(false_accepts, len(negative)),
        "diagnoses": dict(Counter(row["diagnosis"] for row in rows)),
    }


def _corpus_snapshot() -> tuple[set[str], dict[str, list[str]], dict[str, str], dict]:
    indexing.ensure_collection()
    client = indexing._get_client()
    docs, texts, chunks, identities = set(), defaultdict(list), {}, []
    offset = None
    while True:
        records, offset = client.scroll(collection_name=settings.qdrant_collection,
                                        limit=1000, offset=offset, with_payload=True)
        for record in records:
            payload = record.payload or {}
            title = payload.get("source") or ""
            if title:
                docs.add(title)
                text = payload.get("text") or ""
                texts[title].append(text)
                chunk_id = "%s:%s" % (payload.get("document_id"),
                                       payload.get("chunk_idx"))
                chunks[chunk_id] = text
                identities.append("%s:%s:%s:%s" % (
                    payload.get("document_id"), payload.get("chunk_idx"), title,
                    hashlib.sha256(text.encode("utf-8")).hexdigest()))
        if offset is None:
            digest = hashlib.sha256("\n".join(sorted(identities)).encode("utf-8")).hexdigest()
            return docs, dict(texts), chunks, {
                "collection": settings.qdrant_collection,
                "point_count": len(identities),
                "document_count": len(docs),
                "sha256": digest,
            }


def _config_fingerprint() -> dict:
    values = {
        "collection": settings.qdrant_collection,
        "embed_model": settings.embed_model,
        "rerank_model": settings.rerank_model,
        "embed_device": settings.embed_device,
        "rerank_device": settings.rerank_device,
        "rerank_fp16": settings.rerank_fp16,
        "top_k": settings.top_k,
        "recall_pool": settings.recall_pool,
        "rerank_pool": settings.rerank_pool,
        "rrf_k": settings.rrf_k,
        "near_dup_threshold": settings.near_dup_threshold,
        "use_sparse": settings.use_sparse,
        "aux_route": settings.aux_route,
        "gate_multi_signal": settings.gate_multi_signal,
        "aux_focus_topn": settings.aux_focus_topn,
        "aux_focus_need": settings.aux_focus_need,
        "multi_signal_focus_cap": settings.multi_signal_focus_cap,
        "gate_scenario_band": settings.gate_scenario_band,
        "rerank_strict": settings.rerank_strict,
        "rerank_low": retrieval.DEFAULT_RERANK_LOW,
        "rerank_high": retrieval.DEFAULT_RERANK_HIGH,
    }
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return {"sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "values": values}


def signals_from_trace(query: str, trace_data: dict,
                       anchors: gate_signals.AnchorIndex | None = None,
                       absent_reference: str = "") -> dict:
    """Diagnostics-only projection of a real trace into gate signals.

    Additive telemetry: it does not filter candidates, change the gate, or
    mutate the retrieval result.
    """
    stages = trace_data.get("stages") or {}
    degradations = trace_data.get("degradations") or []
    bm25_unavailable = any(item.get("stage") == "bm25"
                           and item.get("code") in ("vocab_missing", "fallback")
                           for item in degradations)
    ranked = stages.get("reranked") or []
    selected = stages.get("final") or ranked[:5]
    return gate_signals.collect(
        query=query, ranked=ranked, selected=selected,
        dense_rows=stages.get("dense") or [],
        bm25_rows=stages.get("bm25") or [],
        bm25_available=not bm25_unavailable,
        focus_document_id=trace_data.get("focus_document_id"),
        degradations=degradations, anchors=anchors,
        absent_reference=absent_reference).to_dict()


def load_keyword_map(path: str | Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("关键词映射必须是对象")
    for case_id, keywords in data.items():
        if not isinstance(case_id, str) or not isinstance(keywords, list) or not all(
                isinstance(item, str) for item in keywords):
            raise ValueError("关键词映射必须是 case_id 到字符串数组")
    return data


def run(gold_path=DEFAULT_GOLD, limit: int | None = None, progress=None,
        keyword_map: dict[str, list[str]] | None = None) -> dict:
    all_gold = load_gold(gold_path)
    excluded = [row for row in all_gold if row["expected_tool"] != "search_regulations"]
    gold = [row for row in all_gold if row["expected_tool"] == "search_regulations"]
    if limit is not None:
        gold = gold[:limit]
    corpus_docs, corpus_texts, corpus_chunks, corpus_meta = _corpus_snapshot()
    rows = []
    keyword_map = keyword_map or {}
    for case in gold:
        trace = RetrievalTrace.start(case["query"])
        keywords = list(keyword_map.get(case["id"]) or [])
        raw = (retrieval.retrieve_frozen(case["query"], trace=trace,
                                         keywords=keywords)
               if keywords else
               retrieval.retrieve_frozen(case["query"], trace=trace))
        trace_data = trace.to_dict()
        final_texts = [row.get("text") or "" for row in raw.get("contexts") or []]
        diagnosis = diagnose_case(case, trace_data, corpus_docs=corpus_docs,
                                  corpus_texts=corpus_texts,
                                  corpus_chunks=corpus_chunks,
                                  final_texts=final_texts)
        fused = trace_data["stages"].get("fused") or []
        reranked = trace_data["stages"].get("reranked") or []
        rows.append({
            "id": case["id"], "case": case, "trace_id": trace.trace_id,
            "keywords": keywords,
            "candidate_rank": _expected_rank(case, fused),
            "rerank_rank": _expected_rank(case, reranked),
            "candidate_evidence_rank": _expected_evidence_rank(
                case, fused, corpus_chunks=corpus_chunks),
            "rerank_evidence_rank": _expected_evidence_rank(
                case, reranked, corpus_chunks=corpus_chunks),
            "gate": (trace_data.get("gate") or {}).get("status"),
            "gate_reason": (trace_data.get("gate") or {}).get("reason"),
            "diagnosis": diagnosis["diagnosis"],
            "diagnosis_detail": diagnosis.get("detail"),
            "trace": trace_data,
        })
        if progress is not None:
            progress(len(rows), len(gold), rows[-1])
    return {"config": _config_fingerprint(), "corpus": corpus_meta,
            "query_plan": {"keyword_cases": sum(bool(row.get("keywords")) for row in rows),
                           "keyword_map_entries": len(keyword_map)},
            "scope": {"tool": "search_regulations", "included": len(gold),
                      "excluded_other_tools": len(excluded),
                      "excluded_ids": [row["id"] for row in excluded]},
            "summary": summarize(rows), "cases": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run retrieval trace diagnostics")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--qdrant-local", default=None,
                        help="optional embedded Qdrant directory; leaves production config unchanged")
    parser.add_argument("--keywords-map", default=None,
                        help="optional reviewed JSON mapping case id to BM25 keywords")
    args = parser.parse_args()
    local_client = None
    if args.qdrant_local:
        from qdrant_client import QdrantClient
        local_client = QdrantClient(path=args.qdrant_local)
        indexing._client = local_client
    try:
        def progress(done, total, row):
            if done == 1 or done % 5 == 0 or done == total:
                print("progress=%d/%d last=%s diagnosis=%s" %
                      (done, total, row["id"], row["diagnosis"]), flush=True)

        report = run(args.gold, args.limit, progress=progress,
                     keyword_map=load_keyword_map(args.keywords_map))
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if local_client is not None:
            local_client.close()
    print(json.dumps(report["summary"], ensure_ascii=False))
    print("report=%s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
