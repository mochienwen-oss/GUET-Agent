# Evidence-Centered Campus Agent v2 Implementation Plan

> **For agentic workers:** Execute this plan phase-by-phase. Do not create a separate baseline phase. Each phase must pass its own acceptance gate before the next phase may change production behavior. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing Campus Agent from document-level relevance gating to auditable claim-level evidence verification, then reconnect that verifier to multi-turn runtime, qualification, and data production without rebuilding the stable entity, tool, context, Skill, or compiler foundations.

**Architecture:** Preserve the current `POLICY_V1` as the active fallback while v2 is developed behind explicit interfaces. Split the path into document retrieval, in-document evidence retrieval, claim planning, deterministic claim verification, decision aggregation, and answer rendering. Training resumes only after the runtime contract and independent end-to-end acceptance are stable.

**Tech Stack:** Python, pytest, Qdrant `school_docs_phase5`, existing BM25/dense/RRF/reranker stack, existing `ConversationContext`, Registry, Skill, replay, qualification, binding, promote, and compiler modules.

**Spec:** `docs/superpowers/specs/2026-10-18-campus-agent-v1-runtime-contract-design.md`; this plan supersedes further G2.x threshold/anchor iteration in `docs/superpowers/plans/2026-10-18-multi-signal-evidence-gate.md`.

## Global Constraints

- Start at Phase 1. Do not add a baseline-building Phase 0.
- Keep `POLICY_V1` as the production fallback until Phase 8 accepts v2; G2.1 remains experimental and is not silently enabled.
- Reuse the entity catalog, alias/family sidecars, resolver, Registry, seven tool schemas, `ConversationContext`, Skill registry, replay/compiler, qualification, and binding contracts.
- Preserve the original query, normalized query, raw arguments, canonical arguments, evidence identity, and decision reason in every affected trace.
- A document title or relevance score can expand recall but can never by itself authorize an answer.
- Every answer-bearing claim must bind to one or more evidence spans; missing support must produce `partial`, `clarify`, `no_evidence`, `contradicted`, `out_of_scope`, or `escalate`, not an invented answer.
- Deterministic validators own exact entities, numbers, units, ranges, tables, scope, ordering, and negation. A later semantic verifier may assist paraphrase handling but may not override deterministic contradictions.
- Do not generate new formal training targets while the runtime claim/evidence contract is changing.
- Do not rebuild indexes, mutate collections/snapshots, connect to the server, use GPU/LLM, freeze qualification, or write formal training assets without separate explicit authorization.
- Run only the changed module's focused tests plus one real production-path integration test. Run compiler/qualification full suites only when their shared contracts or formal assets change.
- Do not update `docs/WORK_PROGRESS.md` or `docs/LATEST_REPORT.md` for intermediate work.

## Review Focus

- A bare numeric table cell such as `80` must inherit its row/column unit only when the table structure proves that relationship.
- A contradiction such as “周三可以吗” versus “统一安排在周末” must be distinguishable from absent evidence.
- Entity matching must respect token/field boundaries so `教育学` does not match inside `非学历教育`.
- A relevant document without the requested count or condition must not become supported merely because rerank and title signals are high.
- Multi-turn pronouns and summaries may supply context only through trusted, uniquely resolved state; assistant prose and summaries never gain evidence authority.

---

## Phase 1: Evidence Contract v2 and Span-Level Review Set

**Deliverable:** A versioned, loadable claim/evidence schema and a deterministic review queue containing 100–150 representative cases. No retrieval or production decision changes.

**Files:**
- Create: `backend/agent/evidence_contract.py`
- Create: `backend/tests/test_evidence_contract.py`
- Create: `_work/phase8/evidence-contract-v2/build_review_set.py`
- Create: `_work/phase8/evidence-contract-v2/validate_review_set.py`
- Create at execution: `_work/phase8/evidence-contract-v2/evidence-review.pending.jsonl`
- Create at execution: `_work/phase8/evidence-contract-v2/evidence-review.manifest.json`
- Reuse: calibration-v2, the 17 G0.1 cases, the 48 independent-validation cases, frozen qualification cases, and representative EasyDataset user questions.

