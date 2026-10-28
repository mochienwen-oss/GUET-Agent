# Agent Platform Phase 2 Tool Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Directly migrate the five production tools and their helper code into `backend/agent/tools/`, make every tool return the common `ToolResult`, and delete the legacy tool/RAG/PPT locations without compatibility wrappers.

**Architecture:** Each tool owns one `@tool` entry, Pydantic input schema, pure observation renderer, internal `scripts/`, tests, and documented public surface. The explicit runtime Registry is the only executor and schema source. `agent/core/observation.py` is the only rendering entry; `Evidence` is constructed only from `ToolResult.sources`. This phase preserves the current main-graph behavior; autonomous ReAct routing and model-driven `create_ppt` handoff are Phase 3.

**Tech Stack:** Python 3.13, LangChain Core `@tool`, Pydantic v2, LangGraph (existing PPT subgraph only), pytest, Qdrant, sentence-transformers, BM25, python-pptx.

**Spec:** `docs/superpowers/specs/2026-10-13-agent-platform-redesign-design.md`

## Global Constraints

- Direct migration only: no `backend/tools/`, root `rag.py`/`vector.py` compatibility modules, import aliases, feature flags, or dual registries remain at the phase gate.
- External callers import a tool only through `backend/agent/tools/<name>/tool.py`; another tool must not import its sibling's `scripts/`.
- Every public tool is a `langchain_core.tools.BaseTool` created with `@tool(args_schema=...)` and returns `ToolResult`.
- Registry imports exactly five tools statically, rejects duplicate names at import, validates required schemas, and is the only source of OpenAI tool schemas.
- Success rendering must preserve the existing golden observation text byte-for-byte; status semantics may become stricter but may not silently collapse `tool_failed` into `no_evidence`.
- `Evidence` is built only from `ToolResult.sources`, never by parsing observation text.
- Source identity priority remains `chunk_id → text_hash → title + line + heading_path`.
- `compare_course` composes the public `query_course` entry and never imports its repository or normalization scripts.
- `create_ppt` migrates the existing subgraph, generation, rendering, QA, state, and delivery code into its tool package; Phase 2 does not yet replace the main graph with the final ReAct graph.
- No training-data rewrite, model training, server upload, service restart, RAG tuning, threshold change, or answer-style change in this phase.
- The repository has no Git metadata; use `docs/WORK_PROGRESS.md` and SHA-256 manifests instead of commit steps.

## Review Focus

1. Empty results, irrelevant results, partial results, invalid arguments, and runtime failures must remain five distinguishable states; tests in Tasks 1 and 7 pin them.
2. Search sources must retain `document_id`/`chunk_idx` as `chunk_id`, while sources without chunk text must use the approved fallback identity; tests in Tasks 2 and 4 pin both.
3. One missing or unresolved major in a comparison must not erase successful rows or claim a complete comparison; Task 3 pins partial evidence.
4. `create_ppt` missing goal/audience must fail validation before creating a task, while a valid call must produce a structured subgraph handoff; Task 6 pins both.
5. Admin endpoints and offline indexing/audit scripts must still reach the one moved RAG implementation; Task 4 includes import and maintenance smoke tests so the refactor cannot leave a second or broken retrieval path.

---

### Task 1: Finalize common tool contracts and the single observation entry

**Files:**
- Create: `backend/agent/core/errors.py`
- Create: `backend/agent/core/observation.py`
- Modify: `backend/agent/core/contracts.py`
- Modify: `backend/agent/core/__init__.py`
- Modify: `backend/agent/runtime/registry.py`
- Test: `backend/tests/agent_platform/test_core_contracts.py`
- Test: `backend/tests/agent_platform/test_observation.py`
- Test: `backend/tests/agent_platform/test_runtime_registry.py`

**Interfaces:**
- Produces: `ToolStatus`, `ToolError(code, detail, retryable)`, `ToolResult(tool, ok, status, data, sources, error, meta)`.
- Produces: `render_observation(result: ToolResult, renderer: Callable[[ToolResult], str]) -> str`.
- Produces: Registry `register(spec)`, `call(name, args) -> ToolResult`, `schemas() -> list[dict]`, and `render(result) -> str`.

- [ ] **Step 1: Write failing contract and status tests**

