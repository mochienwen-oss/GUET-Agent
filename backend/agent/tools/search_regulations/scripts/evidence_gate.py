# -*- coding: utf-8 -*-
"""Pure deterministic evidence gate.

The gate consumes only GateSignals produced by one real retrieval call. It
never reads expected answers, expected documents, labels, scenario ids, or
evaluation membership.
"""
from dataclasses import asdict, dataclass

from .gate_signals import GateSignals
from .rerank_scores import SCORE_VERSION, LEGACY_SCORE_VERSION, threshold_on_single_scale


@dataclass(frozen=True)
class GatePolicy:
    version: str
    high_support: float = 0.72
    low_maybe: float = 0.54
    require_support_signal: bool = True
    min_positive_signals: int = 2
    dense_strong: float = 0.60
    token_coverage_min: float = 0.40
    concentration_min: int = 3
    top3_overlap_min: float = 0.34
    require_semantic_recovery: bool = False
    claim_anchor_coverage_min: float = 0.50
    claim_anchor_min_hits: int = 2
    require_claim_shape_support: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# Production default selected offline on 2026-10-18 (see
# _work/phase7/evidence-gate-v1/selected-policy.json). It passed calibration and
# the independent holdout: zero supported OOS/corpus-absent cases, no answerable
# rejection on calibration, and no loss of returned-evidence recall.
POLICY_V1 = GatePolicy(
    version="evidence-gate/1",
    high_support=0.70,
    low_maybe=0.54,
    require_support_signal=False,
    min_positive_signals=2,
    dense_strong=0.60,
    token_coverage_min=0.45,
    concentration_min=4,
    top3_overlap_min=0.34,
)


@dataclass(frozen=True)
class GateDecision:
    status: str
    reason: str
    policy_version: str
    contributing_signals: tuple[str, ...]
    retry_allowed: bool
    evidence_exposable: bool

    def to_dict(self) -> dict:
        data = asdict(self)
        data["contributing_signals"] = list(self.contributing_signals)
        return data


def claim_anchor_supported(signals: GateSignals, policy: GatePolicy) -> bool:
    """A specific query-to-evidence claim anchor, not ranker agreement.

    The claim must cover enough query terms and, when the question asserts a
    shape (numeric/condition/procedure/applicability/definition), the evidence
    itself must carry that shape. Generic words are already removed upstream.
    """
    coverage = signals.claim_anchor_coverage
    hits = signals.claim_anchor_hits
    if coverage is None or coverage < policy.claim_anchor_coverage_min:
        return False
    if len(hits) < policy.claim_anchor_min_hits:
        # One specific term is allowed only when the whole claim is that term.
        if not (len(signals.claim_anchor_terms) == 1 and len(hits) == 1
                and len(hits[0]) >= 2):
            return False
    # A directly observed shape contradiction is never optional. The policy
    # switch only controls whether missing shape evidence is required.
    if signals.claim_shape_supported is False:
        return False
    if (policy.require_claim_shape_support
            and signals.claim_shape not in (None, "generic")
            and signals.claim_shape_supported is not True):
        return False
    if signals.claim_anchor_phrase_supported is False:
        return False
    return True


def independent_positive_signals(signals: GateSignals,
                                 policy: GatePolicy) -> tuple[str, ...]:
    """Signals that independently support evidence; score proximity is absent."""
    found = []
    if signals.title_anchor_hit is True:
        found.append("title_anchor")
    if signals.docno_anchor_hit is True:
        found.append("docno_anchor")
    if policy.require_semantic_recovery and signals.entity_anchor_hit is True:
        found.append("entity_anchor")
    if policy.require_semantic_recovery and claim_anchor_supported(signals, policy):
        found.append("claim_anchor")
    if signals.dense_bm25_top1_agreement is True:
        found.append("dense_bm25_agreement")
    if (signals.dense_bm25_top3_overlap is not None
            and signals.dense_bm25_top3_overlap >= policy.top3_overlap_min):
        found.append("dense_bm25_overlap")
    if signals.top_doc_count >= policy.concentration_min:
        found.append("document_concentration")
    if (signals.token_coverage_top1 is not None
            and signals.token_coverage_top1 >= policy.token_coverage_min):
        found.append("token_coverage")
    if (signals.best_dense is not None
            and signals.best_dense >= policy.dense_strong):
        found.append("dense_strength")
    if signals.focus_document_id is not None:
        # The reviewed aux route converged on one document. This is the same
        # observable the legacy multi_signal path used; top-document agreement
        # is a separate signal (document_concentration).
        found.append("aux_focus")
    return tuple(sorted(found))


