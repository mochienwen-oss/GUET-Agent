# Regulations RAG Targeted Repair (Phase 5A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以**当前 `_md/`** 为准建立 `school_docs_phase5`（旧集合 `school_docs` 保留不动），
在新索引上重跑 47 条 `search_regulations` 金标准 + 14 条负例，**按新 Trace 重新生成故障清单**，
交付构建报告、迁移后的金标准与阶段5A manifest。**不切生产。**

**Architecture:** 旧 Qdrant 索引与阶段4 报告是历史基线，不作可比要求。顺序：
**指纹硬门 → 表格单表列校验 → BM25 资产成对绑定 → 空转统计 → 建新集合 → 迁移金标准（3 条）→
新索引上重跑并重新诊断**。旧 M1–M4 **不得**作为新索引的故障结论。

**Tech Stack:** Python 3.13、Qdrant（新增集合，不动旧集合）、dense + BM25 + RRF + reranker、pytest。

**Spec:** `docs/superpowers/specs/2026-10-13-agent-platform-redesign-design.md` §7.2、§7.5、§9.3、§11 阶段5A。

**前置分析（已完成，只读）：** `_work/phase5a-corpus-fingerprint.md`、`_work/phase5a-corpus-survey.md`、
`_work/phase5a-index-build-scheme.md`、`_work/phase5a-gold-migration.md`、`_work/phase5a-impact-analysis.md`。

## Global Constraints

- 输入只有当前 `_md/`（91 份）；原始 PDF 仅作事实争议时的最终核对。
- **不覆盖、不删除 `school_docs`**；新集合 `school_docs_phase5`。
- **生产配置保持 `school_docs`**，本阶段不切换。
- **原始 `_md` 表格一个都不删。**
- 读 `_md` 必须用 `open(p, encoding="utf-8").read()`（CRLF→LF）；`rb().decode()` 会多切 337 块。
- 不改检索参数：embedding、BM25 算法、RRF、reranker、门控阈值、`top_k`、`recall_pool`、
  `rerank_pool`、`max_chunks_per_doc`。
- 不改冻结契约：registry schema、`as_observation` 渲染、消息形状、`RetrievalTrace`、`ToolResult`。
- 不修改培养方案结构化工具（`query_course` / `compare_course` 的 `scripts/`）。
- **索引构建工具放 `search_regulations/scripts/`**，是可重放的数据构建工具，不留 `_work/` 一次性脚本。
- 不动服务器；不训练；不上传。

## Review Focus

1. **指纹是硬门** —— `_md/` 在 19:08–19:16 仍被改写。构建前重取；与 19:16 后快照不一致就**停止报告**，
   不得用旧统计继续。
2. **表格校验失败必须隔离，不得静默入库** —— 27 张规章表任一张列数对不齐 → 写隔离报告并**停止**，
   报告原始表格。**禁止自动猜测错位单元格。**
3. **BM25 词表必须与集合成对** —— 新增 `BM25_VOCAB_PATH`；诊断新集合时显式指定新词表，
   不依赖共享单例 `model/bm25_vocab.json`。
4. **金标准迁移不得降难度** —— 只迁 B-004/B-005/B-007；15 条 `query_course` 只打
   `pending_phase5b` 标记，**不重录**。
5. **指标不达标即停在阶段门** —— Candidate Recall@120 <98% / Rerank Recall@5 <95% /
   门控误拒 >3% / 越界误放 >5%，任一触发就停，**不切生产**。

---

### Task 1: 指纹硬门

**Files:** Create `backend/agent/tools/search_regulations/scripts/corpus_fingerprint.py`; Test 同名 `tests/test_corpus_fingerprint.py`

**Interfaces:**
- Produces: `fingerprint(root) -> {"files": int, "tree_sha256": str, "chunked_sha256": str, "per_file": {rel: sha256}}`
- Produces: `digest_markdown(root)` / `digest_chunked(root)`，与 `diagnostics._corpus_snapshot` 同式。

- [ ] **Step 1: 写失败测试** —— 同一输入两次调用同值；改动一个字节后两个指纹都变；用 `open(encoding="utf-8")` 口径。
- [ ] **Step 2: 跑，确认红。**
- [ ] **Step 3: 实现**，读法用 `open(p, encoding="utf-8").read()`。
- [ ] **Step 4: 跑，确认绿。**
- [ ] **Step 5: 重取指纹并与记录比对**（91 份 / 树 `0b1aa77a…` / 分块 `68c72ce7…`）。
      **不一致 → 停止并报告。**

