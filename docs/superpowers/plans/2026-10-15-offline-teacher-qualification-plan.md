# Offline Teacher Qualification Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一份 24–30 条、冻结可复现、用于筛选离线 `assistant_target` teacher 的资格考试包。

**Architecture:** 复用现有 `ScenarioSpec` / Registry / replay / binding / manifest 契约，新增的 qualification 层只负责 rubric、覆盖门、候选问法库和冻结产物。候选问法先机械建库，场景与 rubric 由人审后再在 `school_docs_phase5` 上真工具重放；本计划不调 teacher、不产生答案。

**Tech Stack:** Python 3.13 本地测试，Python 3.10 deploy 真重放，Pydantic v2，pytest，JSONL，SHA-256，现有 Qdrant/Registry/replay 接口。

**Spec:** `docs/superpowers/specs/2026-10-15-offline-teacher-qualification-design.md`

## Global Constraints

- 本计划只冻结资格考试包；不调真实 teacher、不生成 answer candidate、不 promote、不 compile、不训练。
- EasyDataset 只提供 `regulations.single_candidates.jsonl` 的自然问法；它的 assistant 答案不得进入任何产物。
- `regulations.multiturn_repair_queue.jsonl` 不进本考试包，不把第 2/3 轮指代问句伪装成单轮。
- 资格场景必须真工具重放；不手写、缓存或回灌 ToolResult / observation。
- 考试包不进 `训练集/scenarios/`；只写 `_work/phase6/teacher_qualification/`。
- 不修改正式五资产、七个正式场景、Agent/RAG/工具 schema/索引参数。
- 真重放固定 `QDRANT_COLLECTION=school_docs_phase5` / `EMBED_DEVICE=cpu` / `RERANK_DEVICE=cuda`；vLLM 保持停止。
- `create_ppt` 只验 tool-terminal 结构，不携带 `assistant_target`。
- 真正 observation-driven ReAct、多轮 history 契约、Agent function calling 验收不在本计划内。
- 本目录不是 Git 仓库；计划中的「checkpoint」使用文件 SHA-256 + `docs/WORK_PROGRESS.md` 记录，不伪造 commit。

## Review Focus

1. **sidecar 或多轮队列被误当单轮场景** —— Task 2 对 single / calibration seed / acceptance 三种输入分开校验，明确拒绝 multiturn repair queue；Task 4 精确核对来源。
2. **rubric 重复/孤儿/覆盖标签拼错** —— Task 1 一对一与受控标签测试必须红后绿。
3. **pending 场景或已携带 target 的场景进冻结包** —— Task 1 结构门拒绝，不到 replay。
4. **两次重放的状态/binding 漂移** —— Task 3 真重放快照入 manifest，Task 4 双跑不一致即停止。
5. **用条数代替覆盖** —— Task 1 覆盖矩阵门和 Task 4 选择报告必须同时过，24 条但缺格仍失败。

---

### Task 1: Qualification Rubric 与覆盖门

**Files:**
- Create: `训练集/compiler/qualification.py`
- Create: `训练集/compiler/tests/test_qualification.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: `compiler.models.ScenarioSpec`、`compiler.models.BUCKETS`、`compiler.models.TOOL_TERMINAL_BUCKETS`。
- Produces: `QualificationRubric`、`QualificationPackError`、`load_rubrics(path)`、`derived_coverage_tags(spec, tool_names)`、`validate_static_pack(specs, rubrics, tool_names)`、`coverage_summary(specs, rubrics, tool_names)`。

- [ ] **Step 1: 为 rubric 形状写红灯**

  在 `test_qualification.py` 固定 `QualificationRubric` 字段：
  `scenario_id: str`、`coverage_tags: tuple[str, ...]`、`must_preserve: tuple[str, ...]`、
  `must_not_claim: tuple[str, ...]`、`required_disambiguation: tuple[str, ...]`、
  `review_points: tuple[str, ...]`；`extra="forbid"`，`scenario_id` / `coverage_tags` / `review_points` 不得空白，各数组内不得重复。

- [ ] **Step 2: 运行 rubric 红灯**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_qualification.py -q`
  
  Expected: collection/import 或字段校验失败，不是空测试集。

