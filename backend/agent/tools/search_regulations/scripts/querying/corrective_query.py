# -*- coding: utf-8 -*-
"""One bounded deterministic corrective query for an uncertain retrieval.

Allowed inputs only: the original query, the validated QueryPlan, reviewed route
templates, and reviewed formal entity/title/document-number anchors. No LLM, no
free-form entity invention, no second retry.
"""
from dataclasses import asdict, dataclass
import unicodedata

from agent.core.citations import short_title

from agent.tools.search_regulations.scripts.querying import intent
from agent.tools.search_regulations.scripts.evidence_ops.gate_signals import AnchorIndex, find_anchors, title_key

_ANCHORS = None


def _anchors() -> AnchorIndex:
    """Reuse the retrieval gate's cached reviewed anchor index."""
    global _ANCHORS
    if _ANCHORS is None:
        try:
            from agent.tools.search_regulations.scripts.recall import retrieval
            _ANCHORS = retrieval._gate_anchors()
        except Exception:
            _ANCHORS = AnchorIndex()
    return _ANCHORS


@dataclass(frozen=True)
class CorrectiveQuery:
    original_query: str
    normalized_query: str
    deterministic_expansions: tuple[str, ...]
    reason: str
    retry_count: int = 1
    title_correction: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["deterministic_expansions"] = list(self.deterministic_expansions)
        return data


def _signal_value(signals, name, default=None):
    if signals is None:
        return default
    if isinstance(signals, dict):
        return signals.get(name, default)
    return getattr(signals, name, default)


def _candidate_title_correction(signals) -> str | None:
    """One reviewed formal title derived from the first-round top candidate.

    Every condition is a fail-closed guard. The returned title only widens
    recall; it is never treated as evidence that the claim is supported.
    """
    if not signals:
        return None
    if _signal_value(signals, "evidence_exposable") is not False:
        return None
    if _signal_value(signals, "absent_reference"):
        return None
    if _signal_value(signals, "scope_conflict_kind"):
        return None
    if _signal_value(signals, "bm25_available") is not True:
        return None
    degradations = _signal_value(signals, "degradations") or ()
    for item in degradations:
        if item.get("stage") in ("rerank", "bm25") and item.get("code") in (
                "fallback", "vocab_missing"):
            return None
    converged = (_signal_value(signals, "dense_bm25_top1_agreement") is True
                 or ((_signal_value(signals, "dense_bm25_top3_overlap") or 0)
                     >= 0.5 and (_signal_value(signals, "top_doc_count") or 0) >= 2))
    if not converged:
        return None
    source_title = _signal_value(signals, "top_document_title") or ""
    if not source_title.strip():
        return None
    source_norm = unicodedata.normalize("NFKC", source_title).strip()
    key = title_key(source_title)
    matches = [raw for anchor_key, raw in _anchors().titles if anchor_key == key]
    exact = [raw for raw in matches
             if unicodedata.normalize("NFKC", raw).strip() == source_norm]
    if len(exact) != 1:
        return None
    return exact[0]


def build_corrective_query(original_query, query_plan, first_decision,
                           signals=None):
    """Return at most one deterministic corrective query, or None.

    Only an `uncertain` first decision is eligible. `empty`/`irrelevant` and any
    decision without retry permission are never corrected. The optional
    candidate-title branch is additionally gated by `_candidate_title_correction`.
    """
    if first_decision is None:
        return None
    if first_decision.status != "uncertain" or not first_decision.retry_allowed:
        return None

    expansions: list[str] = []
    title_correction = (_candidate_title_correction(signals)
                        if first_decision.evidence_exposable is False else None)
    if title_correction:
        expansions.append(title_correction)
    found = find_anchors(original_query, _anchors())
    if found["title"]:
        expansions.append(short_title(found["title"]))
    elif found["docno"]:
        expansions.append(found["docno"])

    try:
        detected = intent.detect(original_query)
        routes = intent.routes()
        if not detected.get("blocked"):
            for topic in detected.get("topics") or []:
                templates = (routes.get(topic) or {}).get("templates") or []
                if templates:
                    expansions.append(templates[0])
    except Exception:
        pass

    expansions.extend(query_plan.keywords or ())

    clean, seen = [], set()
    for item in expansions:
        item = (item or "").strip()
        if not item or item in seen or item in (original_query or ""):
            continue
        seen.add(item)
        clean.append(item)
    if not clean:
        return None
    normalized = (original_query + " " + " ".join(clean)).strip()
    if not normalized or normalized == original_query:
        return None
    return CorrectiveQuery(
        original_query=original_query,
        normalized_query=normalized,
        deterministic_expansions=tuple(clean),
        reason=("uncertain_%s+candidate_title" % first_decision.reason
                if title_correction else "uncertain_%s" % first_decision.reason),
        retry_count=1,
        title_correction=title_correction,
    )
