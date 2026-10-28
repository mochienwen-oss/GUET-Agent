# Evidence Gate G0-G4 Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: execute this plan task-by-task with the repository's normal TDD workflow.

**Goal:** Produce one calibration-qualified evidence policy without changing the frozen G0 set, formal assets, corpus, index, or current production policy.

**Architecture:** Keep G0 sealed. Improve G1 by deriving compact query concepts and evaluating them against evidence grouped by document, while preserving hard scope and negative gates. Keep G2 to one deterministic title-based retry. Select exactly one policy on calibration only, then stop for human G0 labels and server-authorized G4 replay.

**Tech Stack:** Python 3.13 local project environment, pytest, JSONL offline artifacts.

**Spec:** Approved in chat on 2026-10-18; G0-G4 boundaries and hard gates are unchanged.

## Global Constraints

- Do not read G0 human labels during G1-G3 development.
- Do not modify `_md`, Qdrant, BM25, qualification freeze files, or formal training assets.
- Keep `POLICY_V1` behavior at zero drift until G4 passes.
- No LLM, SSH, GPU, dependency installation, or threshold relaxation.
- No query-specific allowlist and no deletion of calibration failures.

## Review Focus

- Conversational filler must not lower otherwise valid claim coverage.
- Overlapping tokenizer fragments must count as one concept, not several votes.
- Multi-claim support may aggregate selected chunks only within one document.
- Entity anchors must inspect the source title before declaring a scope conflict.
- Corpus-absent and out-of-scope rows must never become supported or exposable partial evidence.

---

### Task 1: Lock Audit Inputs

**Files:**
- Verify: `_work/phase7/evidence-gate-v1/independent-validation-v1/freeze-manifest.json`
- Verify: `_work/phase7/evidence-gate-v1/server-replay-manifest-v1.2.jsonl`

- [ ] Confirm the frozen identity SHA and that no final G0 labels are consumed by policy scripts.
- [ ] Confirm 426 unique replay rows and 441 planned executions because three qualification rows and twelve corrective-query rows use `replay_rounds=2`.

### Task 2: Repair Deterministic Claim Signals

**Files:**
- Modify: `backend/agent/tools/search_regulations/scripts/gate_signals.py`
- Test: `backend/agent/tools/search_regulations/tests/test_gate_signals.py`

- [ ] Add failing tests for conversational filler, maximal non-overlapping concepts, organization phrase boundaries, same-document multi-block support, and source-title entity matching.
- [ ] Implement the minimum deterministic extraction and aggregation changes.
- [ ] Run the signal tests and prove all new cases pass.

### Task 3: Preserve Gate Safety and Bounded Correction

**Files:**
- Modify only if required: `backend/agent/tools/search_regulations/scripts/evidence_gate.py`
- Modify only if required: `backend/agent/tools/search_regulations/scripts/corrective_query.py`
- Test: corresponding focused test files.

- [ ] Add tests proving semantic support can expose evidence only with a supported claim shape/concept and that negative scope conflicts stay closed.
- [ ] Keep one retry maximum and original-query reranking.
- [ ] Prove `POLICY_V1` decisions remain unchanged.

### Task 4: Rebuild Calibration Candidates

**Files:**
- Modify: `_work/phase7/evidence-gate-v1/build_candidate_v12.py`
- Output: existing v1.2 survey and candidate artifacts.

- [ ] Make offline claim aggregation byte-equivalent to production signal collection.
- [ ] Re-run the 240-policy calibration grid without G0 labels.
- [ ] Require all existing hard gates; emit `candidate-policy-v1.2.json` only if one policy passes.
- [ ] Verify deterministic rebuild and report recovered/rejected counts and G2 net benefit.

### Task 5: Prepare G4 Stop Point

**Files:**
- Verify: G0 review package and replay manifest.

- [ ] Do not label the 48 G0 rows as human-reviewed on the model's behalf.
- [ ] If G3 passes, lock the candidate SHA and request explicit server authorization for G4.
- [ ] If G3 still fails, report the remaining generic failure classes without relaxing gates.
