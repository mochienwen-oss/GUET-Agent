"""Structure contracts plus an actual in-memory Qdrant -> Registry -> ledger path."""
from copy import deepcopy
import pytest

from agent.core import EvidenceLedger
from agent.tools.search_regulations.scripts.evidence_ops import evidence_units as units
from agent.tools.search_regulations.scripts.recall import retrieval
from agent.tools.search_regulations.scripts.index_ops import indexing


def row(idx, **extra):
    text = f"原始正文{idx}：必要条件与后果。"
    return dict(document_id=1, chunk_idx=idx, chunk_id=f"physical-{idx}",
                source="样本规定", source_sha256="source-hash", chunking_version="structure-evidence/1",
                parent_id="section-a", section_parent_id="section-a", block_type="paragraph",
                heading_path=["第一章", "办理说明"], text=text, snippet=text, score=.9,
                **extra)


def assemble(seeds, rows, **kwargs):
    def fetch(seed, selector):
        return [candidate for candidate in rows if all(candidate.get(k) == v for k, v in selector.items())]
    return units.reassemble(seeds, fetch, max_chars=kwargs.pop("max_chars", 8000), **kwargs)


def test_article_and_legacy_chunks_do_not_fetch_or_mutate():
    article = row(1)
    article["heading_path"] = ["第一章", "第九条"]
    article["next_id"] = "physical-2"
    before = deepcopy(article)
    result, audit = units.reassemble([article], lambda *args: pytest.fail("Article must not expand"), max_chars=8000)
    assert result[0]["text"] == article["text"] and article == before
    assert audit["groups"][0]["kind"] == "article"
    assert units.reassemble([{"text": "旧格式"}], lambda *a: pytest.fail("No I/O"), max_chars=8000)[1] is None


def test_section_adds_only_one_neighbor_each_way_and_keeps_individual_identities():
    seed = row(2, previous_id="physical-1", next_id="physical-3")
    previous, following = row(1), row(3, next_id="physical-4")
    result, audit = assemble([seed], [previous, following, row(4)])
    assert [r["chunk_idx"] for r in result] == [1, 2, 3]
    assert [r["text"] for r in result] == [previous["text"], seed["text"], following["text"]]
    assert result[1]["score"] == .9
    assert not audit["incomplete"]


@pytest.mark.parametrize("field,value", [("document_id", 2), ("source_sha256", "other"),
                                        ("parent_id", "other"), ("section_parent_id", "other"),
                                        ("heading_path", ["另一业务"])])
def test_expansion_cannot_cross_identity_or_section_boundary(field, value):
    seed = row(2, next_id="physical-3")
    neighbor = row(3)
    neighbor[field] = value
    result, audit = assemble([seed], [neighbor])
    assert [r["chunk_idx"] for r in result] == [2]
    assert audit["groups"][0]["warnings"] or audit["groups"][0]["boundary_skips"]


def test_table_first_row_is_not_silently_claimed_as_a_confirmed_header():
    seed, header = row(4), row(0)
    for value, number in [(seed, 5), (header, 1)]:
        value.update(block_type="table", row_number=number, parent_id="table-a")
    result, audit = assemble([seed], [header])
    assert [r["chunk_idx"] for r in result] == [0, 4]
    assert result[0]["evidence_unit"]["relation"] == "table_first_row_candidate"
    assert audit["incomplete"] and not result[0]["evidence_unit"]["boundary_complete"]


def test_field_projected_table_does_not_guess_or_fetch_another_header():
    seed = row(4, row_number=5, table_fields=[{"header": "学分", "text": "3"}])
    seed["block_type"] = "table"
    result, audit = units.reassemble([seed], lambda *a: pytest.fail("Header already projected"), max_chars=8000)
    assert result[0]["text"] == seed["text"] and not audit["incomplete"]


