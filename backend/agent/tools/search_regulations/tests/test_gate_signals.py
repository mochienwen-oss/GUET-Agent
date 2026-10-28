from dataclasses import fields

from agent.tools.search_regulations.scripts import gate_signals as gs


def _row(doc, idx, rerank, dense=.7, source=None, text="考试作弊按违纪处理"):
    return {"document_id": doc, "chunk_idx": idx,
            "source": source or f"文档{doc}", "text": text,
            "rerank_score": rerank, "score": dense}


def test_scores_margin_dense_and_concentration():
    ranked = [_row(1, 1, .81), _row(1, 2, .77), _row(1, 3, .61, dense=.55),
              _row(2, 1, .70, dense=.66), _row(3, 1, .55, dense=.4)]
    signals = gs.collect(query="考试作弊怎么处理", ranked=ranked)
    assert signals.best_rerank == .81
    assert signals.second_rerank == .77
    assert signals.rerank_margin == .04
    assert signals.best_dense == .7
    assert signals.top_doc_id == "1"
    assert signals.top_doc_count == 3
    assert signals.top_doc_share == .6
    assert signals.candidate_count == 5


def test_dense_bm25_agreement_and_overlap():
    dense = [{"doc_id": "1"}, {"doc_id": "2"}, {"doc_id": "3"}]
    bm25 = [{"doc_id": "1"}, {"doc_id": "2"}, {"doc_id": "4"}]
    signals = gs.collect(query="q", ranked=[_row(1, 1, .8)],
                         dense_rows=dense, bm25_rows=bm25, bm25_available=True)
    assert signals.dense_bm25_top1_agreement is True
    assert signals.dense_bm25_top3_overlap == .5
    assert signals.bm25_candidate_count == 3
    assert signals.bm25_available is True


def test_title_anchor_hit_and_conflict():
    anchors = gs.build_anchor_index(
        sources=["桂电教〔2020〕15号--关于印发《桂林电子科技大学考试管理规定》的通知"])
    hit = gs.collect(
        query="考试管理规定的适用范围是什么？", ranked=[_row(1, 1, .8, source=anchors.titles[0][1])],
        anchors=anchors)
    assert hit.title_anchor
    assert hit.title_anchor_hit is True
    assert hit.scope_conflict is False

    miss = gs.collect(
        query="考试管理规定的适用范围是什么？", ranked=[_row(9, 1, .9, source="另一份文件")],
        anchors=anchors)
    assert miss.title_anchor_hit is False
    assert miss.scope_conflict is True
    assert miss.scope_conflict_kind == "entity_conflict"


def test_entity_anchor_is_independent_from_title_anchor():
    anchors = gs.build_anchor_index(
        sources=["桂林电子科技大学章程(2023年核准稿)"],
        entities=["计算机网络"],
    )
    signals = gs.collect(
        query="计算机网络这门课有什么规定？",
        ranked=[_row(1, 1, .60, source="课程认定办法",
                     text="计算机网络课程可以按规定申请认定。")],
        anchors=anchors,
    )
    assert signals.title_anchor is None
    assert signals.title_anchor_hit is None
    assert signals.entity_anchor == "计算机网络"
    assert signals.entity_anchor_hit is True


def test_entity_anchor_can_be_verified_by_the_source_title():
    anchors = gs.build_anchor_index(entities=["生物医学工程"])
    signals = gs.collect(
        query="生物医学工程专业实践教学课程要多少学分？",
        ranked=[_row(1, 1, .80,
                     source="2025级生物医学工程专业培养方案(主修)",
                     text="实践教学课程要求为32学分。")],
        anchors=anchors,
    )
    assert signals.entity_anchor == "生物医学工程"
    assert signals.entity_anchor_hit is True
    assert signals.scope_conflict_kind is None


def test_entity_anchor_miss_does_not_borrow_title_hit():
    anchors = gs.build_anchor_index(
        sources=["计算机网络课程管理规定"],
        entities=["计算机网络"],
    )
    signals = gs.collect(
        query="计算机网络课程管理规定说了什么？",
        ranked=[_row(1, 1, .80, source="计算机网络课程管理规定",
                     text="本条只包含通用课程管理内容。")],
        anchors=anchors,
    )
    assert signals.title_anchor_hit is True
    assert signals.entity_anchor_hit is False


def test_docno_anchor_hit_and_conflict():
    anchors = gs.build_anchor_index(docnos=["2020-15"])
    hit = gs.collect(query="桂电教〔2020〕15号规定了什么？",
                     ranked=[_row(1, 1, .8, source="桂电教〔2020〕15号--考试管理规定")],
                     anchors=anchors)
    assert hit.docno_anchor == "桂电教〔2020〕15号"
    assert hit.docno_anchor_hit is True
    assert hit.scope_conflict is False

    miss = gs.collect(query="桂电教〔2020〕15号规定了什么？",
                      ranked=[_row(9, 1, .9, source="校教〔2021〕1号--另一份")],
                      anchors=anchors)
    assert miss.docno_anchor_hit is False
    assert miss.scope_conflict_kind == "entity_conflict"