Assert `status="success"` is explicit, `error` is `ToolError | None`, unknown statuses are rejected, `tool_failed` does not contain “未检索到”, and all non-failure statuses delegate to the selected pure renderer.

- [ ] **Step 2: Run the focused tests and confirm the old optional-status contract fails**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_core_contracts.py backend/tests/agent_platform/test_observation.py backend/tests/agent_platform/test_runtime_registry.py -q`

Expected: failures identify the missing `ToolError`, missing observation entry, and legacy `tools` dependency in Registry.

- [ ] **Step 3: Implement the minimal common contract**

Use `Literal["success", "no_evidence", "partial_evidence", "irrelevant_result", "tool_failed"]`; reject unknown values at construction. `render_observation` owns only common failure wording and dispatch discipline, not tool-specific successful text.

- [ ] **Step 4: Convert Registry to static `BaseTool` specs**

Define `ToolSpec(tool: BaseTool, renderer: Callable, readonly: bool = True, cost: str = "low")`. Validation errors become `ToolError(code="invalid_args", retryable=False)` without invoking the tool; unknown names become `unknown_tool`; exceptions become `tool_failed` with retryability set only for the approved runtime classes.

- [ ] **Step 5: Run focused tests**

Expected: common-contract tests pass. Registry may temporarily have zero production registrations until Tasks 2–6; tests inject local fake specs.

- [ ] **Step 6: Record Task 1 in `docs/WORK_PROGRESS.md`**

Record interfaces and test counts; do not start a second renderer.

---

### Task 2: Migrate `query_course`

**Files:**
- Create: `backend/agent/tools/__init__.py`
- Create: `backend/agent/tools/query_course/__init__.py`
- Create: `backend/agent/tools/query_course/tool.py`
- Create: `backend/agent/tools/query_course/schemas.py`
- Create: `backend/agent/tools/query_course/renderer.py`
- Create: `backend/agent/tools/query_course/scripts/repository.py`
- Create: `backend/agent/tools/query_course/scripts/normalize.py`
- Move: `scripts/build_plan_db.py` → `backend/agent/tools/query_course/scripts/build_plan_db.py`
- Test: `backend/agent/tools/query_course/tests/test_tool.py`
- Test: `backend/agent/tools/query_course/tests/test_renderer.py`
- Test: `backend/agent/tools/query_course/tests/test_build_plan_db.py`

**Interfaces:**
- Produces: `QueryCourseArgs(major: str, course: str)` and `query_course_tool: BaseTool` named `query_course`.
- Produces: public `query_course_result(major: str, course: str) -> ToolResult` in `tool.py` for sibling composition.
- Consumes: read-only `backend/data/plan_db.json` and common contracts from Task 1.

- [ ] **Step 1: Move existing course matching tests to the final package and add status assertions**

Pin exact/prefix/substring matching, major/course candidates, complete card fields, source file/line, and no-evidence behavior. Capture the current course-card observation as the renderer golden.

- [ ] **Step 2: Run tests against the absent final package**

Expected: import failures only.

- [ ] **Step 3: Move normalization, repository access, and database builder**

Keep `join_key` and matching algorithms behavior-identical. Resolve `plan_db.json` from the backend root, not from the deeper package directory. The moved builder imports its sibling `normalize.py`; no top-level wrapper remains.

- [ ] **Step 4: Implement the public tool and pure renderer**

Return `success` for a card, `no_evidence` with candidates for missing major/course, and direct `Source(title=file, line=src_line, text=<canonical card facts>)`. Renderer must match the existing card golden byte-for-byte.

- [ ] **Step 5: Register and verify `query_course`**

Assert Registry's OpenAI schema preserves the approved name, description, required `major`/`course`, and rejects blank strings before execution.

- [ ] **Step 6: Run the moved builder dry test and focused suite**

Expected: deterministic builder fixture output unchanged; tool and renderer tests pass.

- [ ] **Step 7: Record Task 2 checkpoint**

Do not delete `backend/tools/plan.py` or `normalize.py` until Task 7 has switched every remaining consumer.

---

### Task 3: Migrate `compare_course` as public-tool composition

**Files:**
- Create: `backend/agent/tools/compare_course/__init__.py`
- Create: `backend/agent/tools/compare_course/tool.py`
- Create: `backend/agent/tools/compare_course/schemas.py`
- Create: `backend/agent/tools/compare_course/renderer.py`
- Test: `backend/agent/tools/compare_course/tests/test_tool.py`
- Test: `backend/agent/tools/compare_course/tests/test_renderer.py`

**Interfaces:**
- Produces: `CompareCourseArgs(course: str, majors: list[str])`, with at least two nonblank majors, and `compare_course_tool: BaseTool` named `compare_course`.
- Consumes: only `query_course_result` from Task 2; it must not import `query_course/scripts/`.

- [ ] **Step 1: Write failing composition tests**

Use a fake public course-query callable to prove each requested major is called once, input order is preserved, unresolved/absent rows remain visible, sources are not fabricated, and partial success yields `partial_evidence`.

- [ ] **Step 2: Write renderer golden tests**

Pin the current Markdown table and per-major source lines byte-for-byte for all-found, partly-found, and none-found cases.

- [ ] **Step 3: Implement the public composition**

Status rules: all requested majors found → `success`; at least one found plus any unresolved/not-open row → `partial_evidence`; no found rows → `no_evidence`. Sources are the successful child results' sources, deduplicated by approved source identity.

- [ ] **Step 4: Register and run focused tests**

Assert schema requires `course` and `majors`, `majors` has minimum length 2, and the Registry exposes the exact approved multi-major description.

- [ ] **Step 5: Record Task 3 checkpoint**

---

### Task 4: Move the complete RAG implementation under `search_regulations/scripts`

**Files:**
- Create: `backend/agent/tools/search_regulations/__init__.py`
- Create: `backend/agent/tools/search_regulations/tool.py`
- Create: `backend/agent/tools/search_regulations/schemas.py`
- Create: `backend/agent/tools/search_regulations/renderer.py`
- Move: `backend/rag.py` → `backend/agent/tools/search_regulations/scripts/retrieval.py`
- Move: `backend/vector.py` → `backend/agent/tools/search_regulations/scripts/indexing.py`
- Move: `backend/bm25.py` → `backend/agent/tools/search_regulations/scripts/bm25.py`
- Move: `backend/coverage.py` → `backend/agent/tools/search_regulations/scripts/coverage.py`
- Move: `backend/flows.py` → `backend/agent/tools/search_regulations/scripts/flow_expand.py`
- Move: `backend/refs.py` → `backend/agent/tools/search_regulations/scripts/reference_follow.py`
- Move: `backend/intent.py` → `backend/agent/tools/search_regulations/scripts/intent.py`
- Move: `backend/citations.py` → `backend/agent/core/citations.py`
- Modify: `backend/agent/tools/search_regulations/tool.py` (public maintenance facade)
- Modify: `backend/llm_client.py`
- Modify: `backend/main.py`
- Modify: `backend/routers/admin.py`
- Modify: `backend/routers/chat.py`
- Modify: retrieval/indexing scripts under `scripts/` that currently import root `rag` or `vector`
- Move tests: retrieval, vector, coverage, citation tests to their final owning packages

**Interfaces:**
- Produces: `SearchRegulationsArgs(query: str)` and `search_regulations_tool: BaseTool` named `search_regulations`.
- Produces from `tool.py`: named maintenance/diagnostic facade functions required by admin and offline scripts; no caller imports `scripts/` directly.
- Consumes: the current frozen retrieval settings unchanged.

- [ ] **Step 1: Add architecture tests for forbidden root RAG modules and internal imports**

Assert production has no root `rag.py`, `vector.py`, `bm25.py`, `coverage.py`, `flows.py`, `refs.py`, or `intent.py` at the phase gate; external production modules may import only `search_regulations/tool.py`, not its `scripts/`.

- [ ] **Step 2: Move modules mechanically and repair internal relative imports**

Do not alter thresholds, top-k values, chunking, fusion, reranking, gate, reference following, or flow expansion. Move cross-cutting citation/source-name functions to `agent/core/citations.py` and update answer-layer consumers there.

- [ ] **Step 3: Preserve chunk identity in the public result**

`search_regulations_tool` maps each retrieved context to a `Source` carrying `doc_id`, `chunk_id=f"{document_id}:{chunk_idx}"` when both fields exist, title, heading path, raw text, hash, and score. Observation text remains the existing block rendering.

- [ ] **Step 4: Add status and trace tests**

Nonempty relevant blocks → `success`; empty blocks → `no_evidence`; explicitly irrelevant diagnostics → `irrelevant_result`; retrieval exception → Registry `tool_failed`. Pin that query-rewrite text and trace metadata never become Evidence.

- [ ] **Step 5: Switch admin and offline maintenance consumers to the public facade**

Cover add/delete/reindex/sync/stats, calibration, local ingest, retrieval evaluation, chunk preview, and corpus/index audits. Add an import-smoke test for every migrated command module without connecting to Qdrant or loading the encoder.

- [ ] **Step 6: Run focused RAG tests**

Expected: existing pure retrieval/index/citation tests pass from final paths; frozen runtime parameters are unchanged; no root RAG import remains.

- [ ] **Step 7: Record Task 4 checkpoint**

This task is a location/contract migration, not the later RAG-quality tuning phase.

---

### Task 5: Migrate `query_empty_classroom`

**Files:**
- Create: `backend/agent/tools/query_empty_classroom/__init__.py`
- Create: `backend/agent/tools/query_empty_classroom/tool.py`
- Create: `backend/agent/tools/query_empty_classroom/schemas.py`
- Create: `backend/agent/tools/query_empty_classroom/renderer.py`
- Move: `backend/tools/cas.py` → `backend/agent/tools/query_empty_classroom/scripts/cas.py`
- Test: `backend/agent/tools/query_empty_classroom/tests/test_tool.py`
- Test: `backend/agent/tools/query_empty_classroom/tests/test_renderer.py`

**Interfaces:**
- Produces: zero-argument `query_empty_classroom_tool: BaseTool` named `query_empty_classroom`.
- Returns: `success` pointer data and no Evidence sources; it performs no login or network request.

- [ ] **Step 1: Write tests that prohibit accidental live login**

Patch the CAS client to raise if touched; invoking the public tool must still return the fixed pointer card. Assert schema has no fake date/time parameters.

- [ ] **Step 2: Move the pointer implementation and archived CAS helper**

Keep `ENTRY_URL`, `ROOM_FREE_PATH`, `NEED`, and observation text byte-for-byte. The CAS helper remains unimported by `tool.py` until a separately approved live-query feature exists.

- [ ] **Step 3: Register and run focused tests**

Expected: tool/renderer/golden tests pass and zero network calls occur.

- [ ] **Step 4: Record Task 5 checkpoint**

---

### Task 6: Migrate `create_ppt` and expose a structured subgraph handoff

**Files:**
- Create: `backend/agent/tools/create_ppt/__init__.py`
- Create: `backend/agent/tools/create_ppt/tool.py`
- Create: `backend/agent/tools/create_ppt/schemas.py`
- Create: `backend/agent/tools/create_ppt/renderer.py`
- Move: `backend/graph/ppt/subgraph.py` → `backend/agent/tools/create_ppt/scripts/subgraph.py`
- Move: `backend/ppt/model.py`, `generate.py`, `layouts.py`, `render.py`, `qa.py`, `delivery.py` → `backend/agent/tools/create_ppt/scripts/`
- Modify: `backend/graph/main_graph.py`
- Modify: `backend/routers/chat.py`
- Modify: `backend/routers/ppt.py`
- Move: PPT-specific tests into `backend/agent/tools/create_ppt/tests/`

**Interfaces:**
- Produces: `CreatePptArgs(goal: str, audience: str, page_count: int | None, style: str | None, constraints: list[str])` and `create_ppt_tool: BaseTool` named `create_ppt`.
- Produces: `create_ppt_result(...) -> ToolResult` with `data.handoff="ppt_subgraph"` and normalized brief; actual streaming execution remains `run_stream(...)` in the same public `tool.py` until Phase 3 consumes the handoff.
- Exposes through `tool.py` the task-status/confirm/token/download facade used by API routes.

- [ ] **Step 1: Write schema and handoff tests**

Blank goal or audience must fail before task creation. A valid request returns a deterministic handoff with the user's goal/audience preserved verbatim; no PPTX is generated by the schema-validation call itself.

- [ ] **Step 2: Move PPT implementation without changing event semantics**

Keep `ppt_task`, `ppt_ready`, `ppt_error`, `replace`, `calls`, and `[DONE]` behavior unchanged. Update internal imports to the final package and keep state-file ownership/path traversal/TTL checks intact.

- [ ] **Step 3: Switch current graph and API callers to `create_ppt/tool.py`**

The existing rule router is only a temporary Phase 2 caller; do not redesign it here. Phase 3 will replace it with the model's `create_ppt` call.

- [ ] **Step 4: Register the fifth tool and verify all schemas**

Assert exactly five unique names and that `create_ppt` is visible to Registry schemas with required goal/audience.

- [ ] **Step 5: Run PPT focused tests**

Cover clarification, state corruption, resume, evidence ledger sharing, render/QA, owner checks, token consumption, and event ordering from the final paths.

- [ ] **Step 6: Record Task 6 checkpoint**

---

### Task 7: Cut all production/training consumers to the Registry and delete legacy paths

**Files:**
- Modify: `backend/agent/runtime/qa.py`
- Modify: `backend/agent/runtime/registry.py`
- Modify: `backend/graph/main_graph.py`
- Modify: `训练集/_work/toolcall/build_tool_samples.py`
- Modify: `训练集/_work/toolcall/build_v7.py`
- Modify: `训练集/_work/toolcall/verify_train_v7.py`
- Modify: `scripts/check_tools.py`
- Delete: `backend/agent/core/adapters.py`
- Delete: `backend/tools/`
- Delete: emptied `backend/graph/ppt/`
- Delete: emptied `backend/ppt/`
- Delete: old root RAG/helper modules moved in Task 4
- Modify: `backend/tests/architecture/test_agent_package_layout.py`
- Modify: `docs/WORK_PROGRESS.md`
- Create: `_work/agent-platform-phase2-manifest.txt`

**Interfaces:**
- `qa.run_stream` consumes only `registry.schemas()`, `registry.call()`, and `registry.render()`.
- Training builders consume the same Registry schemas/calls/rendering as runtime; no hand-authored duplicate schema remains.

- [ ] **Step 1: Add final forbidden-path and duplicate-schema tests**

AST-scan production and active training scripts for `import tools`, root RAG imports, direct sibling `scripts` imports, and duplicate OpenAI schema literals. Assert all legacy paths are absent.

- [ ] **Step 2: Switch QA runtime and active sample builders**

Preserve observable role sequences and observation strings. Convert `ToolResult` to call logs/contexts from structured fields; never parse renderer text to recover sources.

- [ ] **Step 3: Delete the temporary adapter and all old paths**

Delete only after all imports point to final modules and focused tests are green. Do not leave wrappers “for safety”.

- [ ] **Step 4: Run Phase 2 focused tests**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/architecture backend/tests/agent_platform backend/agent/tools backend/tests/graph -q`

