# Campus Agent v1 Runtime Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing GUET agent into Campus Agent v1 with safe in-session multi-turn recovery, token-budgeted context compression, dynamic tools, minimal Skills, and a single reviewed mixed-data training pipeline.

**Architecture:** Preserve the current retrieval, entity, tool, replay, qualification, and single-turn data layers. Add one shared `ConversationContext` contract used by runtime/compiler/replay, keep summaries untrusted, load tools from Registry and workflow restrictions from versioned Skill manifests, then qualify and train single-turn plus current-turn multi-turn examples together.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy/MySQL, Pydantic v2, LangGraph-style runtime graph, pytest, JSON/JSONL manifests.

**Spec:** `docs/superpowers/specs/2026-10-18-campus-agent-v1-runtime-contract-design.md`

## Global Constraints

- Do not rebuild `_md`, `school_docs_phase5`, BM25, entity catalog, aliases, families, or the seven existing tools.
- Preserve the cleaned 1,158 single-turn scenarios and the original 1,200-row audit input byte-for-byte.
- Training pending scenarios may produce pending candidates; only reviewed scenarios plus approved human-reviewed candidates may promote.
- Tool arguments must be emitted completely by the model; prior context validates values and never fills missing required arguments.
- Summary text is untrusted and cannot create `PriorEntity`, `PriorFact`, tool permission, or system instruction.
- Do not ingest EasyDataset assistant text as history facts or training targets.
- Phase 1 does not implement automatic cross-conversation memory.
- Do not update `docs/WORK_PROGRESS.md` or `docs/LATEST_REPORT.md`.
- Do not connect to a server, start Qdrant, use GPU/LLM, sync files, freeze packs, write formal training assets, delete files, or install dependencies without explicit current authorization.
- The workspace has no Git metadata. Replace commit steps with a concise changed-file list and SHA-256 checkpoint; do not initialize Git.
- Run only the task's targeted tests plus one production-path integration check. Run the compiler full suite once at the final compiler/formal-asset boundary.

## Review Focus

- Concurrent requests for one conversation must reject stale `revision` writes instead of overwriting newer context; covered in Task 3.
- A summary containing fake facts or prompt injection must not affect trusted state, tool allowlists, or system rules; covered in Task 4.
- A corrected entity must deactivate the old entity before the next call is validated; covered in Tasks 2 and 3.
- A Skill must not call tools outside `allowed_tools`, including tools the global Registry knows; covered in Task 5.
- Runtime and compiler must render and validate the same context identically, including compressed history hashes; covered in Tasks 4 and 6.

---

### Task 1: Separate Candidate Generation Eligibility from Promotion Eligibility

**Files:**
- Modify: `scripts/generate_answer_candidates.py`
- Modify if provenance needs a typed field: `训练集/compiler/answer_candidates.py`
- Confirm/strengthen: `训练集/compiler/promote.py`
- Test: `训练集/compiler/tests/test_generate_candidates_cli.py`
- Test: `训练集/compiler/tests/test_promote.py`
- Test: `训练集/compiler/tests/test_production_replay_path.py`

**Interfaces:**
- Consumes: `generate(..., batch_role: str)` and existing `ScenarioSpec.review_status`.
- Produces: `_select(specs, max_scenarios, *, batch_role)` returning answer-bearing, skipped, and tool-terminal scenarios; generation manifest records counts by source review status.
- Produces: promotion invariant `scenario.review_status == "reviewed"` AND candidate approved/reviewed.

- [ ] **Step 1: Modify one existing gate test and add one missing test**

  Modify `test_pending_and_rejected_scenarios_touch_neither_tools_nor_llm` so training pending is selected while rejected is skipped before replay/LLM. Add one qualification-specific test proving a pending qualification scenario remains unselected. Reuse `test_every_candidate_is_pending_with_no_reviewer` and `test_an_unreviewed_scenario_is_refused`; do not duplicate them.

- [ ] **Step 2: Run the focused tests and record the real failures**

  Run only the two changed node IDs plus the two reused safety nodes; do not run both complete files.

  Expected before implementation: only the new training-pending selection assertions fail.

