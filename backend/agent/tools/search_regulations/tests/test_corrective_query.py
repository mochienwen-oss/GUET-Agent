from agent.tools.search_regulations.scripts import corrective_query as cq
from agent.tools.search_regulations.scripts import intent
from agent.tools.search_regulations.scripts.evidence_gate import GateDecision
from agent.tools.search_regulations.scripts.gate_signals import build_anchor_index
from agent.tools.search_regulations.scripts.query_plan import QueryPlan


def _decision(status="uncertain", retry=True, reason="gray_band", exposable=True):
    return GateDecision(status=status, reason=reason, policy_version="test/1",
                        contributing_signals=(), retry_allowed=retry,
                        evidence_exposable=exposable)


def _plan(query, keywords=()):
    return QueryPlan(semantic_query=query, sparse_query=query, keywords=tuple(keywords))


def test_only_uncertain_is_corrected():
    plan = _plan("转专业有什么要求", ["转专业"])
    for status in ("supported", "irrelevant", "empty"):
        assert cq.build_corrective_query("转专业有什么要求", plan,
                                         _decision(status)) is None
    assert cq.build_corrective_query("转专业有什么要求", plan,
                                     _decision("uncertain", retry=False)) is None
    assert cq.build_corrective_query("转专业有什么要求", plan, None) is None


def test_title_anchor_expansion_uses_reviewed_title(monkeypatch):
    anchors = build_anchor_index(
        sources=["桂电教〔2020〕15号--关于印发《桂林电子科技大学考试管理规定》的通知"])
    monkeypatch.setattr(cq, "_ANCHORS", anchors)
    monkeypatch.setattr(intent, "detect", lambda q: {"topics": [], "blocked": ""})
    monkeypatch.setattr(intent, "routes", lambda: {})
    # Spaced variant: the normalized anchor matches, but the canonical short
    # title is not a literal substring, so the reviewed title is a real expansion.
    query = "考试 管理 规定适用于哪些情况？"
    result = cq.build_corrective_query(query, _plan(query), _decision())
    assert result is not None
    assert result.original_query == query
    assert result.normalized_query.startswith(query)
    assert "考试管理规定" in result.normalized_query
    assert result.retry_count == 1
    assert result.reason == "uncertain_gray_band"


def test_route_template_and_keywords_are_reviewed_sources(monkeypatch):
    monkeypatch.setattr(cq, "_ANCHORS", build_anchor_index())
    template = "桂林电子科技大学本科生专业分流及转专业管理办法"
    monkeypatch.setattr(intent, "detect", lambda q: {"topics": ["转专业"], "blocked": ""})
    monkeypatch.setattr(intent, "routes", lambda: {"转专业": {"templates": [template]}})
    query = "我想换个专业读，要满足哪些条件？"
    result = cq.build_corrective_query(query, _plan(query, ["转专业"]), _decision())
    assert result is not None
    assert template in result.deterministic_expansions
    assert "转专业" in result.deterministic_expansions
    for item in result.deterministic_expansions:
        assert item in result.normalized_query


def test_no_expansion_returns_none_and_creates_no_entity(monkeypatch):
    monkeypatch.setattr(cq, "_ANCHORS", build_anchor_index())
    monkeypatch.setattr(intent, "detect", lambda q: {"topics": [], "blocked": ""})
    monkeypatch.setattr(intent, "routes", lambda: {})
    query = "这个怎么弄？"
    assert cq.build_corrective_query(query, _plan(query), _decision()) is None


def test_blocked_or_empty_topics_create_nothing(monkeypatch):
    monkeypatch.setattr(cq, "_ANCHORS", build_anchor_index())
    monkeypatch.setattr(intent, "detect",
                        lambda q: {"topics": ["转专业"], "blocked": "外校"})
    monkeypatch.setattr(intent, "routes", lambda: {"转专业": {"templates": ["某模板"]}})
    query = "清华大学转专业"
    assert cq.build_corrective_query(query, _plan(query), _decision()) is None


def _candidate_signals(title, **overrides):
    value = {
        "evidence_exposable": False,
        "absent_reference": "",
        "scope_conflict_kind": None,
        "bm25_available": True,
        "degradations": [],
        "dense_bm25_top1_agreement": True,
        "dense_bm25_top3_overlap": 0.6,
        "top_doc_count": 3,
        "top_document_title": title,
    }
    value.update(overrides)
    return value


def test_candidate_title_correction_requires_every_guard(monkeypatch):
    title = "关于印发《桂林电子科技大学考试管理规定》的通知"
    monkeypatch.setattr(cq, "_ANCHORS", build_anchor_index(sources=[title]))
    monkeypatch.setattr(intent, "detect", lambda q: {"topics": [], "blocked": ""})
    monkeypatch.setattr(intent, "routes", lambda: {})
    query = "考试作弊怎么处理？"
    decision = _decision(exposable=False)
    result = cq.build_corrective_query(
        query, _plan(query), decision, signals=_candidate_signals(title))
    assert result is not None
    assert result.original_query == query
    assert result.title_correction == title
    assert title in result.normalized_query

    blocked = [
        {"evidence_exposable": True},
        {"absent_reference": "桂电教〔2099〕1号"},
        {"scope_conflict_kind": "other_university"},
        {"scope_conflict_kind": "entity_conflict"},
        {"bm25_available": False},
        {"degradations": [{"stage": "rerank", "code": "fallback"}]},
        {"dense_bm25_top1_agreement": False, "dense_bm25_top3_overlap": 0.0,
         "top_doc_count": 1},
        {"top_document_title": "目录里没有的标题"},
    ]
    for override in blocked:
        assert cq.build_corrective_query(
            query, _plan(query), decision,
            signals=_candidate_signals(title, **override)) is None

    # The first decision itself must be non-exposable.
    assert cq.build_corrective_query(
        query, _plan(query), _decision(exposable=True),
        signals=_candidate_signals(title)) is None

    # A title that is not a unique exact catalog entry is not corrected.
    assert cq.build_corrective_query(
        query, _plan(query), _decision(exposable=False),
        signals=_candidate_signals(title + "（2024年修订）")) is None