Expected: all pass.

- [ ] **Step 5: Run the full backend suite and import smoke**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests backend/agent/tools -q`

Expected: no new failures; the one documented stale citations assertion may remain only if its moved equivalent is intentionally unchanged. Import `main`, all routers, Registry, all five `tool.py` modules, and moved maintenance commands in one process.

- [ ] **Step 6: Replay the current tool golden set**

For the four pre-existing tool types, compare final observation output to `backend/tests/agent_platform/golden_as_observation.json`; require zero byte differences. For `create_ppt`, validate the new structured handoff and renderer against its new golden.

- [ ] **Step 7: Write the Phase 2 manifest**

Record final file hashes, five schemas, test counts, golden replay counts, forbidden-path scan, and the exact remaining known failure if any.

- [ ] **Step 8: Update progress and stop at the Phase 2 gate**

Mark Phase 2 complete only if all five tools are registered from final paths, every legacy path is absent, and no production caller bypasses Registry/ToolResult. Next action is the separate Phase 3 unified ReAct graph plan.

---

## Self-Review Result

- Spec coverage: Phase 2 covers the five final tool packages, direct deletion of old implementations, `@tool`, common result/observation/evidence contracts, explicit Registry, RAG ownership, and PPT handoff. Unified ReAct execution remains deliberately assigned to Phase 3.
- Type consistency: every tool returns the Task 1 `ToolResult`; Registry is the only schema/execution/render facade; `compare_course` consumes only Task 2's public result function.
- Review focus: all five listed failure classes have named tests in their owning task.
- Proportion: implementation bodies are intentionally omitted; the plan fixes signatures, ownership, migration order, and observable acceptance behavior only.