### Task 2: 单表逻辑列校验（27 张规章表）

**Files:** Create `.../scripts/table_check.py`; Test `.../tests/test_table_check.py`

**Interfaces:**
- Produces: `check_table(block: str) -> {"ok": bool, "reason": str, "cols": int}`
- Produces: `check_tables(units) -> {"passed": int, "failed": [{"chunk_id", "reason", "raw"}]}`

- [ ] **Step 1: 写失败测试** —— ① 表头 3 列、分隔行 3 列、数据行 3 列 → ok；
      ② 数据行多一列 → `ok=False`；③ 尾部空单元格可补齐 → ok；④ **同一张表内部**判定，
      与别的表列数无关（3 列表与 5 列表各自 ok）。
- [ ] **Step 2: 跑，确认红。**
- [ ] **Step 3: 实现** —— 单表为单位；允许**尾部**空单元格规范化补齐；**不猜**错位单元格。
- [ ] **Step 4: 跑，确认绿。**
- [ ] **Step 5: 对当前语料的 27 张规章表跑校验。**
      **任何一张失败 → 停止并报告原始表格**（把该表原文贴进报告）。

### Task 3: BM25 资产与集合成对绑定

**Files:** Modify `backend/config.py`、`.../scripts/bm25.py`; Test `.../tests/test_bm25_asset.py`

**Interfaces:**
- Produces: `settings.bm25_vocab_path: str = ""`（空 = 沿用 `model/bm25_vocab.json`，行为不变）。
- Produces: `bm25.vocab_path() -> str` 解析默认路径；`load_vocab`/`save_vocab` 默认走它。

- [ ] **Step 1: 写失败测试** —— 设 `BM25_VOCAB_PATH` 后 `vocab_path()` 返回它；不设时返回旧默认。
- [ ] **Step 2: 跑，确认红。**
- [ ] **Step 3: 实现** config 字段 + `bm25.vocab_path()`，默认参数改为 `None` → 走解析。
- [ ] **Step 4: 跑，确认绿。**
- [ ] **Step 5: 保存旧资产** —— 复制 `model/bm25_vocab.json` 为副本，记录其 SHA-256、
      旧集合语料指纹、检索配置（进 `_work/phase5a-asset-inventory.md`）。**不覆盖旧词表。**

### Task 4: 构建 `school_docs_phase5`

**Files:** Create `.../scripts/build_collection.py`; Test `.../tests/test_build_rules.py`

**Interfaces:**
- Produces: `plan_ingest(root) -> {"kept": [...], "excluded": {...}, "counts": {...}}`（**纯函数，不连库**）
- Produces: `build(target_collection, vocab_path, root, dry_run=True) -> report`

入库规则：

| # | 规则 | 判据 |
|---|---|---|
| R1 | 规章制度正文入库 | 非表格的 `_md/规章制度/*` |
| R2 | 规章有效表格**整表**入库 | `_is_table` **且** Task 2 列校验通过 |
| R3 | 培养方案叙述正文入库 | 非表格、且不在 R4/R6 排除节内 |
| R4 | 教学进程课程表**不入** | `heading_path` 命中 `教学进程计划表`/`培养计划学时`/`学时、学分统计表`/`学分统计表` |
| R5 | 先修后修关系图 OCR 堆叠**不入** | 堆叠判据（当前命中 0，判据保留） |
| R6 | **两类矩阵都不入** | `heading_path` 命中 `毕业要求对培养目标的支撑矩阵` / `课程计划与毕业要求的对应矩阵` |

- [ ] **Step 1: 写失败测试** —— `plan_ingest` 在合成语料上：矩阵被排除、教学进程表被排除、
      正文保留、列数不齐的表进隔离报告。
- [ ] **Step 2: 跑，确认红。**
- [ ] **Step 3: 实现 `plan_ingest`（纯函数，不连库、不加载模型）。**
- [ ] **Step 4: 跑，确认绿。**
- [ ] **Step 5: 空转统计** —— 对当前 `_md` 跑 `plan_ingest`，**取得实际入索引块数**。
      预期 ≈968（3384 − 1983 − 433），但**以实测为准，不写死**。
      记录：各类命中数、被排除块数、隔离表数。
