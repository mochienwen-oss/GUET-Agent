"""Public search_regulations tool and the only maintenance facade for its RAG."""
import unicodedata

from langchain_core.tools import tool

from agent.core import Source, ToolResult
from agent.core.evidence import chunk_id_of, text_hash

from .schemas import SearchRegulationsArgs
from agent.tools.search_regulations.scripts.index_ops import indexing
from agent.tools.search_regulations.scripts.recall import retrieval
from agent.tools.search_regulations.scripts.querying.corrective_query import build_corrective_query
from agent.tools.search_regulations.scripts.evidence_ops.evidence_gate import POLICY_V1, GateDecision, GatePolicy
from agent.tools.search_regulations.scripts.querying.query_plan import build_query_plan
from agent.tools.search_regulations.scripts.inspection.trace import RetrievalTrace
from agent.tools.search_regulations.scripts.evidence_ops.answer_coverage import coverage_signal


# ★ 冻结：逐字等于 LoRA 训练列里的 description（与 schemas.py 的 PARAMETERS 同一份来源）。
DESCRIPTION = (
    "检索桂林电子科技大学的规章制度条文（学籍、学业预警、奖助、教务等）。"
    "当问题涉及学校规定、政策、办事流程时用它。"
)


# ★★ reason → ToolStatus 的**唯一映射表**。本文件只做映射：相关性的真相来自 retrieval
#    已经产出的 reason，这里**不复制阈值、不看 rerank_score 再判一次、不读 trace 反推**。
#
#    · no_evidence       没有可用候选，或用户点名的资料未收录。
#    · irrelevant_result 检索**已经找到过候选**，但相关性门判定这些内容不能支持当前问题。
#    · success           有内容通过当前相关性门（本文件只看 blocks 是否非空）。
#
#    ⚠️ 三条口径的边界（写进设计文档，模型不得越界扩写）：
#      - ToolStatus 不是 HTTP/进程状态，它是**证据充分度**状态；
#      - irrelevant_result ≠ 工具执行失败，也 ≠ 学校没有规定；
#      - no_evidence 同样 ≠ 学校没有规定。
#    两者都只能说「这次检索没给出可用依据」，不得被扩写成全局否定结论。
_NO_EVIDENCE_REASONS = frozenset({
    "empty",            # 召回池为空：语料里没有可用候选
    "no_dense_signal",  # 候选并集为空（无一条带稠密分）→ 同上；非空时走 blocks 分支成 success
})
_IRRELEVANT_REASONS = frozenset({
    "rerank_low",  # 候选存在，被精排门拒绝
    "score_low",   # 候选存在，被稠密分门拒绝（未启用精排的等价路径）
    "grader_no",   # 候选存在，被 LLM 评估器判为不相关
})
_ABSENT_PREFIX = "absent:"  # absent:<文档>：用户点名的资料不在语料里 → 未收录

# Evidence-gate state → ToolStatus. This is the only mapping used when the
# retrieval result carries a gate decision; it is exhaustive and fails closed.
_GATE_STATE_TO_STATUS = {
    "supported": "success",
    "uncertain": "partial_evidence",
    "irrelevant": "irrelevant_result",
    "empty": "no_evidence",
}


def _unknown_gate_state_error(state) -> ValueError:
    return ValueError(
        "search_regulations: 未知的证据门状态=%r，无法映射到 ToolStatus。"
        "已知状态=%s。状态与 ToolStatus 是穷举契约；未知状态必须经 Registry "
        "转换为 tool_failed，不得静默降级。" % (state, sorted(_GATE_STATE_TO_STATUS)))


