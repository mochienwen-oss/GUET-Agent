from dataclasses import replace

from agent.tools.search_regulations.scripts.evidence_ops import evidence_gate as eg
from agent.tools.search_regulations.scripts.evidence_ops.gate_signals import GateSignals


def _signals(**overrides):
    base = GateSignals(
        best_rerank=.80, second_rerank=.70, rerank_margin=.10, best_dense=.70,
        dense_bm25_top1_agreement=True, dense_bm25_top3_overlap=.66,
        top_doc_count=5, top_doc_share=1.0, top_doc_id="1",
        title_anchor=None, title_anchor_hit=None,
        docno_anchor=None, docno_anchor_hit=None,
        entity_anchor="考试作弊", entity_anchor_hit=True,
        token_coverage_top1=.6, token_coverage_top3=.7,
        candidate_count=10, bm25_candidate_count=40, bm25_available=True,
        focus_document_id=None, scope_conflict=False, scope_conflict_kind=None,
        absent_reference="", degradations=(), selected_document_ids=("1",),
    )
    return replace(base, **overrides)


POLICY = eg.GatePolicy(version="test/1", high_support=.72, low_maybe=.54,
                       require_semantic_recovery=True)


def test_supported_gray_irrelevant_empty_are_all_reachable():
    assert eg.decide(_signals(), POLICY).status == "supported"
    assert eg.decide(_signals(best_rerank=.60), POLICY).status == "uncertain"
    assert eg.decide(_signals(best_rerank=.50, best_dense=.40,
                             dense_bm25_top1_agreement=False,
                             dense_bm25_top3_overlap=0.0, top_doc_count=1,
                             token_coverage_top1=.1), POLICY).status == "irrelevant"
    assert eg.decide(_signals(candidate_count=0, best_rerank=None), POLICY).status == "empty"
    assert eg.decide(_signals(absent_reference="桂电教〔2099〕1号",
                             candidate_count=0, best_rerank=None), POLICY).status == "empty"


def test_high_score_does_not_override_scope_or_entity_conflict():
    other = eg.decide(_signals(best_rerank=.95, scope_conflict=True,
                              scope_conflict_kind="other_university"), POLICY)
    assert other.status == "irrelevant"
    assert other.reason == "scope_conflict"
    entity = eg.decide(_signals(best_rerank=.95, scope_conflict=True,
                               scope_conflict_kind="entity_conflict"), POLICY)
    assert entity.status == "irrelevant"


def test_low_score_needs_two_independent_signals():
    one = eg.decide(_signals(
        best_rerank=.50, dense_bm25_top1_agreement=True,
        dense_bm25_top3_overlap=0.0, top_doc_count=1,
        token_coverage_top1=.1, token_coverage_top3=.1,
        best_dense=.4, title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None), POLICY)
    assert one.status == "irrelevant"
    two_retrieval_only = eg.decide(_signals(
        best_rerank=.50, dense_bm25_top1_agreement=True,
        dense_bm25_top3_overlap=.5, top_doc_count=1,
        token_coverage_top1=.1, token_coverage_top3=.1,
        best_dense=.4, title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None), POLICY)
    assert two_retrieval_only.status == "uncertain"
    assert two_retrieval_only.reason == "retrieval_consistency_only"
    assert two_retrieval_only.evidence_exposable is False

    semantic_and_retrieval = eg.decide(_signals(
        best_rerank=.50, dense_bm25_top1_agreement=True,
        dense_bm25_top3_overlap=0.0, top_doc_count=1,
        token_coverage_top1=.1, token_coverage_top3=.1,
        best_dense=.4, entity_anchor="计算机网络", entity_anchor_hit=True,
        title_anchor_hit=None, docno_anchor_hit=None), POLICY)
    assert semantic_and_retrieval.status == "uncertain"
    assert semantic_and_retrieval.reason == "multi_signal_recovery"
    assert semantic_and_retrieval.evidence_exposable is True
    assert "entity_anchor" in semantic_and_retrieval.contributing_signals
    assert "dense_bm25_agreement" in semantic_and_retrieval.contributing_signals