- [ ] **Step 3: Implement the minimal selection change**

  Change `_select` to receive `batch_role`. For `training`, include pending and reviewed, exclude rejected, then separate tool-terminal. For `qualification`, preserve reviewed-only behavior. Record `eligible_by_review_status`, `selected_by_review_status`, and skipped reasons in the manifest; do not mutate source scenarios or add an approval CLI.

- [ ] **Step 4: Verify production dry-run identities without calling an LLM**

  Run the existing dry-run path against the cleaned batch. Assert 1,158 eligible inputs, 1,111 answer-bearing, 47 tool-terminal, 0 qualification exclusions, and no candidates written during dry-run.

- [ ] **Step 5: Run the production-path integration test**

  Run: `pytest 训练集/compiler/tests/test_production_replay_path.py::test_one_alias_scenario_through_generate_promote_compile -q`

  Expected: reviewed approved fixture still promotes; pending source fixture is mechanically refused by promote.

- [ ] **Step 6: Record changed-file SHA checkpoint and continue**

  Do not stop unless the gate cannot distinguish training from qualification or promote admits a pending source scenario.

### Task 2: Define ConversationContext v1 and Trusted-State Transitions

**Files:**
- Create: `backend/conversation_context.py`
- Modify: `backend/entity_prior.py`
- Test: `backend/tests/test_conversation_context.py`
- Test: `backend/tests/test_multiturn_context.py`

**Interfaces:**
- Consumes: `entity_prior.PriorContext`, `PriorEntity`, `PriorFact`, `PriorUnresolvedMention`.
- Produces: `ConversationContext`, `PendingTask`, `UserCorrection`, `CompressedHistory` Pydantic models.
- Produces: `apply_turn_update(context, *, entities, facts, unresolved, correction, pending_task) -> ConversationContext`.
- Produces: `context_binding_payload(context) -> dict` containing versions, revision, structured state, summary hashes, and recent-message identity.

- [ ] **Step 1: Add only the missing ConversationContext tests**

  Reuse all ten existing `test_multiturn_context.py` tests. Add tests only for: `apply_turn_update` deactivating a corrected entity and incrementing revision; summary text not creating trusted facts; summary identity/revision changing the existing conversation binding. Do not duplicate prior-source, multi-candidate, compiler/runtime agreement, or single-turn regression coverage.

- [ ] **Step 2: Run the tests and verify model/type failures are real**

  Run: `pytest backend/tests/test_conversation_context.py backend/tests/test_multiturn_context.py -q`

- [ ] **Step 3: Implement frozen Pydantic models and one transition function**

  Use `context_version="conversation-context/1"` and `summary_version="conversation-summary/1"`. Reuse `PriorContext`; do not copy its trust rules. Keep `summary` as plain untrusted text plus `covered_turns`, `source_hash`, and `summary_sha256`.

- [ ] **Step 4: Verify all context tests pass**

  Expected: focused tests pass with no production graph or database changes yet.

- [ ] **Step 5: Record SHA checkpoint and continue**

### Task 3: Persist Context and Wire Cross-User-Turn Recovery into Runtime

**Files:**
- Create: `backend/conversation_context_store.py`
- Modify: `backend/db.py`
- Modify: `backend/agent/runtime/state.py`
- Modify: `backend/agent/runtime/graph.py`
- Modify: `backend/routers/chat.py`
- Test: `backend/tests/test_conversation_context_store.py`
- Test: `backend/tests/agent_platform/test_runtime_multiturn_context.py`

**Interfaces:**
- Consumes: `ConversationContext` and `apply_turn_update` from Task 2.
- Produces: `load_context(db, conversation_id) -> ConversationContext`.
- Produces: `save_context(db, conversation_id, context, *, expected_revision: int) -> ConversationContext`; stale revision raises a dedicated conflict error.
- Extends: `agent.runtime.graph.run_stream(..., prior_context=None, pending_task=None, skill_context=None)` without breaking existing callers.