def _unknown_reason_error(reason) -> ValueError:
    """空结果 + 表外 reason：**当场抛错**，不返回 ToolResult。

    这是**穷举契约**，不是防御性兜底：空结果只允许落在上面两张表里。表外值（未知字符串 /
    `None` / 空串）意味着检索加了新的空结果出口而映射没跟上 —— 属**契约漂移**。
    此时**不得静默降级成 `no_evidence`**：那会把「有内容但不相关」说成「库里没有」，
    正是本任务要修的那类语义丢失；也不得自行构造 `tool_failed`（那是 Registry 的边界职责）。

    Registry 的既有边界（`registry.call` 的 `except Exception`）会把这个异常统一转成
    `ok=False / status=tool_failed / error.code=tool_failed`，并对模型渲染统一的故障措辞
    （「不代表学校没有相关规定」）—— 与工具执行失败走同一条路，不新增第二套失败语义。
    """
    return ValueError(
        "search_regulations: 未知的检索 reason=%r，无法映射到 ToolStatus。"
        "已知 no_evidence reasons=%s；已知 irrelevant_result reasons=%s；"
        "或以 %r 开头表示指定资料未收录。"
        "空结果 reason 与 ToolStatus 是穷举契约：检索新增空结果 reason 时必须同步扩展映射与测试。"
        "不得静默归为 no_evidence。"
        % (reason, sorted(_NO_EVIDENCE_REASONS), sorted(_IRRELEVANT_REASONS), _ABSENT_PREFIX)
    )


def _score(context: dict):
    value = context.get("rerank_score")
    return value if value is not None else context.get("score")


def _source(context: dict) -> Source:
    text = (context.get("text") or "").strip()
    return Source(
        doc_id=str(context["document_id"]) if context.get("document_id") is not None else None,
        chunk_id=chunk_id_of(context.get("document_id"), context.get("chunk_idx")),
        title=context.get("source") or "",
        heading_path=list(context.get("heading_path") or []),
        text=text,
        text_hash=text_hash(text),
        score=_score(context),
        source_span=list(context.get("source_span") or []),
        source_sha256=context.get("source_sha256") or "",
        block_type=context.get("block_type") or "",
        row_number=context.get("row_number"),
    )


def scoped_document_id(trace_data: dict, title_correction: str | None):
    """Unique real document_id whose first-round source equals the title.

    The title is only used to locate a row that the real first-round retrieval
    already returned; document_id always comes from that row's payload.
    """
    if not title_correction:
        return None
    wanted = unicodedata.normalize("NFKC", title_correction).strip()
    if not wanted:
        return None
    rows = ((trace_data or {}).get("stages") or {}).get("reranked") or []
    doc_ids = set()
    for row in rows:
        title = unicodedata.normalize("NFKC", row.get("title") or "").strip()
        if title != wanted:
            continue
        doc_id = row.get("doc_id")
        if doc_id is None:
            continue
        try:
            doc_ids.add(int(doc_id))
        except (TypeError, ValueError):
            continue
    return doc_ids.pop() if len(doc_ids) == 1 else None


def g2_1_eligible(policy: GatePolicy, signals: dict,
                  title_correction: str | None, document_id,
                  document_scoped_recovery: bool) -> bool:
    """All G2.1 entry guards; fail closed on every missing condition."""
    if not document_scoped_recovery:
        return False
    if policy is None or not policy.require_semantic_recovery:
        return False
    if policy.version == POLICY_V1.version:
        return False
    if not title_correction or document_id is None:
        return False
    signals = signals or {}
    if signals.get("absent_reference"):
        return False
    if signals.get("scope_conflict_kind"):
        return False
    return True


def _close_unexposable(raw: dict, trace: RetrievalTrace, first_gate: dict) -> dict:
    """Turn unresolved internal uncertainty into a public fail-closed result."""
    closed = dict(raw)
    closed["contexts"] = []
    closed["tier"] = "low"
    closed["reason"] = "uncertain_unresolved"
    closed["first_gate"] = first_gate
    gate = dict(raw.get("gate") or {})
    gate.update({
        "status": "irrelevant",
        "reason": "uncertain_unresolved",
        "retry_allowed": False,
        "evidence_exposable": False,
    })
    closed["gate"] = gate
    previous = dict(trace.gate)
    trace.record_gate(
        "irrelevant", "uncertain_unresolved", previous.get("best_score"),
        policy_version=gate.get("policy_version"),
        signals=previous.get("signals"),
        contributing_signals=list(gate.get("contributing_signals") or ()),
        retry_allowed=False, evidence_exposable=False)
    return closed


