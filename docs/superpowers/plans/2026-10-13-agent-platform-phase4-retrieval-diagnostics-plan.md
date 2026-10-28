# Retrieval Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有检索结果的前提下，为 `search_regulations` 建立可重放的 RetrievalTrace、独立检索金标准和故障分层报告。

**Architecture:** Trace 是 `search_regulations` 工具内部的旁路观测对象，只记录各阶段候选、门控决策、降级事件和耗时，不参与排序与门控。评测器直接消费 Trace 和金标准，把失败唯一归类为语料覆盖、候选池、去重/截断、精排、门控或分块；阶段 5 再依据报告改 RAG。

**Tech Stack:** Python 3.13, dataclasses, Qdrant, existing dense + BM25 + RRF + reranker pipeline, pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-agent-platform-redesign-design.md` §7.4、§9.3、§11 阶段 4。

## Global Constraints

- 不改 embedding、BM25、RRF、reranker、去重、分块、top-k 或门控参数。
- `retrieve()` / `retrieve_frozen()` 的原有返回字段和 observation 文本必须逐字不变；Trace 不整体暴露给模型。
- 金标准不从训练数据抽取；只转换已独立的 `eval_holdout_src.jsonl` 和 `agent_acceptance.json`。
- 建集脚本是可重放的数据工具，不是一次性迁移脚本；输出必须确定性且带来源与审核状态。
- 本阶段不训练、不上传、不部署、不启停服务。

## Review Focus

1. Trace 开启与否不得改变候选顺序、评分、最终块或 observation。
2. sparse 不可用和 reranker 降级必须可见，但不得把技术失败记成 `no_evidence`。
3. 同文档多块必须以 `document_id:chunk_idx` 区分，不得按标题合并。
4. 可答题、明确未收录题必须分开计算召回和越界误放，不得用一个总准确率掩盖。
5. 故障分层必须有排他顺序和可定位证据，不得把所有 `hits=0` 都归因为召回。

---

### Task 1: RetrievalTrace contract

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/trace.py`
- Modify: `backend/agent/tools/search_regulations/scripts/__init__.py`
- Test: `backend/agent/tools/search_regulations/tests/test_trace.py`

**Interfaces:**
- Produces: `RetrievalTrace.start(original_query, trace_id=None)`.
- Produces: `record_candidates(stage, rows)`, `record_timing(stage, elapsed_ms)`, `record_gate(status, reason, best_score)`, `record_degradation(stage, code, detail)`, `to_dict()`.

- [x] Write tests for stable candidate identity, stage order, timing, gate and degradation serialization.
- [x] Implement a data-only trace; candidate snapshots contain identity/title/rank/scores and a bounded preview, never framework/model objects.
- [x] Verify JSON round-trip and that two chunks from one document remain distinct.

### Task 2: Instrument the frozen retrieval pipeline without behavior drift

**Files:**
- Modify: `backend/agent/tools/search_regulations/scripts/retrieval.py`
- Modify: `backend/agent/tools/search_regulations/tool.py`
- Test: `backend/agent/tools/search_regulations/tests/test_retrieval_trace.py`
- Test: `backend/agent/tools/search_regulations/tests/test_tool.py`

**Interfaces:**
- `retrieve(..., trace: RetrievalTrace | None = None) -> dict`.
- `retrieve_frozen(query, prev_query=None, trace=None) -> dict`.
- `ToolResult.meta["retrieval_trace"]` receives `trace.to_dict()`; renderer ignores it.

- [x] Capture original/effective query, dense, BM25, aux, fused, post-dedupe, rerank-pool, reranked and final stages.
- [x] Capture stage latency, gate reason and sparse/reranker fallback events.
- [x] Add a paired fake-backend test: trace off/on must produce equal retrieval return values and byte-identical observation.
- [x] Keep existing callers valid by making Trace optional.

### Task 3: Build a provenance-preserving retrieval gold set

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/build_gold.py`
- Create: `backend/agent/tools/search_regulations/scripts/gold.py`
- Create: `测试集/retrieval_gold.jsonl`
- Test: `backend/agent/tools/search_regulations/tests/test_gold.py`

**Interfaces:**
- Produces records with `id`, `query`, `answerable`, `expected_tool`, `expected_docs`, `expected_evidence`, `acceptable_gate`, `source_dataset`, `source_id`, `review_status`.
- 48 answerable cases come from `eval_holdout_src.jsonl`; 14 no-evidence cases come from the active acceptance set's `kind="未收录"` bucket. The 15 structured cultivation-plan cases remain in the file with `expected_tool="query_course"` but are excluded from `search_regulations` metrics.

- [x] Validate unique IDs, nonblank queries, answerable evidence/doc requirements and negative-case emptiness.
- [x] Build deterministically and assert a second build is byte-identical.
- [x] Mark provenance honestly (`inherited_source_verified` / `inherited_behavior_reviewed`), not as a new manual audit.
- [x] Assert zero exact-question overlap with the active training candidate if that file exists; report rather than silently delete collisions.

### Task 4: Fault-layer diagnostic runner

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/diagnostics.py`
- Test: `backend/agent/tools/search_regulations/tests/test_diagnostics.py`

**Interfaces:**
- Produces metrics: Candidate Recall@120, Rerank Recall@5, gate false-reject rate, out-of-scope false-accept rate.
- Produces one exclusive diagnosis per failed answerable case: `corpus_coverage`, `candidate_pool`, `dedupe_or_truncation`, `rerank`, `gate`, `chunking`, or `success`.

- [x] Pin the exclusive decision order with synthetic traces for every fault layer.
- [x] Compare document names with the existing canonical title normalization rules; do not use raw equality only.
- [x] Add a CLI entry (`python -m ...diagnostics`) that writes JSON report and exits nonzero on malformed gold/trace, not on poor quality metrics.
- [x] Include configuration fingerprint and trace IDs in the report; do not dump credentials or full auth headers.

### Task 5: Close the Phase 4 gate

**Files:**
- Modify: `docs/WORK_PROGRESS.md`
- Create: `_work/agent-platform-phase4-manifest.txt`
- Create when a real corpus run is available: `_work/retrieval-diagnostics-phase4.json`

- [x] Run focused tests and the full backend suite with a writable pytest base temp.
- [x] Replay observation and frozen tools-schema goldens; require zero drift.
- [x] Run the diagnostic CLI against the real indexed corpus when available; report current metrics and fault clusters without tuning.
- [x] If the real corpus/model is unavailable, stop with implementation complete but Phase 4 quality baseline explicitly pending; do not fabricate metrics. (Not applicable: the local indexed corpus and both models were available.)
- [x] Record hashes, counts, tests, trace coverage and any external blocker; update progress only to the achieved state.

## Self-Review Result

- Spec coverage: §7.4 maps to Tasks 1–2; §9.3 maps to Tasks 3–4; the stage-4 fault report and no-tuning boundary map to Task 5.
- Type consistency: only `RetrievalTrace` crosses retrieval functions; `ToolResult` and observation contracts remain unchanged.
- Review focus: all five failure classes have synthetic tests before any real-corpus result is accepted.
- Proportion: this plan adds observability and evaluation only. Query rewriting, threshold calibration, parser repair and model training remain outside Phase 4.