- [ ] **Step 1: Write four persistence and migration tests**

  Pin: an existing conversation with null context loads a clean v1 context; save increments revision; stale revision is rejected; unknown context version fails closed. Because context is stored on the Conversation row, do not add a redundant context-deletion test.

- [ ] **Step 2: Add the minimal Conversation columns/migration**

  Add versioned JSON context and integer revision to `Conversation` using the existing lightweight migration pattern. Do not create a long-term-memory database or copy Message rows into the context JSON.

- [ ] **Step 3: Write two graph-level end-to-end tests**

  Cover: a two-turn missing-field clarification that persists `pending_task` and resumes with a complete model-emitted call; a correction sequence where a later reference uses only the replacement major. Reuse existing multi-candidate, forged/tampered prior, compiler/runtime agreement, and missing-argument tests instead of duplicating them. Turn count is data, not a separate code path; long-history behavior belongs to Task 4.

- [ ] **Step 4: Wire load -> graph -> update -> optimistic save**

  Replace the fixed `limit(6)` behavior with a temporary context-window call that Task 4 will implement. The graph receives trusted prior context; the model still emits complete arguments. At turn completion, derive only enumerated trusted sources and persist the next revision.

- [ ] **Step 5: Run targeted runtime integration**

  Run: `pytest backend/tests/test_conversation_context_store.py backend/tests/agent_platform/test_runtime_multiturn_context.py backend/tests/agent_platform/test_entity_resolution_graph.py -q`

- [ ] **Step 6: Record SHA checkpoint and continue**

### Task 4: Add Token-Budgeted Context Window and Untrusted Summary Provider

**Files:**
- Create: `backend/agent/runtime/context_window.py`
- Create: `backend/agent/runtime/context_summary.py`
- Modify: `backend/agent/runtime/graph.py`
- Modify: `训练集/compiler/multiturn.py`
- Test: `backend/tests/agent_platform/test_context_window.py`
- Test: `训练集/compiler/tests/test_multiturn_compile.py`

**Interfaces:**
- Produces: `build_context_window(context, current_query, *, token_budget, estimator) -> ContextWindow`.
- Produces: `ConversationSummaryProvider.summarize(previous, messages) -> CompressedHistory`.
- Produces: `render_context_messages(context_window) -> list[dict]`; runtime and compiler import the same renderer.

- [ ] **Step 1: Write five compression tests**

  Pin: below budget makes zero summary calls; above budget keeps complete tool-call/observation pairs; structured trusted state always remains; injected instructions or unsupported numbers in a summary gain no authority; runtime/compiler rendering is byte-identical. Use the last test's fixture as the single 8-12 turn integration case; do not add another long-history test.

- [ ] **Step 2: Implement a token-estimator boundary and provider interface**

  Keep the estimator injectable. Do not add a new dependency. Summary generation is a separate model call surface, not part of the main answer stream; no real network call occurs in tests.

- [ ] **Step 3: Bind summary identity into conversation binding**

  Extend `conversation_binding` with context version, revision, summary version/hash/source hash, covered turns, and recent-message range. Do not remove existing history/prior/current evidence fields.

- [ ] **Step 4: Run the five targeted tests, including the 8-12 turn rendering fixture**

  Run: `pytest backend/tests/agent_platform/test_context_window.py 训练集/compiler/tests/test_multiturn_compile.py -q`

- [ ] **Step 5: Record SHA checkpoint and continue**

### Task 5: Introduce Minimal Versioned Skills and Dynamic Tool Allowlisting

**Files:**
- Create: `backend/agent/runtime/skills.py`
- Create: `backend/agent/skills/campus_information_query.json`
- Create: `backend/agent/skills/create_presentation.json`
- Modify: `backend/agent/runtime/graph.py`
- Test: `backend/tests/agent_platform/test_skills.py`

**Interfaces:**
- Produces: `SkillManifest` with `skill_id`, `version`, `description`, `instructions`, `allowed_tools`, `required_inputs`, `side_effect_level`, `approval_policy`.
- Produces: `load_skill(skill_id) -> SkillManifest` and `validate_skill_call(skill, tool_name) -> None`.
- Consumes: existing Registry schemas; does not duplicate tool schemas in Skill files.