- [ ] **Step 3: 实现 rubric 模型与受控覆盖标签**

  `qualification.py` 定义 `STATIC_COVERAGE_TAGS` 为非工具标签的唯一集合，覆盖：
  `bucket:no_tool`、`bucket:clarify`、`bucket:create_ppt`、
  `state:success`、`state:no_evidence`、`state:partial_evidence`、
  `state:irrelevant_result`、`state:tool_failed`、`state:recovered_tool_failed`，
  以及 `risk:numeric_boundary`、`risk:entity_disambiguation`、`risk:multi_claim`、
  `risk:multi_source`、`risk:nonexistent_entity`、`risk:no_tool_hallucination`、
  `risk:clarification`、`risk:source_leak`。`tool:<name>` 不手抄第二份工具列表；
  由 `validate_static_pack(..., tool_names)` 对着 Registry/schema snapshot 实际名称校验并要求逐个覆盖。
  其他未知标签当场拒绝，不自动收录。

  其中 `tool:*` / `bucket:*` / `state:*` 不信任人工填写：`derived_coverage_tags`
  必须从 `ScenarioSpec.steps`、`bucket`、`expect`、`expected_evidence.state` 机械推导，
  rubric 中这三类标签必须与推导集合逐项相等；只有 `risk:*` 是人工审核标注。
  无步骤的 `no_tool` / `clarify` 只计对应 bucket，不借它们的记账末态
  `no_evidence` 冒充工具证据态覆盖；`state:recovered_tool_failed` 只在前置步真有
  `expect=tool_failed` 且最终恢复为非失败状态时推导。

  ⚠️ **`state:irrelevant_result` 的可达性前提（Task 4.3，2026-10-15）**：该状态在本任务前
  **没有任何生产者** —— `search_regulations_result` 当时写成
  `status = "success" if blocks else "no_evidence"`，把「候选被相关性门拒绝」和
  「语料里根本没有候选」压成了同一个 `no_evidence`。Task 4.3 已改为按检索 `reason` 映射：
  `{rerank_low, score_low, grader_no}` → `irrelevant_result`；
  `{empty, absent:<文档>, no_dense_signal}` → `no_evidence`；有块 → `success`。
  口径见设计文档 §4.2.1。**因此「现有 inventory 无合格 irrelevant_result 问法」是数据缺口，
  不是能力缺口** —— 不必再为此改覆盖门或删状态。

  ★ **Task 4.3.1 收紧（2026-10-15）**：上表是**穷举契约**，不是兜底白名单。表外的空结果
  `reason`（未知串 / `None` / 空串）**不再 warning + 回落 `no_evidence`**，改为**抛 `ValueError`**，
  由 Registry 既有边界统一转成 `tool_failed`（`ok=False` / `error.code=tool_failed`，渲染
  「不代表学校没有相关规定」）。**检索新增任何空结果 reason 时必须同步扩展映射表与测试**，
  否则上游会在首次真实命中时抛错 —— 这是有意的"吵醒"设计。口径见设计文档 §4.2.1 / §4.2.2。

- [ ] **Step 4: 为 pack 一对一和结构门写红灯**

  测试必须覆盖：重复 rubric id、孤儿 rubric、缺 rubric、重复 scenario id、
  `review_status != reviewed`、携带 `assistant_target`、小于 24 条、大于 30 条、
  24 条但缺必需覆盖标签、人工伪写了场景实际不具备的
  `tool:*` / `bucket:*` / `state:*` 标签。失败必须在任何 replay 之前发生。

- [ ] **Step 5: 实现 `load_rubrics` / `validate_static_pack` / `coverage_summary`**

  - `load_rubrics(path: str | Path) -> dict[str, QualificationRubric]`：报文件+行号，拒绝重复 id。
  - `derived_coverage_tags(spec: ScenarioSpec, tool_names: Collection[str]) -> frozenset[str]`：只从场景规格推导工具/bucket/状态标签。
  - `validate_static_pack(specs: Sequence[ScenarioSpec], rubrics: Mapping[str, QualificationRubric], tool_names: Collection[str]) -> None`：固定 24–30 条、一对一、全 reviewed、无 target、机械标签与场景一致、覆盖齐全。
  - `coverage_summary(specs: Sequence[ScenarioSpec], rubrics: Mapping[str, QualificationRubric], tool_names: Collection[str]) -> dict[str, int]`：机械标签按场景重算，risk 标签从 rubric 读取；工具名按传入值排序，静态标签按定义顺序，0 也写。