def test_budget_never_cuts_a_chunk_and_keeps_seed_before_context():
    seed = row(2, previous_id="physical-1", next_id="physical-3")
    result, audit = assemble([seed], [row(1), row(3)], max_unit_chars=100)
    assert [r["chunk_idx"] for r in result] == [2]
    assert result[0]["text"] == seed["text"] and audit["incomplete"]
    with pytest.raises(ValueError, match="上下文预算"):
        assemble([seed], [], max_chars=2)


def test_budget_omits_whole_lower_priority_blocks_and_deduplicates():
    first, second = row(1), row(2)
    budget = units._cost(first)
    result, audit = assemble([first, first, second], [], max_chars=budget)
    assert [r["chunk_idx"] for r in result] == [1]
    assert result[0]["text"] == first["text"]
    assert audit["used_chars"] == budget
    assert audit["omitted_chunk_ids"] == [second["chunk_id"]]
    assert audit["incomplete"]


def test_flow_expansion_preserves_metadata_without_crossing_article(monkeypatch):
    from agent.tools.search_regulations.scripts.recall import flow_expand
    seed = row(2)
    related, unrelated = row(3), row(1)
    unrelated["parent_id"] = "another-article"
    monkeypatch.setattr(flow_expand, "flow_expand_enabled", lambda: True)
    monkeypatch.setattr(flow_expand, "_is_flow_chunk", lambda _: True)
    monkeypatch.setattr(indexing, "get_document_chunks", lambda _: [unrelated, related])
    result = flow_expand.expand_flow_sections([seed])
    assert [r["chunk_idx"] for r in result] == [2, 3]
    assert result[1]["chunk_id"] == related["chunk_id"]
    assert result[1]["source_sha256"] == related["source_sha256"]
    assert result[1].get("rerank_score") is None


def test_missing_or_duplicate_link_is_not_top_one_and_cancellation_propagates():
    seed = row(2, next_id="physical-3")
    result, audit = assemble([seed], [row(3), row(3)])
    assert len(result) == 1 and audit["incomplete"]
    def fail(*args):
        raise OSError("read failed")
    _, audit = units.reassemble([seed], fail, max_chars=8000)
    assert "relation_read_failed:OSError" in audit["groups"][0]["warnings"]
    def cancel(*args):
        raise InterruptedError("cancelled")
    with pytest.raises(InterruptedError):
        units.reassemble([seed], cancel, max_chars=8000)


def test_overlapping_windows_do_not_duplicate_a_physical_chunk():
    a, b = row(1, next_id="physical-2"), row(2, previous_id="physical-1")
    result, _ = assemble([a, b], [a, b])
    assert [r["chunk_idx"] for r in result] == [1, 2]


def test_context_budget_keeps_recognition_rule_before_plan_background(monkeypatch):
    plan = dict(row(1), source='2025级人工智能专业培养方案(主修)')
    basis = dict(row(2), document_id=2, source='本科生课程认定暂行办法')
    monkeypatch.setattr(retrieval, 'ensure_collection', lambda: None)
    monkeypatch.setattr(retrieval, '_retrieve_chunks', lambda *a, **kw: [plan, basis])
    monkeypatch.setattr(retrieval, '_rerank', lambda q, rows: [r.update(rerank_score=.99) for r in rows])
    monkeypatch.setattr(retrieval, '_gate_anchors', lambda: None)
    monkeypatch.setattr(retrieval, '_absent_reference', lambda q: '')
    monkeypatch.setattr(retrieval, '_expand_flow', lambda rows: rows)
    monkeypatch.setattr(retrieval, '_follow_refs', lambda rows: rows)
    monkeypatch.setattr(retrieval, '_missing_files', lambda rows: [])
    monkeypatch.setattr(retrieval.settings, 'gate_multi_signal', False)
    monkeypatch.setattr(retrieval.settings, 'max_context_chars', units._cost(basis))
    result = retrieval.retrieve('通识选修课能认定为必修课吗？', top_k=2)
    assert [r['source'] for r in result['contexts']] == [basis['source']]