**Interfaces:**
- `ClaimSpec`: `claim_id`, `claim_type`, `subject`, `predicate`, `object`, `operator`, `value`, `unit`, `scope`, `required`.
- `EvidenceSpan`: `document_id`, `chunk_id`, `source`, `heading_path`, `quote`, `start`, `end`, `evidence_type`, `relation`.
- `ClaimVerification`: `claim_id`, `status`, `reason`, `evidence_spans`, `verifier_version`.
- Allowed claim types: `numeric`, `range`, `condition`, `procedure`, `scope`, `negation`, `definition`, `calculation`.
- Allowed span relations: `supports`, `contradicts`, `insufficient`.

- [x] Add schema tests that reject unknown fields, missing identity, invalid offsets, unsupported claim types, and evidence quotes that do not match the declared source text supplied to validation.
- [x] Implement strict dataclasses/loaders in `evidence_contract.py`; do not add retrieval or model dependencies.
- [x] Build a deterministic stratified queue of 100–150 cases covering every claim type, formal/colloquial, single/multi-turn, answerable/ambiguous/corpus-absent/OOS, and table/non-table evidence.
- [x] Group duplicate facts under `fact_cluster_id`; keep register variants but prevent them from counting as independent facts.
- [x] Record source SHA-256 and selection reasons; rebuild twice and require byte-identical JSONL and manifest.
- [x] Run `pytest backend/tests/test_evidence_contract.py -q` and the review-set validator.

**Acceptance:** The schema is strict, the queue is deterministic, every row declares why it was selected, and no row is silently assigned a final support label. Human span/claim review is the explicit stop point before Phase 2 evaluation uses gold labels.

---

## Phase 2: Two-Level Retrieval Contract