- [ ] **Step 6: 运行 Task 1 测试与全套回归**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_qualification.py 训练集/compiler/tests -q`
  
  Expected: 新测试全绿；原 561 条一条不减。

- [ ] **Step 7: 记录 checkpoint 并停止审核**

  将改动文件完整 SHA-256、红灯数、绿灯数、新接口写入 `WORK_PROGRESS`。不进 Task 2，等审核。

---

### Task 2: 候选问法库的确定性建表

**Files:**
- Create: `scripts/build_teacher_qualification_inventory.py`
- Create: `训练集/compiler/tests/test_teacher_qualification_inventory.py`
- Create: `backend/tests/agent_platform/runtime_tool_names.json`
- Modify: `backend/tests/agent_platform/test_runtime_registry.py`
- Create on execution: `_work/phase6/teacher_qualification/qualification.inventory.jsonl`
- Create on execution: `_work/phase6/teacher_qualification/qualification.inventory.manifest.json`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: `_work/phase6/questions/regulations.single_candidates.jsonl` + 6 个 reviewed 校准种子的显式路径（不含 `seed-react-001`）+ `测试集/agent_acceptance.json` + `backend/tests/agent_platform/runtime_tool_names.json`；Task 1 的受控标签仅用于展示合法选项，本步不自动贴语义标签。
- Produces: `load_tool_names(tool_names_path) -> tuple[str, ...]`、`inventory_rows(single_path, seed_paths, acceptance_path, tool_names) -> list[dict]`、`write_inventory(single_path, seed_paths, acceptance_path, tool_names_path, out_path, manifest_path) -> dict`。

- [ ] **Step 1: 写输入边界红灯**

  测试必须锁住：single 输入只允许 `origin="单轮"` + `round=1`；seed 必须 reviewed 且无 target；
  acceptance 只读 `id/question/want_tool/want_args`；非空 `want_tool` 必须属于传入的当前工具名称契约，
  `want_tool=null` + `want_args=null` 是合法的 no-tool/越界候选并保留。历史数据中已核实有 46 条
  `want_tool` 非空但 `want_args=null`：这些行保留自然问法，显式写
  `hint_complete=false` + `review_flags=["missing_want_args"]`，manifest 记
  `incomplete_tool_hints`；不得伪造参数，Task 4 必须重新人审工具与参数。参数对象完整时
  `hint_complete=true` + 空 `review_flags`。只有 `want_tool=null` 但 `want_args` 非空、未知工具、
  缺字段或非法类型才整体拒绝；不把 `must_include`、
  `must_not`、`source` 或旧回答带进 inventory。空问句、缺字段、非 JSON、重复输入要么被显式标成 exact duplicate，
  要么整体失败，不静默丢行；多轮 repair queue 当场拒绝。

- [ ] **Step 2: 写稳定 id 与排序红灯**

  single candidate 的 `candidate_id` 必须从规范 JSON `{source_kind, question, chunk, origin, round}` 的 SHA-256 得出；
  calibration seed 的 id 必须从 `{source_kind, scenario_id, question}` 得出。不使用行号、时间或随机数。
  acceptance 的 id 从 `{source_kind, id, question, want_tool, want_args}` 得出。
  输出按 `(source_kind, question, candidate_id)` 稳定排序；两次运行逐字节一致。

- [ ] **Step 3: 实现 inventory CLI**

  CLI 只接收 `--single`、六个显式 `--seed`、`--acceptance`、`--tool-names`、`--output`、`--manifest`；不接收 multiturn 输入、不扫描场景目录、不调 LLM、不调 Qdrant、不生成答案、不自动猜 bucket/risk tag。
  `--tool-names` 本轮固定使用 `backend/tests/agent_platform/runtime_tool_names.json`；该文件只记**有序的 7 个工具名**，不复制第二份大型 runtime schema。inventory 只解析这串名称，**不导入运行时 Registry**。
  ★ 原定的 `backend/tests/agent_platform/golden_tool_schemas.json` **不能**用作本输入：它是故意冻结的 v7 训练分布，只含旧 4 工具（`search_regulations` / `query_course` / `compare_course` / `query_empty_classroom`），而运行时 Registry 有 7 个。该文件**保持原样不动**，也不得扩成 7 工具。
  `backend/tests/agent_platform/test_runtime_registry.py` 新增的**唯一一致性门**负责证明 `runtime_tool_names.json` 逐项、逐序等于 `list(REGISTRY)` —— 这是全仓库唯一允许导入运行时 Registry 做该核验的地方；构建器自身仍不得导入它。
  acceptance 的 `want_tool/want_args` 只是可追溯的既有候选提示，Task 4 仍需人审和真重放，不自动当成正确场景。
  inventory 行显式带 `source_kind=single_candidate|calibration_seed|agent_acceptance`；manifest 记 9 个输入的逻辑名、行数、精确重复数和 SHA-256，不记绝对路径/时间戳。

- [ ] **Step 4: 运行单测与 955 行真实建表**

  Run tests first; then run CLI against `regulations.single_candidates.jsonl`.
  
  Expected: manifest 的 9 个输入 SHA 与磁盘一致；single 输入关系满足 `input rows = unique rows + exact duplicates`；6 个 seed 均有唯一 inventory 行；acceptance 的接纳/过滤行数守恒；工具名称契约的工具集合为当前 7 工具且逐项等于 `list(REGISTRY)`；第二次运行两产物逐字节一致。

- [ ] **Step 5: 扫描禁止依赖**

  源码 AST/字符串门断言 inventory 模块不导入 `llm_client`、Qdrant、torch、sentence-transformers、Agent Registry 或网络客户端。

- [ ] **Step 6: 记录 checkpoint 并停止审核**

  更新 `WORK_PROGRESS`，报告真实行数/重复数/哈希，不进 Task 3。

---

### Task 3: 冻结器、真重放快照与 manifest

**Files:**
- Modify: `训练集/compiler/qualification.py`
- Modify: `训练集/compiler/replay.py`
- Create: `scripts/freeze_teacher_qualification_pack.py`
- Create: `训练集/compiler/tests/test_teacher_qualification_freeze.py`
- Modify: `训练集/compiler/tests/test_replay.py`
- Modify: `训练集/scenarios/README.md`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: Task 1 的 `load_rubrics` / `validate_static_pack` / `coverage_summary`；Task 2 inventory + inventory manifest；`compiler.io.load_scenarios`；`compiler.replay.replay_step` / `validate_trajectory`；`compiler.binding.evidence_binding`；`compiler.manifest.schema_snapshot` / `sha256_file` / `sha256_text`。
- Produces: `validate_replay_results(spec, probed)`、`QualificationSelectionRow`、`load_selection(path, inventory)`、`ReplaySnapshot`、`probe_qualification_drafts(specs, replay=None)`、`replay_qualification_pack(specs, replay=None) -> tuple[ReplaySnapshot, ...]`、`freeze_pack(...) -> dict`；CLI 四份正式 pack 产物。

- [ ] **Step 1: 为「草稿 probe」与「正式轨迹」的共享校验写红灯**

  先在 `test_replay.py` 锁住：新 `validate_replay_results(spec, probed)` 对 pending 草稿也能校验 tool/order/status/末态/tool-terminal 结构，但不改审核状态；旧 `validate_trajectory` 仍保持当前精确顺序（先 tool-terminal 结构门，再 review 门，再 steps/tool/status/末态）并拒绝 pending/rejected，对同一 mismatch 保持原错误文本。
  两个入口必须复用同一个私有 step/status/末态校验实现，不允许 qualification 再写一套判据。

- [ ] **Step 2: 实现共享 replay 结果校验并跑原回归**

  `replay.py` 只做机械拆分：`validate_trajectory` 的 review 门和现有判据顺序不变；草稿 probe 调 `validate_replay_results`。
  Run `test_replay.py` + compiler 全套，Expected: 原有断言逐条保持。

- [ ] **Step 3: 为 replay snapshot 写红灯**

  `ReplaySnapshot` 只记 `scenario_id`、`tools`、`statuses`、`evidence_binding_sha256`；不存 ToolResult repr、observation 全文、绝对路径或时间。同输入 + 同 replay 字节一致。

- [ ] **Step 4: 实现 `probe_qualification_drafts` 和 `replay_qualification_pack`**

  两者都对每条 scenario 使用现有 `replay_step` 并用 `evidence_binding` 计算绑定。draft probe 调 `validate_replay_results`，因此允许保持 `pending`但绝不放行冻结；formal replay 调 `validate_trajectory`，要求已 reviewed。不写第二份状态校验器。

- [ ] **Step 5: 为 freeze 静态门写红灯**

  `QualificationSelectionRow` 固定字段：`candidate_id`、`decision=selected|rejected`、`reason`、`scenario_id|null`。
  selected 必须有 scenario_id，rejected 必须为 null；reason 不得空白。
  拒绝：输出目录中四份产物任一已存在、输入逻辑名重复、inventory 与它的 manifest 哈希不符、selection 有重复/孤儿 candidate id、selected scenario_id 与 scenarios 不一对一、语料身份缺字段、collection 不是 `school_docs_phase5`、points 不是 789、prompt version 空白。
  sampling 与 response schema 不从 CLI 自由传入，必须直接复用 `scripts/generate_answer_candidates.py` 的
  `SAMPLING` 与 `DRAFT_RESPONSE_FORMAT`；测试锁住 freeze 路径读到的就是这两份定义，
  两者的规范 JSON 值与 SHA-256 进 manifest。所有静态门先于 replay。

- [ ] **Step 6: 实现 `freeze_pack` 与 CLI**

  CLI 显式接受 draft scenarios/rubric/selection、Task 2 inventory + inventory manifest、**已批准的人工覆盖探针 sidecar**、输出目录、corpus fingerprint、prompt version；collection/points 必须在服务器实测而不是只信参数。输出按 `scenario_id` 稳定排序的：
  `qualification.scenarios.jsonl`、`qualification.rubric.jsonl`、`qualification.manifest.json`、`selection-report.md`。manifest 包含每场景 replay snapshot 和覆盖统计，不含 observation 全文。

  ★ **Task 4.0 补的接口（`--coverage-probes`）**：设计 §4 允许 `source_kind=human_coverage_probe`
  的人工探针，但本轮之前没有承载它的**输入**。现新增独立 sidecar
  `_work/phase6/teacher_qualification/qualification.coverage-probes.jsonl`：
  每行记 `probe_id` / `question` / `want_tool` / `want_args` / `gap_reason` /
  `review_status` / `reviewed_by`，以及由**规范身份字段**算出的 `candidate_id`
  （`reviewed_by` / `review_status` / `gap_reason` **不进身份**）。
  冻结时 selection 的合法 `candidate_id` 集合 = Task 2 inventory ∪ 已批准 probe；
  sidecar 的静态门与哈希记录**先于 replay**。**不得**按 `probe-` 前缀特判放行，
  **也不得**不登记 probe 却让对应场景进冻结包。

- [ ] **Step 7: 为写盘边界写红灯并实现**

  全部内存校验和 replay 通过后才 stage；manifest 最后 replace；任何失败清理未 replace 的 `.tmp`；replace 中途失败明确列出已替换/未替换，不伪装事务、不回滚已写文件。

- [ ] **Step 8: 可复现与禁止字段测试**

  两个不同根目录用相同逻辑名输入，四份产物逐字节一致；manifest/report 不含绝对路径、时间戳、主机名、用户名、token、endpoint 或完整 prompt。

- [ ] **Step 9: 运行 Task 3 测试与全套回归**

  Expected: 新测试全绿；compiler 原有测试数不减；`backend/tests/agent_platform` 117 条保持全绿。

- [ ] **Step 10: 记录 checkpoint 并停止审核**

  更新 README 的资格考试入口和 `WORK_PROGRESS`；不进 Task 4。

---

### Task 4: 编制、人审与冻结真实资格考试包

**Files:**
- Create: `_work/phase6/teacher_qualification/qualification.selection.jsonl`
- Create: `_work/phase6/teacher_qualification/qualification.scenarios.draft.jsonl`
- Create: `_work/phase6/teacher_qualification/qualification.rubric.draft.jsonl`
- Create after approval: `_work/phase6/teacher_qualification/qualification.scenarios.jsonl`
- Create after approval: `_work/phase6/teacher_qualification/qualification.rubric.jsonl`
- Create after approval: `_work/phase6/teacher_qualification/qualification.manifest.json`
- Create after approval: `_work/phase6/teacher_qualification/selection-report.md`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: Task 2 inventory；Task 3 freeze CLI；现有 6 条 reviewed 锚点场景。
- Produces: 规格定义的四份冻结产物，供后续 teacher 接入计划使用。

- [ ] **Step 1: 确定性分层与人工选题**

  从 inventory 中选题，保留 `candidate_id` / source kind / source file / source line / question / chunk 或既有 want_tool+want_args。`qualification.selection.jsonl` 为每个被考虑的候选写 `selected|rejected` 和非空理由；不得只记入选者。
  先用上述三类现有来源检查 7 工具覆盖；若仍缺工具（预期需特别核对 `list_courses`），
  可补最少量 `source_kind=human_coverage_probe` 行，但必须单独记缺口证据、问句和拟用工具/参数，并在 Step 5 由用户单独批准。

- [ ] **Step 2: 编制 24–30 条 draft ScenarioSpec**

  6 条校准锚点可通过复制并改新 `scenario_id`/来源标识纳入 pack，不修改正式种子。其余条目从已登记的 inventory / 人工覆盖探针来；参数只能来自问句或已核实语料，不得为让工具成功偷换专业/课程/实体。初稿保持 `review_status=pending`。

- [ ] **Step 3: 为每条 draft 编制 rubric**

  rubric 不写完整金标答案；只写 coverage tags、必须保留的原义片段、禁止主张、消歧要求和人审点。

- [ ] **Step 4: 在服务器执行审核前真重放调查**

  设备固定 CPU embedding / CUDA reranker，Qdrant 固定 `school_docs_phase5` 789 points，vLLM 停止。调 Task 3 的 `probe_qualification_drafts`：它用共享判据校验真工具结果，但不要求把 draft 提前标成 reviewed。此时不能生成正式 manifest，不能把 pending 当 reviewed。

- [ ] **Step 5: 停在人工场景审核门**

  交付一份审核表：每条问句、bucket、steps/参数/expect、真工具状态、证据预览、coverage tags、入选理由。在用户批准前，不得将 `review_status` 改成 `reviewed`。

- [ ] **Step 6: 根据人工裁决只改审核状态或被指定的错误字段**

  被驳回的场景保留在 selection report 中，不为凑数静默删记录。批准后的正式 draft 必须全部 `reviewed`，仍不携带 target。

- [ ] **Step 7: 服务器冻结运行两次**

  两次使用两个空输出目录，真重放每条场景；四份产物必须逐字节一致，每场景 evidence binding 一致。任一状态/binding 漂移即停止，不挑一轮当真值。

- [ ] **Step 8: 回传本地并运行最终验收**

  核对服务器/本地 SHA；运行 qualification 新测试、compiler 全套和 `backend/tests/agent_platform`；再核对正式五资产与七个正式场景哈希未变。

- [ ] **Step 9: 记录最终 checkpoint 并停止**

  `WORK_PROGRESS` 记录 pack version、覆盖矩阵、未覆盖项、四份产物哈希、重放环境、测试结果。停止等待下一份「teacher 接入与盲审」计划，不在本计划内调模型。

---

## Completion Gate

只有同时满足下列条件，Task 8C.2 才完成：

1. 四份冻结产物存在且服务器/本地 SHA 一致；
2. 24–30 条场景全部 reviewed、无 target，与 rubric 一对一；
3. 覆盖门通过，或因真工具不可稳定复现而停在未冻结状态（不伪造通过）；
4. 全部场景真重放两次，状态、顺序和 binding 一致；
5. 产物不含 EasyDataset assistant、多轮指代问句、observation 全文、凭据或环境路径；
6. compiler 原有 561 条测试不减且新测试全绿；agent_platform 117 条保持全绿；
7. 正式五资产、七个正式场景、Agent/RAG/工具/索引配置一字节未变；
8. 未调 teacher、未生成答案候选、未 promote/compile/训练。