def search_regulations_result(query: str, keywords: list[str] | None = None,
                              prev_query: str | None = None,
                              *, policy: GatePolicy = POLICY_V1,
                              document_scoped_recovery: bool = False, request_task=None) -> ToolResult:
    keywords = list(keywords or [])
    trace = RetrievalTrace.start(query)
    final_trace = trace
    first_trace_data = None
    scoped_used = False
    task_args = {"request_task": request_task} if request_task is not None else {}
    # 空关键词走旧调用形状，保证旧调用方/测试替身与旧检索行为逐字兼容。
    raw = (retrieval.retrieve_frozen(query, prev_query=prev_query, trace=trace,
                                     keywords=keywords, policy=policy, **task_args)
           if keywords else retrieval.retrieve_frozen(query, prev_query=prev_query,
                                                       trace=trace, policy=policy, **task_args))
    gate = raw.get("gate") or {}
    if gate.get("retry_allowed") and not (raw.get("target_record") or {}).get("recovery_used"):
        first_decision = GateDecision(
            status=gate.get("status"), reason=gate.get("reason"),
            policy_version=gate.get("policy_version") or "",
            contributing_signals=tuple(gate.get("contributing_signals") or ()),
            retry_allowed=True,
            evidence_exposable=gate.get("evidence_exposable", True))
        plan = build_query_plan(query, keywords, prev_query)
        signal_payload = raw.get("signals")
        if signal_payload is not None:
            signal_payload = dict(signal_payload)
            signal_payload["evidence_exposable"] = first_decision.evidence_exposable
        corrective = build_corrective_query(
            query, plan, first_decision, signals=signal_payload)
        if corrective is not None:
            if trace is not None:
                trace.record_candidates("first_final", raw.get("contexts") or [])
            scoped_id = scoped_document_id(trace.to_dict(),
                                           corrective.title_correction)
            use_scoped = g2_1_eligible(
                policy, raw.get("signals") or {}, corrective.title_correction,
                scoped_id, document_scoped_recovery)
            if use_scoped:
                first_trace_data = trace.to_dict()
                scoped_trace = RetrievalTrace.start(query)
                second = (retrieval.retrieve_frozen(
                    query, prev_query=prev_query, trace=scoped_trace,
                    keywords=keywords, policy=policy, document_id=scoped_id,
                    recovery_round=1, **task_args) if keywords else
                    retrieval.retrieve_frozen(
                        query, prev_query=prev_query, trace=scoped_trace,
                        policy=policy, document_id=scoped_id, recovery_round=1, **task_args))
                scoped_trace.record_correction(first_decision, corrective)
                second["corrective"] = corrective.to_dict()
                second["first_gate"] = gate
                raw = second
                gate = raw.get("gate") or {}
                final_trace = scoped_trace
                scoped_used = True
            else:
                second = (retrieval.retrieve_frozen(
                    query, prev_query=prev_query, trace=trace, keywords=keywords,
                    extra_query=corrective.normalized_query, policy=policy, recovery_round=1, **task_args)
                    if keywords else retrieval.retrieve_frozen(
                        query, prev_query=prev_query, trace=trace,
                        extra_query=corrective.normalized_query, policy=policy, recovery_round=1, **task_args))
                if trace is not None:
                    trace.record_correction(first_decision, corrective)
                second["corrective"] = corrective.to_dict()
                second["first_gate"] = gate
                raw = second
                gate = raw.get("gate") or {}
        elif not first_decision.evidence_exposable:
            trace.record_candidates("first_final", raw.get("contexts") or [])
            raw = _close_unexposable(raw, trace, gate)
            gate = raw.get("gate") or {}
    if (gate.get("status") == "uncertain"
            and gate.get("evidence_exposable") is False):
        raw = _close_unexposable(raw, final_trace, raw.get("first_gate") or gate)
        gate = raw.get("gate") or {}
    contexts = raw.get("contexts") or []
    reason = raw.get("reason")
    blocks = [{
        "source": context.get("source"),
        "heading_path": context.get("heading_path"),
        "text": context.get("text"),
        "score": _score(context),
        "document_id": context.get("document_id"),
        "chunk_idx": context.get("chunk_idx"),
        **({"evidence_unit": context["evidence_unit"]} if "evidence_unit" in context else {}),
        **({"table_context": context["table_context"]} if "table_context" in context else {}),
    } for context in contexts]
    gate = raw.get("gate") or {}
    data = {
        "query": query,
        "keywords": keywords,
        "reason": reason,
        "tier": raw.get("tier"),
        "blocks": blocks,
        "sources": [context.get("source") for context in contexts],
        "missing_files": raw.get("missing_files"),
        "gate": {key: gate.get(key) for key in
                 ("status", "reason", "policy_version", "retry_allowed",
                  "evidence_exposable")}
        if gate else None,
        "corrective": raw.get("corrective"),
        "first_gate": raw.get("first_gate"),
        "target_record": raw.get("target_record"),
        **({"evidence_units": raw["evidence_units"]} if "evidence_units" in raw else {}),
    }
    data["coverage"] = dict(coverage_signal(query, blocks),
                            basis="query_term_match_not_answer_sufficiency",
                            scope_filter_applied=False, scope_filtered=None)
    state = gate.get("status")
    if state is not None:
        status = _GATE_STATE_TO_STATUS.get(state)
        if status is None:
            raise _unknown_gate_state_error(state)
        # The gate state is authoritative: a non-empty decision must carry
        # blocks, and an empty decision must not.
        if state in ("supported", "uncertain") and not blocks:
            raise ValueError(
                "search_regulations: 证据门状态=%r 但 blocks 为空，拒绝映射为成功类状态。"
                % state)
        if state in ("irrelevant", "empty") and blocks:
            raise ValueError(
                "search_regulations: 证据门状态=%r 但 blocks 非空，拒绝丢弃证据。"
                % state)
    elif blocks:
        status = "success"
    elif reason in _IRRELEVANT_REASONS:
        status = "irrelevant_result"
    elif reason in _NO_EVIDENCE_REASONS or (reason or "").startswith(_ABSENT_PREFIX):
        status = "no_evidence"
    else:
        raise _unknown_reason_error(reason)
    trace_data = final_trace.to_dict()
    trace_data.update({
        "tier": raw.get("tier"), "reason": reason,
        "missing_files": raw.get("missing_files"),
        "target_record": raw.get("target_record"),
    })
    if scoped_used:
        trace_data["first_round"] = first_trace_data
        trace_data["corrective"] = corrective.to_dict()
        recovery = dict(trace_data.get("recovery") or {})
        recovery.update({
            "original_query": trace.original_query,
            "corrective_query": corrective.normalized_query,
            "normalized_query": corrective.normalized_query,
            "title_correction": corrective.title_correction,
        })
        trace_data["recovery"] = recovery
    return ToolResult(
        tool="search_regulations", ok=True, status=status, data=data,
        sources=[_source(context) for context in contexts if (context.get("text") or "").strip()],
        meta={"retrieval_trace": trace_data},
    )