def test_gray_band_without_semantic_anchor_is_internal_retry_only():
    decision = eg.decide(_signals(
        best_rerank=.64, dense_bm25_top1_agreement=True,
        dense_bm25_top3_overlap=1.0, top_doc_count=5,
        token_coverage_top1=.1, best_dense=.7,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor_hit=None), POLICY)
    assert decision.status == "uncertain"
    assert decision.reason == "gray_band_weak_support"
    assert decision.retry_allowed is True
    assert decision.evidence_exposable is False


def test_aux_focus_is_independent_of_top_document_agreement():
    # Isolate retrieval-consistency signals: the synthetic base entity anchor
    # would otherwise be an independent semantic anchor.
    decision = eg.decide(_signals(
        best_rerank=.50, focus_document_id="9", top_doc_id="1",
        dense_bm25_top1_agreement=False, dense_bm25_top3_overlap=0.0,
        top_doc_count=1, token_coverage_top1=.1, best_dense=.65,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None), POLICY)
    assert decision.status == "uncertain"
    assert decision.evidence_exposable is False
    assert "aux_focus" in decision.contributing_signals
    assert "dense_strength" in decision.contributing_signals


def test_score_proximity_alone_never_counts():
    near = eg.decide(_signals(
        best_rerank=.5399, dense_bm25_top1_agreement=False,
        dense_bm25_top3_overlap=0.0, top_doc_count=1,
        token_coverage_top1=.1, best_dense=.3), POLICY)
    assert near.status == "irrelevant"


def test_high_score_with_weak_support_is_uncertain_when_required():
    weak = dict(best_rerank=.80, dense_bm25_top1_agreement=False,
                dense_bm25_top3_overlap=0.0, top_doc_count=1,
                token_coverage_top1=.1, best_dense=.3,
                title_anchor_hit=None, docno_anchor_hit=None,
                entity_anchor=None, entity_anchor_hit=None)
    assert eg.decide(_signals(**weak), POLICY).status == "uncertain"
    loose = eg.GatePolicy(version="test/2", high_support=.72, low_maybe=.54,
                          require_support_signal=False)
    assert eg.decide(_signals(**weak), loose).status == "supported"


def test_degraded_reranker_never_supports():
    decision = eg.decide(_signals(
        best_rerank=.99,
        degradations=({"stage": "rerank", "code": "fallback"},)), POLICY)
    assert decision.status == "uncertain"
    assert decision.reason == "signal_degraded"
    assert decision.retry_allowed is True
    assert decision.evidence_exposable is False


def test_only_uncertain_allows_retry():
    assert eg.decide(_signals(), POLICY).retry_allowed is False
    assert eg.decide(_signals(best_rerank=.60), POLICY).retry_allowed is True
    assert eg.decide(_signals(best_rerank=.50, best_dense=.3, top_doc_count=1,
                             dense_bm25_top1_agreement=False,
                             dense_bm25_top3_overlap=0.0,
                             token_coverage_top1=.1), POLICY).retry_allowed is False
    assert eg.decide(_signals(candidate_count=0), POLICY).retry_allowed is False


def test_claim_anchor_is_experimental_only_and_shape_guarded():
    claim = dict(claim_anchor_terms=("休学", "累计"),
                 claim_anchor_hits=("休学", "累计"),
                 claim_anchor_coverage=1.0,
                 claim_shape="numeric", claim_shape_supported=True)
    experimental = eg.decide(_signals(
        best_rerank=.64, token_coverage_top1=.1,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None, **claim), POLICY)
    assert "claim_anchor" in experimental.contributing_signals
    assert experimental.evidence_exposable is True

    # POLICY_V1 must be byte-for-byte behaviorally unchanged.
    v1 = eg.decide(_signals(
        best_rerank=.64, token_coverage_top1=.1,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None, **claim), eg.POLICY_V1)
    assert "claim_anchor" not in v1.contributing_signals

    # A numeric question whose evidence has no number cannot claim support.
    refused = eg.decide(_signals(
        best_rerank=.64, token_coverage_top1=.1,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None,
        **dict(claim, claim_shape_supported=False)), POLICY)
    assert "claim_anchor" not in refused.contributing_signals
    assert refused.reason == "gray_band_weak_support"
    assert refused.evidence_exposable is False