- [ ] **Step 6: 建词表** —— 用**保留的块**重建 BM25 词表，存到新路径（不覆盖旧词表）。
- [ ] **Step 7: 写入** `school_docs_phase5`。
- [ ] **Step 8: 断言 `school_docs` 未被改动**（块数仍 3056）；写构建报告 `_work/phase5a-build-report.json`。

### Task 5: 迁移金标准（只 3 条）

**Files:** Modify `测试集/retrieval_gold.jsonl`; Test `.../tests/test_gold.py`

**Interfaces:**
- Produces: `expected_evidence` 规则 —— 每条必须落在**同一个块内**；跨块拆成多条。
- Produces: 15 条 `query_course` 行加 `"pending_phase5b": true`。

- [ ] **Step 1: 写失败测试** —— 断言 47 条里没有 `expected_evidence` 跨块（现 B-004/B-007 会红）。
- [ ] **Step 2: 跑，确认红。**
- [ ] **Step 3: 逐条改**，**读原文**（铁律 6）：
      - `B-004` → 拆成两个独立证据项（句子 / 散文形态换算规则），不要求跨块连续或固定顺序；
      - `B-005` → 只按当前 `_md` 重录标点，问题与事实要求一字不动；
      - `B-007` → 拆成跨标题边界的多个证据项。
- [ ] **Step 4: 15 条 `query_course` 只加 `pending_phase5b` 标记，不重录。**
- [ ] **Step 5: 跑测试到绿**；`gold.validate_case` 全过；14 条负例不动。

### Task 6: 新索引上重跑并重新诊断

**Files:** Create `_work/retrieval-diagnostics-phase5a.json`

**Interfaces:** Consumes `diagnostics.run()`（`QDRANT_COLLECTION=school_docs_phase5` +
`BM25_VOCAB_PATH=<新词表>`）。

- [ ] **Step 1: 跑 47 条 + 14 条负例**，记录 Candidate Recall@120、Rerank Recall@5、
      门控误拒率、越界误放率、`diagnoses` 分布。
- [ ] **Step 2: 逐条看失败者** —— 证据块 `chunk_id`、各层名次、原始块、期望证据。**不凭标签下结论。**
- [ ] **Step 3: 与旧报告并列对照**（看哪些结论变了），写进报告。
- [ ] **Step 4: 生成新故障清单**，每条指名机制与代码位置。**不沿用旧 M1–M4。**
- [ ] **Step 5: 执行门判定** —— 任一项不达标（<98% / <95% / >3% / >5%）→ **停在阶段门，不切生产。**

### Task 7: 阶段门与产出

**Files:** Create `_work/agent-platform-phase5a-manifest.txt`、`_work/phase5a-asset-inventory.md`;
Modify `docs/WORK_PROGRESS.md`

- [ ] **Step 1:** 冻结契约回放：observation 金标准 8/8、tools schema 冻结列、消息形状 —— 三条零字节差。
- [ ] **Step 2:** 可跑到的测试不新增失败（全量测试需 langgraph，本机无可用环境，**如实记录**）。
- [ ] **Step 3:** manifest 含：指纹、哈希清单、每条故障的 `chunk_id` 与名次、被否决的假设、
      **旧 M1–M4 与新结论的对照**。
- [ ] **Step 4:** 更新 `docs/WORK_PROGRESS.md`，**停在阶段门**。

---

## 执行门（硬性，逐步不可跳）

1. 构建前重取 `_md` 指纹。
2. 与 19:16 后快照不同 → **停止报告**，不用旧统计继续。
3. 一致 → 跑空转统计。
4. 空转通过 → 建 `school_docs_phase5`。
5. **不覆盖、不删除 `school_docs`。**
6. 重跑 47 条金标准 + 14 条负例。
7. 生成新 RetrievalTrace 与故障清单。
8. **不沿用旧 M1–M4** 作为新索引故障结论。
9. 指标不达标 → 停在阶段门，不切生产。
10. 全部通过也不切生产，先交报告等审核。

## 明确不做

- 不切生产、不训练、不上传服务器。
- 不重录 15 条 `query_course`（留给 5B）。
- 不改任何检索参数或门控阈值（**语料大改后是否需要重标定，只记录，不执行**）。
- 不修改培养方案结构化工具；不进入阶段5B。