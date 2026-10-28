# Multi-Signal Evidence Gate Implementation Plan

> **For agentic workers:** Execute this plan task-by-task in a fresh CC session. Do not implement before the owner approves this plan. Use the smallest relevant tests; do not update `WORK_PROGRESS.md` or `LATEST_REPORT.md`.

**Goal:** Replace the single `rerank_score < 0.54` decision with a calibrated deterministic multi-signal evidence gate and one bounded corrective retrieval, while preserving existing corpus, index, entity, replay, qualification, and training assets until their affected cases have been revalidated.

**Architecture:** Keep BM25 + dense retrieval, RRF, reranking, `ToolResult`, evidence binding, and the current index unchanged. Extract an auditable pure gate that classifies retrieval as `supported`, `uncertain`, `irrelevant`, or `empty`; only `uncertain` receives one deterministic retry. `0.54` remains a measured feature during calibration and is not lowered by hand.

**Tech Stack:** Python, pytest, Qdrant `school_docs_phase5`, existing BM25 vocabulary, bge embedding/reranker, existing replay/audit files.

**Spec:** `docs/superpowers/specs/2026-10-13-agent-platform-redesign-design.md`

## Global Constraints

- Do not rebuild or mutate `_md`, `school_docs_phase5`, snapshots, BM25 vocabulary, catalog, alias/family sidecars, frozen qualification artifacts, or formal training assets.
- Do not use an LLM grader in this implementation.
- Never use expected evidence, answer labels, scenario IDs, or evaluation membership as a runtime signal.
- Preserve `original_query`; corrective retrieval may add a normalized query but may not replace the original.
- At most one corrective retrieval per tool call; no unbounded retry loop.
- `empty` means no usable candidates or deterministic named-document absence; `irrelevant` means candidates exist but fail support checks; `uncertain` means plausible but incomplete evidence.
- A production behavior change requires replay only for affected `search_regulations` cases, not unrelated tools or the entire repository test suite.
- Server, Qdrant, GPU, synchronization, and deletion require a separate explicit authorization.

## Review Focus

- Low reranker score with correct evidence at rank 1 must enter `uncertain` or `supported`, not silently become `irrelevant`.
- High reranker score for an out-of-scope or wrong-scope document must not become `supported` merely because the score is high.
- Corpus-absent questions must remain non-answerable after corrective retrieval.
- BM25 missing, reranker degradation, or incomplete trace signals must be observable and must not be counted as a valid calibration run.
- `uncertain` evidence must not be exposed to the answer layer as ordinary `success`.

---

### Task 1: Freeze the valid baseline and build a labeled calibration matrix

**Files:**
- Create: `_work/phase7/evidence-gate-v1/build_calibration_matrix.py`
- Create: `_work/phase7/evidence-gate-v1/calibration-matrix.jsonl`
- Create: `_work/phase7/evidence-gate-v1/calibration-manifest.json`
- Reuse: `测试集/retrieval_gold.jsonl`
- Reuse: existing answerable, colloquial, out-of-scope, Task 6 rerun, numeric-premise, and training replay audits

**Interfaces:**
- Produces one immutable row per query with `query_id`, provenance, human/evidence label, expected document/evidence identity where available, and an explicit `label_basis`.
- Allowed labels: `supported`, `irrelevant`, `empty`, `needs_human_review`.

- [ ] Inventory all usable labeled cases and reject stale runs, especially the BM25-missing Task 6 run.
- [ ] Treat the three corpus-absent multi-turn qualification questions as `empty`; do not relabel them from their generated expectations.
- [ ] Do not trust the declared `success` of the 135 failed generated scenarios as ground truth. Label only cases supported by exact evidence identity or human review; put the remainder in `needs_human_review`.
- [ ] Split calibration and holdout by source document/question family, not random row, to prevent near-duplicate leakage.
- [ ] Verify deterministic rebuild: two local builds are byte-identical, all referenced inputs have recorded SHA-256, and no production file changes.

**Acceptance:** No runtime change. Every calibration label has an evidence-backed basis; unresolved rows remain unresolved rather than being forced into a class.

