# Entity Alias Resolution Remaining Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with TDD. This workspace has no Git metadata, so replace commit steps with SHA-256 checkpoints in `docs/WORK_PROGRESS.md`.

**Goal:** Complete the production integration of entity resolution without silent entity selection, then restore reproducible training and qualification assets.

**Architecture:** `entity_resolver` remains the only query-level resolver and `entity_calls` remains the pure call validator. The runtime stores both artifacts in `AgentState`, validates model calls before Registry execution, and executes only canonical arguments. Structured tools retain defensive ambiguity guards. Existing promoted targets are revised only through an explicit audited revision path; no hash is hand-edited.

**Tech Stack:** Python 3.10+, Pydantic, LangGraph, pytest, JSONL sidecars.

**Spec:** `docs/superpowers/specs/2026-10-16-entity-alias-resolution-design.md`

## Global Constraints

- Do not change the seven public tool schemas or add a `ToolStatus`.
- T4-T7 never become selected entities.
- Preserve `original_query`, `normalized_query`, `raw_args`, and `canonical_args`.
- Ambiguity becomes clarification before Registry execution, never `no_evidence`, `irrelevant_result`, or `tool_failed` on the normal graph path.
- Do not modify `_md`, `school_docs_phase5`, `bm25_vocab_phase5.json`, or rebuild the index.
- Do not hand-edit binding or promotion hashes.
- Existing unrelated backend failures remain out of scope.

## Review Focus

- A reviewed target whose scenario arguments change must retain an auditable old-to-new revision chain.
- A call that validates to `clarify` or `reject` must produce zero Registry calls.
- Canonical arguments, not model spelling, must drive duplicate and budget keys.
- `compare_course` must remain all-or-nothing and preserve major order.
- Resolver construction failure must stop and report, not degrade to an answer or tool failure.

---

### Task 1: Repair the two synthetic course scenarios through an audited revision

**Files:**
- Modify: `训练集/scenarios/stop_and_recover.jsonl`
- Modify: `训练集/scenarios/react_multi_step.jsonl`
- Modify: `训练集/compiler/promote.py`
- Test: `训练集/compiler/tests/test_promote.py`
- Modify generated assets only through the tested revision entry point.

**Interfaces:**
- Consume: existing candidate, target, ledger, replay, and binding contracts.
- Produce: one explicit target-revision operation that preserves prior batch/hash provenance and leaves exactly one active target.

- [x] Write red tests proving a changed reviewed scenario cannot be repaired by ordinary `promote`, direct hash edits, or an unaudited overwrite.
- [x] Define the smallest revision row/entry shape needed to retain previous batch and target hash while identifying the new active target.
- [x] Implement preflight, replay, binding recomputation, staged writes, and cross-file consistency checks.
- [x] Change both synthetic questions and tool arguments from `工程力学` to `工程力学1`; update the human target wording through the revision candidate.
- [x] Regenerate target/ledger/full_sft/quarantine/manifest and verify byte reproducibility plus full compiler tests.

### Task 2: Runtime state and resolver node

**Files:**
- Modify: `backend/agent/runtime/state.py`
- Modify: `backend/agent/runtime/graph.py`
- Test: `backend/tests/agent_platform/test_entity_resolution_graph.py`

**Interfaces:**
- Produce state fields `resolution`, `resolution_error`, and deterministic resolver provenance.
- Produce graph node `resolve_entities(state) -> dict` before the first model decision.

- [x] Add red tests for resolver construction failure, original/normalized query retention, and one resolver call per user turn.
- [x] Add the state fields and injected resolver factory path for tests.
- [x] Insert `resolve_entities` before `decide`; construction failure must terminate with a user-visible stop/report message.
- [x] Verify no Registry, Qdrant, or LLM call occurs when resolver construction fails.

### Task 3: Graph call-validation barrier

**Files:**
- Modify: `backend/agent/runtime/graph.py`
- Test: `backend/tests/agent_platform/test_entity_resolution_graph.py`

**Interfaces:**
- Consume: `entity_calls.validate_calls`.
- Produce state fields `validated_calls`, `call_validations`, and clarification output.

- [x] Add red tests for T4, unknown, scope conflict, cross-type narrowing, `compare_course` partial failure, and opaque tools.
- [x] Add `validate_calls` between `decide` and `execute_tools`.
- [x] Route any `clarify` to a deterministic clarification response and any `reject` to a safe correction response; execute zero tools in both cases.
- [x] Preserve validation audit records in the final done payload without changing existing SSE keys.

### Task 4: Canonical execution and duplicate/budget accounting

**Files:**
- Modify: `backend/agent/runtime/graph.py`
- Modify: `backend/agent/runtime/budgets.py`
- Modify: `backend/agent/runtime/state.py`
- Test: existing graph/budget tests plus entity-resolution graph tests.

**Interfaces:**
- Execute only `canonical_args` for allowed structured calls.
- Store both raw and canonical args in call logs and executed records.

- [x] Add red tests showing spelling variants collapse to one duplicate key and raw args remain available for audit.
- [x] Use canonical args for planning, duplicate detection, Registry execution, evidence identity, and budget bookkeeping.
- [x] Keep opaque-tool behavior byte compatible.
- [x] Run agent-platform and backend regression tests (backend retains the same two pre-existing failures).

### Task 5: Retrieval query construction

**Files:**
- Modify only the existing `search_regulations` query-construction boundary and focused tests.

- [x] Add red tests that embedding receives the confirmed normalized query while BM25 retains the original abbreviation plus confirmed canonical terms.
- [x] Do not expand unresolved aliases and do not change retrieval thresholds, pool sizes, reranker, gate, index, or vocabulary.
- [x] Verify existing search-regulations tests remain green.

### Task 6: Pending alias review queue

**Files:**
- Add one focused queue module and tests; update docs.

- [x] Add red tests for de-identification, duplicate aggregation, and prohibition on automatic promotion.
- [x] Record only unresolved/ambiguous expressions with catalog/resolver provenance.
- [x] Keep the production alias files unchanged until explicit human review.

### Task 7: Qualification and final regression

**Files:**
- Add only representative pending qualification scenarios and rubrics after the runtime is complete.

- [ ] Add A/B/D mechanism cases; keep C recorded as a coverage gap until a calibrated deterministic unknown-abbreviation rule exists.
- [ ] Double-replay all tool-using additions in the real environment and compare tools, args, statuses, reasons, binding, and evidence preview.
- [ ] Resolve the pre-existing `tq-009` and `tq-010` human decisions without rebuilding Phase 5 solely for this feature.
- [ ] Run protected-asset SHA checks and all relevant suites; freeze only after explicit human review.