- [ ] **Step 1: Write four Skill contract tests**

  Pin: allowlisted tool passes; globally registered but non-allowlisted tool is blocked; unloaded Skill instructions are absent; Skill version changes call audit/conversation binding. Verify dynamic Registry lookup through these interfaces; do not add a brittle source-text test asserting that tool names never appear in the system prompt.

- [ ] **Step 2: Implement strict manifest loading**

  Reject unknown fields, duplicate IDs, unknown tool names, blank versions, and invalid side-effect/approval combinations. Register only `campus_information_query` and `create_presentation`.

- [ ] **Step 3: Wire Skill context into prompt rendering and call validation**

  Keep stable identity/safety/evidence rules in the system prompt. Inject only the selected Skill instructions and its allowed Registry schemas. Include Skill ID/version/allowlist in call audit and conversation binding.

- [ ] **Step 4: Run targeted Skill and graph tests**

  Run: `pytest backend/tests/agent_platform/test_skills.py backend/tests/agent_platform/test_runtime_multiturn_context.py -q`

- [ ] **Step 5: Run one consolidated local regression checkpoint**

  Run the entity resolver/calls, runtime graph, candidate gate, promote, and multi-turn targeted sets once. Do not run unrelated backend full suites.

- [ ] **Step 6: Stop only if local behavior conflicts with the spec; otherwise proceed to Task 6 local preparation**

### Task 6: Close the Multi-Turn Qualification Pack and Materialize the First Batch

**Files:**
- Modify: `_work/phase7/batch-training-multiturn-v1/qualification.multiturn.scenarios.draft.jsonl`
- Create/modify: `_work/phase7/batch-training-multiturn-v1/build_multiturn_scenarios.py`
- Create: `_work/phase7/batch-training-multiturn-v1/batch-training-multiturn-v1.scenarios.jsonl`
- Create: `_work/phase7/batch-training-multiturn-v1/multiturn-generation.manifest.json`
- Modify only as required: `训练集/compiler/multiturn.py`
- Test: `_work/phase7/tests/test_multiturn_v1.py`

**Interfaces:**
- Consumes: frozen local contracts from Tasks 2-5 and existing 160-group identity list.
- Produces: 18-22 pending qualification scenarios and 250-350 pending current-turn training scenarios.

- [ ] **Step 1: Extend qualification coverage without changing existing valid cases**

  Add 4-6 representative cases for 4/6/8-12 turns, correction after compression, unsupported summary fact, Skill selection/denial, and historical prompt injection. Keep every row pending with null assistant target.

- [ ] **Step 2: Materialize the 160-group first batch deterministically**

  Use only user messages, approved historical targets, real observations, and trusted prior context. Short cases may have null summary. Do not ingest EasyDataset assistant content.

- [ ] **Step 3: Run four compact batch checks**

  Use one whole-file schema/identity test (unique IDs, catalog identities, trusted prior sources, pending/null targets, no EasyDataset assistant), one qualification/eval exclusion test, one two-directory byte-identical rebuild test, and one local current-turn replay test. Report failing scenario IDs from within each batch test rather than creating one pytest function per row.

- [ ] **Step 4: Prepare one server replay manifest**

  Include the two existing EasyDataset qualification cases, 29 numeric-premise checks, and all new server-dependent qualification cases. Do not connect to the server. Qualification scenarios require double replay; the 29 numeric-premise candidates require one evidence audit, not double replay. Ordinary server-dependent training scenarios require one real replay, not double replay.

- [ ] **Step 5: Stop and request one consolidated server authorization**

  The request must cover read-only identity probe, task-specific Qdrant copy/start/stop, GPU if needed, double replay, evidence audit, exact cleanup, and return of audit artifacts. Do not freeze or call Teacher under this authorization unless separately included by the user.

### Task 7: Generate and Review Teacher Candidates (Authorization Gate)

**Files:**
- Candidate outputs only under `_work/phase7/`; no formal targets in this task.
- Reuse: `scripts/generate_answer_candidates.py`