### Task 2: Collect runtime-observable signals with the gate held open

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/gate_signals.py`
- Modify: `backend/agent/tools/search_regulations/scripts/diagnostics.py`
- Test: `backend/agent/tools/search_regulations/tests/test_gate_signals.py`
- Create at execution time: `_work/phase7/evidence-gate-v1/signal-survey.jsonl`

**Interfaces:**
- Produce `GateSignals` from data available during a real retrieval only: best rerank score, top-1/top-2 margin, best dense score, BM25/dense document agreement, focused-document concentration, exact reviewed title/document-number/entity anchors, query-token coverage, candidate count, scope conflict, and degradation flags.
- Must not consume expected answers, expected documents, or labels.

- [ ] Add focused unit tests for each signal and for missing/degraded inputs.
- [ ] Extend diagnostics to record `GateSignals` without changing retrieval decisions.
- [ ] Under one separately authorized server run, use the valid Phase 5 identity, absolute BM25 path, embedding CPU, and reranker CUDA to survey the calibration and holdout rows with thresholds opened only for measurement.
- [ ] Fail the survey if BM25 is missing, reranker falls back, corpus identity differs, or any query lacks its expected trace fields.
- [ ] Verify that rerunning a small fixed probe produces identical decision inputs except telemetry.

**Acceptance:** A complete signal survey exists; production output, tool status, frozen qualification pack, and datasets are unchanged.

### Task 3: Select and record a policy offline

**Files:**
- Create: `_work/phase7/evidence-gate-v1/select_policy.py`
- Create: `_work/phase7/evidence-gate-v1/policy-candidates.json`
- Create: `_work/phase7/evidence-gate-v1/selected-policy.json`
- Test: `_work/phase7/evidence-gate-v1/test_select_policy.py`

**Interfaces:**
- Consume only `calibration-matrix.jsonl` and `signal-survey.jsonl`.
- Produce a versioned deterministic rule set with explicit thresholds, required signal combinations, calibration metrics, holdout metrics, input hashes, and rejection reasons.

- [ ] Re-run the legacy scalar-threshold calibration on the current frozen corpus, current BM25 vocabulary, current embedding, current reranker, and the same signal-survey rows. Scan the full observed rerank-score range rather than testing only `0.54`; record confusion matrices and answerable/OOS/absent results for every candidate threshold.
- [ ] Save the scalar baseline separately as `scalar-threshold-baseline.json`, including score distributions by question class, PR/ROC-style operating points, expected-document recall, input hashes, model identity, and the best scalar threshold under the same safety constraints. This artifact is a comparison baseline only and must not rewrite `DEFAULT_RERANK_LOW`.
- [ ] Compare the existing single threshold against simple deterministic policies; do not train a black-box model in v1.
- [ ] Keep the existing high-score path only when scope/entity checks do not contradict it.
- [ ] Permit a below-0.54 case to enter `uncertain` only through at least two independent positive signals; score proximity alone is insufficient.
- [ ] Require zero `supported` decisions on confirmed out-of-scope and corpus-absent holdout cases.
- [ ] Require no loss of expected-document recall versus the current open-gate retrieval on labeled answerable holdout cases.
- [ ] If no policy satisfies the safety constraints, stop with the comparison report; do not alter production code or lower 0.54.

**Acceptance:** The selected policy is evidence-backed and reproducible. Selection does not depend on the 135 generated scenarios' unverified declared status.

The decision report must explicitly answer all three questions: whether `0.54` remains the best scalar operating point, whether another scalar threshold is measurably better, and whether any scalar threshold can satisfy the safety constraints without a gray-zone/multi-signal policy. A new scalar value may be proposed but cannot be applied automatically.

### Task 4: Implement the pure evidence gate and correct status semantics

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/evidence_gate.py`
- Modify: `backend/agent/tools/search_regulations/scripts/retrieval.py`
- Modify: `backend/agent/tools/search_regulations/tool.py`
- Modify: `backend/agent/tools/search_regulations/scripts/trace.py`
- Test: `backend/agent/tools/search_regulations/tests/test_evidence_gate.py`
- Test: `backend/agent/tools/search_regulations/tests/test_tool.py`
- Test: `backend/agent/tools/search_regulations/tests/test_trace.py`

**Interfaces:**
- `decide(signals: GateSignals, policy: GatePolicy) -> GateDecision`.
- `GateDecision.status` is one of `supported`, `uncertain`, `irrelevant`, `empty`; it includes stable `reason` and the exact signals used.
- Tool mapping: `supported -> success`, `uncertain -> partial_evidence`, `irrelevant -> irrelevant_result`, `empty -> no_evidence`.

- [ ] Write tests for the four outcomes, high-score scope contradiction, low-score multi-signal recovery, absent corpus evidence, and degraded signal input.
- [ ] Move the final gate decision out of the inline `best >= high / best < low` branch into the pure module; leave recall and reranking algorithms untouched.
- [ ] Preserve existing reasons where semantics are unchanged; add exhaustive mappings for new reasons so unknown reasons still fail closed through the Registry boundary.
- [ ] Make trace output include the policy version, decision, reason, and contributing signals without changing evidence binding inputs.
- [ ] Map medium/gray evidence to `partial_evidence`; do not continue exposing it as ordinary `success` merely because blocks exist.
- [ ] Run only the gate, search tool, trace, renderer/golden, and replay-contract tests affected by the status change.