def test_other_school_and_absent_reference_conflicts():
    other = gs.collect(query="清华大学转专业需要什么条件？", ranked=[_row(1, 1, .9)])
    assert other.scope_conflict is True
    assert other.scope_conflict_kind == "other_university"

    # "外校" alone is not a scope conflict: it can describe an in-scope GUET
    # regulation (e.g. staff applying for a visiting year elsewhere).
    in_scope = gs.collect(query="我导师申请去外校访学一年，能批吗？",
                          ranked=[_row(1, 1, .9)])
    assert in_scope.scope_conflict is False

    absent = gs.collect(query="桂电教〔2099〕1号规定了什么？", ranked=[],
                        absent_reference="桂电教〔2099〕1号")
    assert absent.scope_conflict is True
    assert absent.scope_conflict_kind == "absent_reference"
    assert absent.candidate_count == 0


def test_token_coverage_and_empty_inputs():
    covered = gs.collect(query="考试作弊怎么处理",
                         ranked=[_row(1, 1, .8, text="考试作弊的处理规定")])
    assert covered.token_coverage_top1 > 0
    assert covered.token_coverage_top3 == covered.token_coverage_top1

    empty = gs.collect(query="考试作弊", ranked=[])
    assert empty.best_rerank is None
    assert empty.best_dense is None
    assert empty.candidate_count == 0
    assert empty.token_coverage_top1 is None
    assert empty.dense_bm25_top1_agreement is None


def test_unsupported_action_intent_is_runtime_observable():
    assert gs.unsupported_action("帮我写一篇 8000 字的机械设计论文") is True
    assert gs.unsupported_action("怎么绕过考试作弊检测？") is True
    assert gs.unsupported_action("论文字数最低要求是多少？") is False
    assert gs.unsupported_action("他已经写了一篇论文，这样能申报吗？") is False
    assert gs.unsupported_action("转学申请书应该怎么写？") is False


def test_degraded_signal_is_exposed():
    signals = gs.collect(query="q", ranked=[_row(1, 1, .8)],
                         bm25_available=False,
                         degradations=[{"stage": "bm25", "code": "vocab_missing"}])
    assert signals.bm25_available is False
    assert signals.degradations == ({"stage": "bm25", "code": "vocab_missing"},)


def test_signals_cannot_carry_evaluation_labels():
    names = {field.name for field in fields(gs.GateSignals)}
    assert not any("expected" in name or "label" in name or "scenario" in name
                   for name in names)


def test_claim_anchor_requires_specific_terms_and_evidence_shape():
    numeric = gs.claim_anchor(
        "休学累计最长能休多久？", (), ["学生休学累计不得超过2年。"])
    assert numeric["claim_shape"] == "numeric"
    assert numeric["claim_shape_supported"] is True
    assert numeric["claim_anchor_coverage"] >= 0.5
    assert "休学" in numeric["claim_anchor_hits"]

    no_number = gs.claim_anchor(
        "奖学金分几档？", (), ["奖学金评审应当坚持公平公正公开的原则。"])
    assert no_number["claim_shape"] == "numeric"
    assert no_number["claim_shape_supported"] is False

    generic_only = gs.claim_anchor(
        "学生学校规定怎么办？", (), ["学校规定学生应当遵守纪律。"])
    assert not generic_only["claim_anchor_hits"]


def test_claim_terms_drop_conversational_filler_and_overlapping_fragments():
    terms = gs.claim_terms("我想问下，啥情况下咱们学生能申请转学啊？")
    assert "申请" in terms
    assert "转学" in terms
    assert "我想" not in terms
    assert "问下" not in terms
    assert not any(short != long and short in long
                   for short in terms for long in terms)


def test_organization_phrase_stops_at_the_named_organization():
    assert gs.claim_phrases(
        "通识教育专家委员会的办公室设在哪里？") == ("通识教育专家委员会",)
    assert gs.claim_phrases(
        "咱们的学科学术委员会归哪个机构管？") == ("学科学术委员会",)
    assert gs.claim_phrases(
        "我们院准备组建一个学科学术委员会，他能当委员吗？") == ("学科学术委员会",)


def test_best_claim_anchor_may_use_multiple_chunks_from_one_document():
    result = gs.best_claim_anchor(
        "委员连任届数和会议出席比例分别是多少？", (),
        [["委员每届任期四年，可以连任。",
          "委员会会议须有三分之二以上委员出席。"],
         ["其他文件只提到委员。"]],
    )
    assert result["claim_shape"] == "numeric"
    assert result["claim_shape_supported"] is True
    assert "连任" in result["claim_anchor_hits"]
    assert "出席" in result["claim_anchor_hits"]


def test_claim_shapes_are_distinct():
    assert gs.claim_shape("什么情况会被学校退学？") == "condition"
    assert gs.claim_shape("转专业要走什么流程？") == "procedure"
    assert gs.claim_shape("能不能申请转专业？") == "applicability"
    assert gs.claim_shape("非学历教育是什么意思？") == "definition"
    assert gs.claim_shape("宿舍在哪儿？") is None