def semantic_anchor_signals(signals: GateSignals,
                            policy: GatePolicy) -> tuple[str, ...]:
    """Reviewed query-to-evidence anchors, independent of rank consistency."""
    if signals.claim_shape_supported is False:
        return ()
    found = []
    if signals.title_anchor_hit is True:
        found.append("title_anchor")
    if signals.docno_anchor_hit is True:
        found.append("docno_anchor")
    if signals.entity_anchor_hit is True:
        found.append("entity_anchor")
    if policy.require_semantic_recovery and claim_anchor_supported(signals, policy):
        found.append("claim_anchor")
    return tuple(sorted(found))


def retrieval_consistency_signals(signals: GateSignals,
                                  policy: GatePolicy) -> tuple[str, ...]:
    """Ranker/recall agreement signals; none is semantic grounding alone."""
    all_positive = independent_positive_signals(signals, policy)
    semantic = set(semantic_anchor_signals(signals, policy))
    return tuple(item for item in all_positive if item not in semantic)


def _strong_support(signals: GateSignals, policy: GatePolicy) -> bool:
    """At least one high-specificity signal, so a high score is not enough."""
    return bool(semantic_anchor_signals(signals, policy))


def _decision(status, reason, policy, contributing=(), retry=False,
              exposable=False):
    return GateDecision(status=status, reason=reason,
                        policy_version=policy.version,
                        contributing_signals=tuple(contributing),
                        retry_allowed=bool(retry),
                        evidence_exposable=bool(exposable))


def decide(signals: GateSignals, policy: GatePolicy) -> GateDecision:
    """Deterministic four-state decision. Unknown inputs fail closed."""
    if signals.absent_reference:
        return _decision("empty", "absent_reference", policy)
    if signals.candidate_count == 0:
        return _decision("empty", "no_candidates", policy)
    if signals.scope_conflict_kind in ("other_university", "entity_conflict"):
        return _decision("irrelevant", "scope_conflict", policy,
                         contributing=(signals.scope_conflict_kind,))
    if (signals.scope_conflict_kind == "entity_anchor_conflict"
            and policy.require_semantic_recovery):
        return _decision("irrelevant", "scope_conflict", policy,
                         contributing=(signals.scope_conflict_kind,))
    if (signals.scope_conflict_kind == "unsupported_action"
            and policy.require_semantic_recovery):
        return _decision("irrelevant", "scope_conflict", policy,
                         contributing=(signals.scope_conflict_kind,))
    degraded = any(item.get("stage") == "rerank" and item.get("code") == "fallback"
                   for item in signals.degradations)
    positives = independent_positive_signals(signals, policy)
    semantic = semantic_anchor_signals(signals, policy)
    consistency = retrieval_consistency_signals(signals, policy)
    recoverable = (bool(semantic and consistency
                        and len(positives) >= policy.min_positive_signals)
                   if policy.require_semantic_recovery
                   else len(positives) >= policy.min_positive_signals)
    retryable_consistency = len(consistency) >= policy.min_positive_signals
    if degraded:
        return _decision("uncertain", "signal_degraded", policy,
                         contributing=positives, retry=True, exposable=False)
    best = signals.best_rerank
    if best is None:
        if recoverable:
            return _decision("uncertain", "multi_signal_recovery", policy,
                             contributing=positives, retry=True,
                             exposable=not policy.require_semantic_recovery or bool(semantic))
        return _decision("irrelevant", "no_rerank_score", policy,
                         contributing=positives)
    # Existing policy artifacts keep their recorded units. New live scores use
    # one sigmoid; convert thresholds, never apply sigmoid to a score again.
    if signals.rerank_score_version not in (SCORE_VERSION, LEGACY_SCORE_VERSION):
        raise ValueError("未知 reranker 分数口径")
    high, low = policy.high_support, policy.low_maybe
    if signals.rerank_score_version == SCORE_VERSION:
        high, low = map(threshold_on_single_scale, (high, low))
    if best >= high:
        if policy.require_support_signal and not _strong_support(signals, policy):
            return _decision("uncertain", "high_score_weak_support", policy,
                             contributing=positives, retry=True,
                             exposable=False)
        return _decision("supported", "supported_high", policy,
                         contributing=positives, exposable=True)
    if best >= low:
        if not policy.require_semantic_recovery:
            return _decision("uncertain", "gray_band", policy,
                             contributing=positives, retry=True,
                             exposable=True)
        # A deterministic query-to-evidence anchor is direct support in the
        # gray band; ranker agreement is corroboration, not a prerequisite.
        if semantic:
            return _decision("uncertain", "gray_band", policy,
                             contributing=positives, retry=True,
                             exposable=True)
        return _decision("uncertain", "gray_band_weak_support", policy,
                         contributing=positives, retry=True, exposable=False)
    if recoverable:
        return _decision("uncertain", "multi_signal_recovery", policy,
                         contributing=positives, retry=True,
                         exposable=True)
    if policy.require_semantic_recovery and retryable_consistency:
        return _decision("uncertain", "retrieval_consistency_only", policy,
                         contributing=positives, retry=True, exposable=False)
    return _decision("irrelevant", "low_score", policy, contributing=positives)