**Deliverable:** Separate document candidates from in-document evidence candidates while preserving the current hybrid retriever and production default.

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/retrieval_contract.py`
- Create: `backend/agent/tools/search_regulations/scripts/document_retrieval.py`
- Create: `backend/agent/tools/search_regulations/scripts/evidence_retrieval.py`
- Modify: `backend/agent/tools/search_regulations/scripts/retrieval.py`
- Modify only if metadata is missing: `backend/agent/tools/search_regulations/scripts/indexing.py`
- Test: `backend/agent/tools/search_regulations/tests/test_retrieval_contract.py`
- Test: `backend/agent/tools/search_regulations/tests/test_evidence_retrieval.py`

**Interfaces:**
- `retrieve_documents(query, keywords, *, limit, trace) -> list[DocumentCandidate]`.
- `retrieve_evidence(query, documents, *, claims=(), per_document_limit, adjacent_window=1, trace) -> list[EvidenceCandidate]`.
- `DocumentCandidate` carries document identity and document-level scores only.
- `EvidenceCandidate` carries document/chunk/section/table identity, text, provenance, and ranking signals; it does not carry a final support decision.

- [ ] Pin current document candidate ordering on representative fixed fixtures before extracting functions.
- [ ] Extract document-level retrieval without changing BM25/dense/RRF/reranker algorithms.
- [ ] Implement document-scoped chunk retrieval using the existing Qdrant `document_id` filter; reuse G2.1 mechanics but remove its authority to decide support.
- [ ] Add deterministic adjacent-block and same-table-row expansion with strict document boundaries and bounded windows.
- [ ] If existing payload lacks parent/neighbor/table metadata, first implement an offline metadata audit; stop for index-rebuild authorization rather than mutating the collection.
- [ ] Add separate trace sections for document recall and evidence recall.
- [ ] Run focused retrieval tests and one production import/call smoke test with `POLICY_V1` unchanged.

**Acceptance:** Document retrieval and evidence retrieval can be evaluated independently; title/scoped recovery expands candidates only; production output remains unchanged until Phase 6.

---

## Phase 3: Deterministic Claim Planner

**Deliverable:** A bounded planner that converts the current user question plus trusted context into structured claims without inventing subjects, scope, entities, or facts.

**Files:**
- Create: `backend/agent/claim_planner.py`
- Create: `backend/tests/test_claim_planner.py`
- Modify: `backend/agent/runtime/graph.py` only to carry an optional `claim_plan` field behind a disabled v2 path.
- Modify: `训练集/compiler/multiturn.py` only to serialize the same claim-plan identity for replay fixtures.

**Interfaces:**
- `plan_claims(query: str, *, prior_context=None, resolved_entities=()) -> ClaimPlan`.
- `ClaimPlan` preserves `original_query`, resolved references, unresolved references, claims, and `requires_clarification`.
- The planner may use reviewed lexicons and deterministic parsing; it may return incomplete claims but may not guess missing fields.

- [ ] Test numbers/units, ranges, conditions, ordered procedures, institutional scope, negation, calculations, and empty/no-claim questions.
- [ ] Test token boundaries, including `教育学` versus `非学历教育`.
- [ ] Test pronoun resolution with zero, one, and multiple trusted antecedents.
- [ ] Implement minimal deterministic parsers and reuse catalog/resolver outputs rather than duplicating entity logic.
- [ ] Ensure summaries and assistant prose cannot create trusted claim fields.
- [ ] Verify runtime and compiler serialize byte-identical claim plans for the same trusted inputs.

**Acceptance:** Supported claim types produce auditable structures; ambiguous references produce clarification; unknown language remains unplanned rather than being forced into a false claim.

---

## Phase 4: Deterministic Claim Verifiers

**Deliverable:** Pure verifiers for the common campus-document fact shapes, returning support/contradiction/insufficiency with exact spans.

**Files:**
- Create: `backend/agent/claim_verifiers/base.py`
- Create: `backend/agent/claim_verifiers/numeric.py`
- Create: `backend/agent/claim_verifiers/condition.py`
- Create: `backend/agent/claim_verifiers/procedure.py`
- Create: `backend/agent/claim_verifiers/scope.py`
- Create: `backend/agent/claim_verifiers/negation.py`
- Create: `backend/agent/claim_verifiers/table.py`
- Create: `backend/agent/claim_verifiers/registry.py`
- Test: `backend/tests/claim_verifiers/`

**Interfaces:**
- `verify_claim(claim: ClaimSpec, evidence: list[EvidenceCandidate]) -> ClaimVerification`.
- Exact validators return only `supported`, `contradicted`, or `insufficient` and must cite spans.
- Deterministic contradiction outranks semantic or score-based support.

- [ ] Implement the registry and fail closed for unsupported claim types.
- [ ] Implement numeric/unit/range validation, including inherited table headers and rejecting unrelated nearby numbers.
- [ ] Implement condition validation for all/any, prerequisites, outcomes, and exceptions.
- [ ] Implement ordered-procedure completeness and required-step checks.
- [ ] Implement institution/college/major/student-category scope checks.
- [ ] Implement negation and contradiction checks, including weekday/weekend and allowed/prohibited forms.
- [ ] Add fixture tests for the known failure shapes: bare `80`, paraphrased condition, `教育学` boundary, document-without-count, and “周三” versus “周末”.
- [ ] Run only verifier tests plus evidence-contract tests.

**Acceptance:** Each known failure class has a deterministic result and span; high retrieval scores cannot override missing or contradictory facts.

---

## Phase 5: Optional Constrained Semantic Verifier

**Deliverable:** A separately versioned semantic assistant for paraphrase/entailment cases that deterministic verifiers cannot resolve. This phase may be skipped if Phase 4 meets acceptance targets.

**Files:**
- Create if justified: `backend/agent/claim_verifiers/semantic.py`
- Create if justified: `backend/tests/claim_verifiers/test_semantic.py`
- Create: `_work/phase8/semantic-verifier-v1/calibration.json`

**Interfaces:**
- `verify_semantically(claim, spans) -> SemanticVerification` with `entails`, `contradicts`, or `unknown`.
- It receives only bounded retrieved spans and may not retrieve, choose entities, calculate exact values, or override deterministic contradictions.

- [ ] Measure Phase 4 unresolved paraphrase cases before selecting a model or adding dependencies.
- [ ] If the unresolved set is material, prepare a labeled calibration set and request dependency/server/GPU authorization separately.
- [ ] Require fail-closed behavior, model identity, input hash, and exact span retention.
- [ ] Skip implementation and record “not required” if deterministic coverage is sufficient.

**Acceptance:** Either no semantic verifier is needed, or it improves only the unresolved paraphrase subset without changing deterministic facts or introducing unsupported exposure.

---

## Phase 6: Evidence Aggregation and Runtime Decisions

**Deliverable:** A v2 decision layer with distinct answer, partial, clarify, no-evidence, contradicted, out-of-scope, tool-failed, and escalate outcomes.

**Files:**
- Create: `backend/agent/evidence_decision.py`
- Modify: `backend/agent/tools/search_regulations/tool.py`
- Modify: `backend/agent/tools/search_regulations/renderer.py`
- Modify: `backend/agent/runtime/graph.py`
- Modify: `backend/agent/core/contracts.py` only if existing fields cannot represent claim results without breaking compatibility.
- Test: `backend/tests/test_evidence_decision.py`
- Test: `backend/agent/tools/search_regulations/tests/test_tool.py`
- Test: `backend/tests/agent_platform/test_entity_resolution_graph.py`

**Interfaces:**
- `aggregate_claims(plan, verifications, retrieval_state) -> EvidenceDecision`.
- The v2 path is selected explicitly by policy version; `POLICY_V1` remains the fallback.
- Evidence binding includes claim-plan version, verifier versions, decision version, and span identities, not model prose.

- [ ] Define exhaustive aggregation rules for required versus optional claims.
- [ ] Separate missing context from absent corpus evidence and from tool execution failure.
- [ ] Prevent `partial` or `contradicted` evidence from being rendered as ordinary success.
- [ ] Preserve existing `ToolResult` compatibility at the Registry boundary through explicit mappings.
- [ ] Add renderer tests proving no unsupported numeric threshold, consequence, step, or scope statement is emitted.
- [ ] Run the focused tool/graph suite and compare unaffected non-regulation tools for zero behavior drift.

**Acceptance:** Every final state has one documented cause; every answerable claim has supporting spans; ambiguous, absent, contradictory, and failed cases remain distinct.

---

## Phase 7: Multi-Turn, Summary, and Skill Integration

**Deliverable:** Claim/evidence v2 works through existing long-context, correction, pending-task, and Skill paths without granting facts to summaries or prior assistant text.

**Files:**
- Modify: `backend/conversation_context.py`
- Modify: `backend/entity_prior.py`
- Modify: `backend/agent/runtime/context_window.py`
- Modify: `backend/agent/runtime/context_summary.py`
- Modify: `backend/agent/runtime/skills.py`
- Modify: `训练集/compiler/multiturn.py`
- Test: `backend/tests/test_multiturn_context.py`
- Test: `backend/tests/agent_platform/test_runtime_multiturn_context.py`
- Test: `backend/tests/agent_platform/test_context_window.py`
- Test: `backend/tests/agent_platform/test_skills.py`

**Interfaces:**
- Trusted prior facts may contribute spans only when their evidence identity remains active.
- User corrections invalidate affected prior claims and bindings.
- Summary text is interpretation context only; it cannot satisfy a verifier.
- Skill allowlists constrain tool availability but cannot bypass claim/evidence decisions.

- [ ] Test unique, absent, and multiple antecedents for pronouns such as “它”“这个专业”“上一条规定”.
- [ ] Test cross-turn tool-failure recovery separately from within-trajectory retries.
- [ ] Test 8–12 turn compaction with unchanged active evidence identities.
- [ ] Test user correction invalidation and stale-summary resistance.
- [ ] Test prompt injection in historical user text and summaries.
- [ ] Verify runtime and compiler produce the same current-turn binding.

**Acceptance:** Multi-turn context improves interpretation but never supplies unbound facts; corrections, compaction, and Skill selection remain auditable and deterministic.

---

## Phase 8: Layered Evaluation and Qualification v2

**Deliverable:** A fresh, untouched holdout and a qualification pack that separately measures document recall, span recall, claim verification, decision state, and final answer quality.

**Files:**
- Create: `_work/phase8/evaluation-v2/build_splits.py`
- Create: `_work/phase8/evaluation-v2/evaluate_layers.py`
- Create: `_work/phase8/evaluation-v2/error_taxonomy.py`
- Create at execution: calibration, holdout, fact-cluster, and human-review artifacts.
- Extend only after approval: qualification draft, rubric, selection, and coverage-probe files.

**Interfaces:**
- Metrics are reported by layer, claim type, register, single/multi-turn, negative type, and fact cluster.
- The holdout is never used to select rules, thresholds, examples, or prompts.
- Error classes: label error, ambiguity, document miss, span miss, unsupported claim, contradiction miss, calculation error, scope error, rendering error.

- [ ] Split by fact cluster and source family, not random rows.
- [ ] Keep calibration and holdout identities immutable after freeze.
- [ ] Report document recall, evidence-span recall, claim precision/recall, state accuracy, unsupported exposure, clarification accuracy, and end-to-end answer review separately.
- [ ] Compare v2 against `POLICY_V1`; require zero confirmed unsupported exposure and no unexplained regression on answerable fact clusters.
- [ ] Run server replay only after explicit authorization, with collection/BM25/corpus identity hard gates and exact cleanup.
- [ ] Submit qualification answers for one human review; do not tune v2 from qualification or holdout failures without creating a new version and a new untouched set.

**Acceptance:** v2 passes component and end-to-end gates on untouched data, or remains disabled with a complete error report. Passing retrieval alone is insufficient.

---

## Phase 9: Data Migration, Teacher, Compile, and Training Readiness

**Deliverable:** Existing single- and multi-turn question pools are replayed against the accepted v2 runtime; only reviewed, bound targets are promoted and compiled.

**Files:**
- Reuse: cleaned 1,158 single-turn scenarios and 320 multi-turn scenarios as question pools.
- Modify only after approval: `训练集/compiler/replay.py`, `qualification.py`, `promote.py`, `compile.py` if v2 binding fields require it.
- Write only after explicit approval: formal targets, ledger, full SFT, quarantine, and manifest.

**Interfaces:**
- Old questions may be retained; old evidence states, observations, and targets are not trusted until v2 replay.
- Teacher generates prose only after v2 evidence decisions are fixed.
- Promote still requires reviewed scenario, approved candidate, reviewer identity, matching replay/evidence binding, and exclusion checks.

- [ ] Replay existing question pools and classify each row as reusable, relabel-required, evidence-incomplete, or rejected.
- [ ] Do not replace difficult valid questions merely to satisfy quotas.
- [ ] Freeze qualification v2 only after human approval.
- [ ] Generate Teacher candidates, then perform claim-by-claim human review against v2 spans.
- [ ] Promote and compile only under explicit formal-asset authorization.
- [ ] Report final counts, source/fact-cluster distribution, quarantine reasons, manifest identity, and training configuration before requesting GPU training authorization.

**Acceptance:** Formal data contains only v2-replayed, human-reviewed targets with reproducible evidence bindings. Training becomes an optional deployment step, not a prerequisite for the improved runtime.

---

## Execution Order and Stop Rules

1. Execute Phase 1 locally and stop for human evidence-span review.
2. Execute Phase 2 locally until an index metadata gap requires explicit rebuild authorization.
3. Execute Phases 3–4 locally with focused fixtures and reviewed Phase 1 cases.
4. Execute Phase 5 only if measured unresolved semantic cases justify it.
5. Execute Phases 6–7 behind an explicit v2 policy flag; keep `POLICY_V1` available.
6. Request one consolidated server authorization for Phase 8 replay only after all local gates pass.
7. Enter Phase 9 only after the owner approves the qualification and holdout report.

At every stop, report only: completed results, focused verification, formal-asset impact, true blockers/human decisions, and the next action with its authorization requirement.