@tool("search_regulations", args_schema=SearchRegulationsArgs, description=DESCRIPTION)
def search_regulations_tool(query: str, keywords: list[str] | None = None) -> ToolResult:
    """Retrieve evidence from the frozen GUET regulation corpus."""
    return search_regulations_result(query, keywords)


# Public maintenance/diagnostic facade. External callers must not import scripts/.
retrieve = retrieval.retrieve
retrieve_frozen = retrieval.retrieve_frozen
ensure_collection = indexing.ensure_collection
add_document = indexing.add_document
prepare_add_document = indexing.prepare_add_document
delete_document = indexing.delete_document
reindex_document = indexing.reindex_document
reindex_all = indexing.reindex_all
sync_documents = indexing.sync_documents
get_stats = indexing.get_stats
get_document_chunks = indexing.get_document_chunks
build_bm25_vocab = indexing.build_bm25_vocab
index_document = indexing._index_document
build_index_units = indexing._build_index_units
is_table_sep = indexing._is_table_sep
is_meaningless = indexing._is_meaningless
is_complete_article = indexing._is_complete_article
client = indexing._get_client
encoder = indexing._get_encoder
dedupe_near = retrieval._dedupe_near
shingles = retrieval._shingles


def set_client(value) -> None:
    """Inject an in-memory/local Qdrant client for offline maintenance and tests."""
    indexing._client = value

DEFAULT_CRAG_HIGH = retrieval.DEFAULT_CRAG_HIGH
DEFAULT_CRAG_LOW = retrieval.DEFAULT_CRAG_LOW
DEFAULT_RERANK_HIGH = retrieval.DEFAULT_RERANK_HIGH
DEFAULT_RERANK_LOW = retrieval.DEFAULT_RERANK_LOW
FROZEN_USE_RERANKER = retrieval.FROZEN_USE_RERANKER
FROZEN_USE_LLM_GRADER = retrieval.FROZEN_USE_LLM_GRADER