**Interfaces:**
- Consumes: 1,158 single-turn scenarios and the approved/frozen multi-turn qualification plus first batch.
- Produces: 1,111 pending single-turn answer candidates, 47 tool-terminal identities, and 250-350 pending multi-turn current-turn candidates.

- [ ] **Step 1: Obtain explicit server/Qdrant/DeepSeek authorization**

- [ ] **Step 2: Run the consolidated server evidence work once**

  Fail on state mismatch, drift, binding mismatch, unsupported numeric premise, or Skill/context-contract divergence. Precisely stop only task PIDs and remove only the task directory.

- [ ] **Step 3: Freeze multi-turn qualification only after human approval**

- [ ] **Step 4: Generate single-turn and multi-turn candidates**

  Candidates remain pending. The Teacher generates only the current assistant turn and never generates summaries or rewrites history.

- [ ] **Step 5: Apply mechanical gates before human review**

  Reject empty/unsupported answers, binding mismatch, unknown evidence, stale entity use, scope errors, duplicate source prose, `.md` leakage, silent alias choice, prompt-injection compliance, and qualification/eval leakage.

  Reuse existing candidate-gate tests. Add new focused test cases only for summary-derived unsupported facts, inactive-entity use, and Skill allowlist violation.

- [ ] **Step 6: Produce one compact human review artifact**

  Show current query, necessary history/summary, active entities, calls, observations, answer, rubric, and risk flags. Do not write candidate decisions without human input.

### Task 8: Promote, Compile, Train, and Evaluate (Formal-Asset Authorization Gate)

**Files:**
- Formal writes only after explicit authorization: `训练集/scenarios/targets.jsonl`, `训练集/scenarios/promote.ledger.jsonl`, `训练集/full_sft.jsonl`, `训练集/quarantine.jsonl`, `训练集/manifest.json`.

**Interfaces:**
- Consumes: human-reviewed scenarios and approved candidates.
- Produces: mixed single/multi SFT with provenance and context/Skill identities.

- [ ] **Step 1: Obtain explicit formal-asset write authorization**

- [ ] **Step 2: Promote only rows passing the dual review gate**

- [ ] **Step 3: Compile once and run the compiler full suite once**

  Manifest must report single/multi/tool-terminal counts, source distributions, quarantine, system prompt SHA, tool schema SHA, Skill manifest SHA, context version, and summary version.

- [ ] **Step 4: Present the pre-training report and obtain training authorization**

  Include row counts, single/multi ratio, tool/Skill/state distribution, quarantine, base model, LoRA config, estimated GPU time, and all data/config identities.

- [ ] **Step 5: Train one mixed Campus Agent v1 checkpoint**

- [ ] **Step 6: Run unified acceptance**

  Cover single-turn tools, 2/4/6/8-12 turn recovery, correction, compression, aliases, clarification, no-evidence, tool failure/recovery, Skill allowlisting, PPT, and evidence safety.

## Approved Test Budget

- Task 1: modify one existing generator test, add one qualification-pending test, and reuse three existing safety/integration tests.
- Task 2: add two or three ConversationContext assertions and reuse the existing ten multi-turn contract tests.
- Task 3: add four persistence tests and two end-to-end cross-turn tests.
- Task 4: add five compression/rendering tests; one of them is the sole 8-12 turn integration fixture.
- Task 5: add four Skill tests.
- Task 6: add four batch-level tests; qualification double replay happens once, numeric-premise evidence audit once, ordinary training replay once.
- Task 7: reuse existing candidate gates and add only three new risk cases.
- Task 8: run the compiler full suite once, immediately before/with formal compile; do not repeatedly run it in Tasks 1-7.

Do not require every Task to manufacture a red failure. A red/green cycle is required only when a real new behavior or reproduced defect exists.

## Execution Stop Points

CC may execute Tasks 1-6 local preparation continuously after reading the spec and this plan. It must stop at Task 6 Step 5 for server authorization, at Task 7 Step 3 for human qualification approval if not already granted, at Task 7 Step 6 for answer review, at Task 8 Step 1 for formal-asset authorization, and at Task 8 Step 4 for training authorization.