**Acceptance:** All four states are reachable and auditable. No expected-evidence label is imported by production code. Non-search tools and entity resolution are unchanged.

### Task 5: Add one bounded deterministic corrective retrieval

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/corrective_query.py`
- Modify: `backend/agent/tools/search_regulations/scripts/retrieval.py`
- Modify: `backend/agent/tools/search_regulations/scripts/query_plan.py`
- Test: `backend/agent/tools/search_regulations/tests/test_corrective_query.py`
- Test: `backend/agent/tools/search_regulations/tests/test_retrieval_trace.py`

**Interfaces:**
- `build_corrective_query(original_query, query_plan, first_decision) -> CorrectiveQuery | None`.
- `CorrectiveQuery` preserves `original_query`, records `normalized_query`, deterministic expansions, and reason; it can execute at most once.

- [ ] Allow correction only for `uncertain`, never for deterministic `empty` or clear `irrelevant`.
- [ ] Build correction from reviewed route templates, resolved entities, official titles/document numbers, and existing keywords; no LLM and no free-form guessed facts.
- [ ] Retrieve both the original and corrective query, merge/deduplicate candidates, rerank once, and run the same evidence gate again.
- [ ] Preserve the original question in tool data and trace; record retry count and both query forms.
- [ ] Test that correction cannot loop, cannot create a new entity, and cannot turn a confirmed corpus-absent case into `supported`.

**Acceptance:** A gray case gets at most one deterministic recovery attempt, with full traceability and unchanged corpus/index.

### Task 6: Revalidate only the affected qualification and training surface

**Files:**
- Create at execution time: `_work/phase7/evidence-gate-v1/affected-cases.jsonl`
- Create at execution time: `_work/phase7/evidence-gate-v1/server-replay/`
- Do not modify frozen qualification or formal training assets in this task.

**Interfaces:**
- The affected set is the union of `search_regulations` cases whose status, reason, selected evidence, or binding changes under the new gate.

- [ ] Run an offline impact scan first; do not replay structural tools or unaffected scenarios.
- [ ] Replace the three corpus-absent multi-turn qualification questions with answerable mechanism-equivalent questions before qualification replay; do not change their expected state to make absent facts pass.
- [ ] Under one separately authorized server session, double-replay affected qualification cases and single-replay affected training cases using the same corpus/BM25/device identity as Task 2.
- [ ] Compare tool, raw/canonical args, status, reason, gate signals, evidence IDs, observation, and binding; telemetry is excluded.
- [ ] For the 29 numeric-premise rows, require every numeric/conditional token used by a future answer to be visible in evidence; unresolved rows remain excluded.
- [ ] Partition the 320 multi-turn rows into `validated`, `rewrite_required`, and `exclude`; never bulk-change declared status to match retrieval output.

**Acceptance:** Revised qualification cases pass double replay; every retained training case has evidence-consistent status. No formal asset is written.

### Task 7: Resume the existing data path after human review

**Files:**
- Inputs: validated single-turn 1,158-row batch and validated multi-turn subset
- Outputs only after separate authorization: reviewed scenarios, Teacher candidates, targets/ledger, compiled dataset, quarantine, manifest

- [ ] Submit only changed/new qualification rows and the `rewrite_required`/numeric-audit rows for human review; do not ask the owner to rereview unaffected material.
- [ ] Freeze the revised multi-turn qualification pack only after review and successful double replay.
- [ ] Generate Teacher answers only for reviewed, evidence-valid scenarios; keep tool-terminal rows out of text generation.
- [ ] Promote only candidates with both scenario review and answer review.
- [ ] Compile and inspect manifest/quarantine before training authorization.

**Acceptance:** The existing single-turn chain is reused, the validated multi-turn subset joins through the new compiler path, and no dataset is retrained merely because the retrieval gate implementation changed.

## Explicit Non-Goals

- No index rebuild, embedding/reranker replacement, Qdrant upgrade, LLM relevance grader, new ToolStatus, alias/catalog change, or broad Agent redesign.
- No automatic repair of all 135 failed generated scenarios.
- No full repository test run unless a shared contract outside search/replay is changed.
- No `WORK_PROGRESS.md` or `LATEST_REPORT.md` update.

## Stop Conditions

- Calibration labels cannot be supported by evidence.
- Confirmed negative/absent holdout receives `supported`.
- BM25 or reranker degrades during survey/replay.
- Corpus/index/BM25 identity differs from the frozen Phase 5 baseline.
- Corrective retrieval requires an unreviewed alias, guessed entity, LLM judgment, or a second retry.
- Any formal training or frozen qualification asset would need to be overwritten without separate authorization.