def test_real_memory_qdrant_payload_filter_registry_and_final_packet(monkeypatch):
    from qdrant_client import QdrantClient, models
    from agent.runtime import registry, grounded_answer
    from agent.core.scope import request_scope
    client = QdrantClient(":memory:")
    name = "evidence-unit-test"
    client.create_collection(name, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    seed = row(2, previous_id="physical-1", next_id="physical-3")
    payloads = [row(1), seed, row(3), row(4)]
    payloads[-1].update(document_id=2, chunk_id="physical-3", source_sha256="other-hash")
    client.upsert(name, points=[models.PointStruct(id=i, vector=[1., 0.], payload=p)
                                for i, p in enumerate(payloads)])
    monkeypatch.setattr(indexing, "_client", client)
    monkeypatch.setattr(retrieval.settings, "qdrant_collection", name)
    monkeypatch.setattr(retrieval, "ensure_collection", lambda: None)
    monkeypatch.setattr(retrieval, "_absent_reference", lambda query: "")
    monkeypatch.setattr(retrieval, "_gate_anchors", lambda: None)
    monkeypatch.setattr(retrieval, "_retrieve_chunks", lambda *a, **kw: [seed])
    def rerank(query, rows):
        for value in rows:
            value["rerank_score"] = .99
    monkeypatch.setattr(retrieval, "_rerank", rerank)
    monkeypatch.setattr(retrieval, "_expand_flow", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_follow_refs", lambda rows: rows)
    monkeypatch.setattr(retrieval, "_missing_files", lambda rows: [])
    try:
        result = registry.call("search_regulations", {"query": "办理有什么规定？"})
        assert result.status == "success"
        assert [s.chunk_id for s in result.sources] == ["1:1", "1:2", "1:3"]
        assert [s.score for s in result.sources] == [None, .99, None]
        assert "同章节窗口" in registry.render(result)
        ledger = EvidenceLedger()
        ledger.add_many(result.tool, {"query": "办理有什么规定？"}, result.sources)
        packet = grounded_answer.evidence_packet({"question": "办理有什么规定？", "request_scope": request_scope("办理有什么规定？"),
                    "evidence": {item.evidence_id: item for item in ledger.items()}, "tool_results": [result]})
        assert len(packet["evidence"]) == 3
        assert all(r["evidence_unit"]["kind"] == "section" for r in packet["evidence"])
        assert [r['evidence_role'] for r in packet['evidence']] == ['candidate', 'supplementary', 'supplementary']
        assert len(packet['evidence_organization']['candidate_ids']) == 1
        assert packet['evidence_organization']['primary_ids'] == []
        assert len(packet['evidence_organization']['supplementary_ids']) == 2
        assert all(r["text"] in [p["text"] for p in payloads[:3]] for r in packet["evidence"])
        boundary = row(5)
        boundary["parent_id"] = "another-section"
        client.upsert(name, points=[models.PointStruct(id=100, vector=[1., 0.], payload=boundary)])
        seed["next_id"] = "physical-5"
        boundary_result = registry.call("search_regulations", {"query": "办理有什么规定？"})
        assert boundary_result.status == "success"
        assert [s.chunk_id for s in boundary_result.sources] == ["1:1", "1:2"]
        assert boundary_result.data["evidence_units"]["groups"][0]["boundary_skips"] == ["next_id"]
        from agent.tools.search_regulations.scripts.evidence_ops.evidence_gate import GateDecision
        monkeypatch.setattr(retrieval, "decide", lambda *a: GateDecision(
            "uncertain", "test_unexposable", "test/1", (), True, False))
        monkeypatch.setattr(retrieval, "_related_evidence", lambda *a: pytest.fail("No expansion of unexposable evidence"))
        closed = retrieval.retrieve_frozen("办理有什么规定？")
        assert closed["gate"]["evidence_exposable"] is False
        assert "evidence_units" not in closed
    finally:
        client.close()