def test_gray_band_claim_anchor_is_directly_exposable_without_ranker_agreement():
    claim = dict(claim_anchor_terms=("无故缺席", "委员", "终止"),
                 claim_anchor_hits=("无故缺席", "委员", "终止"),
                 claim_anchor_coverage=1.0,
                 claim_shape="numeric", claim_shape_supported=True)
    decision = eg.decide(_signals(
        best_rerank=.69, best_dense=.3,
        dense_bm25_top1_agreement=False, dense_bm25_top3_overlap=0.0,
        top_doc_count=1, token_coverage_top1=.1,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None, **claim), POLICY)
    assert decision.status == "uncertain"
    assert decision.reason == "gray_band"
    assert decision.evidence_exposable is True

    refused = eg.decide(_signals(
        best_rerank=.69, best_dense=.3,
        dense_bm25_top1_agreement=False, dense_bm25_top3_overlap=0.0,
        top_doc_count=1, token_coverage_top1=.1,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None,
        **dict(claim, claim_shape_supported=False)), POLICY)
    assert refused.evidence_exposable is False


def test_token_overlap_is_not_a_semantic_anchor_for_recovery():
    decision = eg.decide(_signals(
        best_rerank=.64, token_coverage_top1=.9,
        dense_bm25_top1_agreement=True, dense_bm25_top3_overlap=0.0,
        top_doc_count=1, best_dense=.3,
        title_anchor_hit=None, docno_anchor_hit=None,
        entity_anchor=None, entity_anchor_hit=None), POLICY)
    assert "token_coverage" in decision.contributing_signals
    assert decision.reason == "gray_band_weak_support"
    assert decision.evidence_exposable is False


def test_claim_shape_contradiction_always_blocks_claim_anchor():
    loose_shape_policy = replace(POLICY, require_claim_shape_support=False)
    signals = _signals(
        claim_anchor_terms=("论文", "字数"),
        claim_anchor_hits=("论文", "字数"),
        claim_anchor_coverage=1.0,
        claim_shape="numeric", claim_shape_supported=False)
    assert eg.claim_anchor_supported(signals, loose_shape_policy) is False


def test_claim_shape_contradiction_blocks_topic_only_semantic_anchors():
    signals = _signals(
        title_anchor="机械设计", title_anchor_hit=True,
        entity_anchor="机械设计", entity_anchor_hit=True,
        claim_shape="numeric", claim_shape_supported=False)
    assert eg.semantic_anchor_signals(signals, POLICY) == ()

    decision = eg.decide(replace(signals, best_rerank=.64), POLICY)
    assert decision.reason == "gray_band_weak_support"
    assert decision.evidence_exposable is False


def test_generic_claim_can_use_terms_or_an_exact_reviewed_phrase_anchor():
    unanchored = _signals(
        claim_anchor_terms=("考试", "作弊"),
        claim_anchor_hits=("考试", "作弊"),
        claim_anchor_coverage=1.0,
        claim_shape="generic", claim_shape_supported=None,
        claim_anchor_phrases=(), claim_anchor_phrase_hits=(),
        claim_anchor_phrase_supported=None)
    assert eg.claim_anchor_supported(unanchored, POLICY) is True

    anchored = replace(
        unanchored,
        claim_anchor_phrases=("人工智能学院",),
        claim_anchor_phrase_hits=("人工智能学院",),
        claim_anchor_phrase_supported=True,
    )
    assert eg.claim_anchor_supported(anchored, POLICY) is True


def test_experimental_policy_rejects_unsupported_action_intent():
    signals = _signals(scope_conflict=True,
                       scope_conflict_kind="unsupported_action")
    decision = eg.decide(signals, POLICY)
    assert decision.status == "irrelevant"
    assert decision.reason == "scope_conflict"

    # The production v1 policy remains behaviorally frozen during calibration.
    assert eg.decide(signals, eg.POLICY_V1).status == "supported"
