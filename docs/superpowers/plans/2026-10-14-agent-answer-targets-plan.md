# Task 5.2 实施计划：答案目标与完整 SFT 编译

> **For agentic workers:** 逐任务执行，每个任务先写红灯测试再最小实现。
> 步骤用 `- [ ]` 跟踪。

**Goal:** 把编译器从「轨迹前缀」升级为「确定性 full SFT 编译器」，并把 LLM 答案生成
彻底移出 `compile.py`。

**Architecture:** B 方案。答案候选由**独立入口**生成（会调 LLM，落盘为 `pending`），
经人工/独立规则审核后由 `promote.py` 冻结进 `ScenarioSpec`；`compile.py` 重放工具、
重算 evidence binding、校验 target，最后追加 assistant 并输出 `full_sft`。
**`compile.py` 的执行路径里不得出现任何网络调用。**

**Spec:** `docs/superpowers/specs/2026-10-14-agent-answer-targets-design.md`

**运行环境（统一）:**
```
D:\venv-guet-ppt\Scripts\python.exe
```
不用裸 `python`（Panaconda 缺 `langchain_core`，含 registry 的测试会**收集失败**）。

## Global Constraints

- 不训练可见 `Thought:` 或任何内部推理；完整样本最后一个角色必须是 `assistant`。
- LLM 生成的 target 一律 `pending`；只有人能写 `reviewed`。
- `compile.py` 不调用 LLM、不改写答案、不自动补 target、不在 binding 不一致时放行。
- `evidence_binding_sha256` 与 `artifact_kind` 的口径由设计 §5 / §7.3 定义，不得就地改。
- 新数据继续使用 `registry.schemas()`（七工具）；`training_schemas()` 只复现 v7。
- observation 只能来自 `registry.call()` + `registry.render()`；不手写、不让 LLM 生成。
- 产物必须**连跑两次字节一致**（Task 5.1.1 已建立该口径）；因此**不用时间戳**。
- 服务器默认只读；未获明确授权不上传、不训练、不部署。
- 当前目录不是 Git 仓库：以文件 SHA-256 + 测试命令 + `docs/WORK_PROGRESS.md` 做检查点。
- 已知基线失败 `backend/tests/test_citations.py::test_block_suffix_does_not_ask_for_citation_markers`
  不属本阶段；不得新增失败。

## Review Focus

1. **`compile.py` 的网络隔离**：任何 import 或调用链把 LLM 客户端带进编译路径即失败。
2. **kind 由 `expected_evidence.state` 推导，不建第二个「运行末态真相源」**（设计 §4.1）。
3. **`no_tool` / `clarify` 按 `bucket` 优先**（设计 §4.2）：`NO_TOOL_STATE` 只作记账值。
   错在这里会教出「闲聊也答『学校没有规定』」这种最坏兜底。
4. **`steps` ↔ `kind` ↔ `binding` 的联合语义归 compiler admission**（设计 §5.3 / §7.4）：
   模型层**只校验字段自身**。若在装载时拒掉，`target_kind_mismatch` 与
   `evidence_binding_mismatch` 会变成**不可达**的 reason。**不许**凭 `target.kind`
   反推「有没有工具」—— 那样 kind 一错，binding 的判据跟着错，两个错互相掩护。
5. **隔离理由按固定优先级归因**（设计 §7.4）：六档顺序，一个场景只归**第一个**命中的原因。
6. **不得为了「像 ReAct」给中间轮塞 assistant**：那是 Task 5.3 的范围（设计 §9）。
7. **`targets.jsonl` 三条硬规矩**（设计 §6.3）：target 重复 / 孤儿 target / 同 ID 后写覆盖。

---

## Task 1: `AssistantTarget` 数据模型与合法映射

**Files:**
- Modify: `训练集/compiler/models.py`
- Create: `训练集/compiler/tests/test_target_model.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `TargetKind`（7 值 Literal）；`AssistantTarget`（Pydantic 模型）；
  `target_kind(bucket: str, expected_state: str) -> TargetKind`。
- Preserves: `ScenarioSpec` 现有字段与 `check_structure` model_validator 一行不动；
  只**新增**可选字段 `assistant_target: AssistantTarget | None = None`。
- ★ 本任务**只做模型与映射**：不建 `targets.jsonl` 内容、不写 `load_scenarios` 的合并逻辑
  （那是 Task 4）、不碰 binding / candidate / promote / compile_full。

- [ ] **Step 1: 写红灯测试**

  `test_target_model.py` 断言：

  - `no_tool` + `expected_evidence={"state":"no_evidence"}` → 仍映射 `direct_answer`；
  - `clarify` 同理 → `clarification`；
  - `stop_and_recover` + `expected_evidence={"state":"success"}` → `grounded_answer`；
  - `success` → `grounded_answer`；`partial_evidence` → `partial_answer`；
    `no_evidence` → `no_evidence_answer`；`irrelevant_result` → `irrelevant_result_answer`；
    `tool_failed` → `tool_failed_answer`；
  - **★ `tool_failed` 与 `no_evidence` 的 kind 不得互换**（TDD #11）；
  - `llm_candidate` 缺 `model` 或 `prompt_version` → 拒；
  - `human` / `deterministic` 带 `model` 或 `prompt_version` → 拒；
  - `content` 为空白 → 拒；`extra` 字段 → 拒；
  - `evidence_binding_sha256` 非 `None` 时必须严格匹配 `^[0-9a-f]{64}$`；
  - **★ 结构合法但语义不匹配的 target 必须能装载**（Task 1.1）：
    `no_tool + grounded_answer + 64 位 binding`、`工具场景 + direct_answer + binding=None`、
    `工具场景 + grounded_answer + binding=None`、`no_tool + direct_answer + 64 位 binding`；
  - **旧种子不带 `assistant_target` 仍全部可加载**。

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_target_model.py -q`
  Expected: 全红（模块与模型都还不存在）。

- [ ] **Step 2: 最小实现**

  在 `models.py` 加 `TargetKind`、`AssistantTarget`、`target_kind()`、
  `ScenarioSpec.assistant_target`。**复用** `ReviewStatus`，不新抄一份。
  ★ **只做字段自身校验**：不按 `kind` 判绑定、不做 `steps` 联合校验（Task 1.1）。

- [ ] **Step 3: 复跑**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests -q`
  Expected: 全绿，且 **Task 5.1 / 5.1.1 的 80 条一条不红**。

**停止条件：** 若给 `ScenarioSpec` 加字段导致任何既有种子装载失败，**停**，
不要改种子来迁就。

**回滚：** 删 `test_target_model.py`，`git`-无关地把 `models.py` 恢复为
「不含 `AssistantTarget` / `assistant_target`」的版本（改动集中在一个文件，逐块可撤）。

---

## Task 2: 规范化 evidence binding 纯函数

**Files:**
- Create: `训练集/compiler/binding.py`
- Create: `训练集/compiler/tests/test_binding.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `binding_rows(spec, probed) -> list[dict]`；
  `binding_sha256(rows) -> str`；`evidence_binding(spec, probed) -> str | None`。
- **纯函数**：不导入 Qdrant / torch / config / LLM 客户端。

- [ ] **Step 1: 写红灯测试**

  `test_binding.py` 断言：

  - 序列化参数逐条可观察：`ensure_ascii=False` / `sort_keys=True` / `separators=(",", ":")`
    （对一段含中文的固定输入断言**逐字**输出，再断言其 SHA-256 等于手算常量）；
  - **调换两步 observation 的顺序 → 哈希改变**（TDD #8）；
  - **改工具名 / 改参数 / 改 status → 哈希各改变**（TDD #9）；
  - **observation 文本相同但工具或参数不同 → 哈希不同**（TDD #10）；
  - `steps=[]` → `evidence_binding(...) is None`；
  - `arguments` 字典键序不同 → **哈希相同**（这是规范化要保证的）；
  - 同输入连算两次 → 哈希相同。

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_binding.py -q`

- [ ] **Step 2: 最小实现**

  `binding.py`：只做字典构造 + `json.dumps` + `hashlib.sha256`。**不引入任何依赖。**

- [ ] **Step 3: 复跑**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests -q`

**停止条件：** 若为了让哈希「好看」而想加规范化步骤（裁剪空白、折叠换行等），**停** ——
那会让「observation 变了但哈希没变」，正是要防的。规范化只做设计 §5.2 列的五条。

**回滚：** 删 `binding.py` 与其测试；`compile.py` 尚未引用它，无连带。

---

## Task 3: answer candidate 产物契约与 pending 生成接口

**Files:**
- Create: `训练集/compiler/answer_candidates.py`
- Create: `训练集/compiler/tests/test_answer_candidates.py`
- Create: `训练集/_work/phase6/answer_candidates/`（产物目录，含 `.gitkeep` 不必；目录由程序创建）
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `CandidateStatus` / `DraftAnswer` / `CandidateRow` / `DraftOutcome` /
  `build_catalog()` / `draft_candidate(...)` / `write_candidates(...)`。
- ★ 复用而不重建：`agent.core.evidence.EvidenceLedger`（evidence_id）、
  `agent.core.citations.build_source_block`（来源块**唯一**渲染器）与
  `strip_model_sources`（模型自写来源行的**唯一**剥离实现）。
- ★ **本任务不接真 vLLM**（服务器不可用）。`draft` 是注入的
  `Callable[[str], str]`，测试一律用替身。
- ★ **`batch_id` 是必填参数**：由调用方显式传入，签名里**不得有默认值**，
  函数内**不得**读时钟或按日期拼串（设计 §6.1）。
- ★ **来源归属**（设计 §6.4）：模型**只指认 `evidence_id`**，标题由
  `build_source_block()` 产出；**不重建** `short_title` / `clean_path` / 书名号规则，
  也**不**把 `ToolResult.sources` 全列出来。

- [ ] **Step 1: 写红灯测试**

  `test_answer_candidates.py` 断言：

  - **LLM 候选落盘后 `candidate_status` 与 `proposed_target.review_status` 都必须是 `pending`**
    （TDD #12）；draft 想把状态升格 → 那两个字段是**未知字段**，整条进 redteam；
  - **调用方不传 `batch_id` → 直接报错**；模块源码 ast 里**不得**出现 `time` / `datetime`；
  - 轨迹不合法（未复核 / 步数不符 / 工具不符 / 步状态不符 / 末态不符）
    → **抛错且 `draft` 调用 0 次**；
  - 来源块**逐字等于** `build_source_block` 的输出；
    模型自写的「资料来源：《假文件》」被剥除，最终只剩脚本那份；
  - **模型只写来源行 → `empty_content`；剥完仍有残留 → `unstrippable_sources`**
    （`strip_model_sources` 只从**最后一条**截断，更早那条会留下）；
  - 未知 `evidence_id` → redteam；grounded/partial 未指认 → redteam；
    非 grounded/partial 指认来源 → redteam；可见 `Thought` → redteam；非法 JSON → redteam；
  - redteam 行保留 `scenario_id` / `batch_id` / `risk_flags` / `raw_draft` / binding；
  - candidates 与 redteam 的 `scenario_id` **交集为空**；
  - 两个输出即使 0 行也**真实存在**且哈希正确（空串哈希，不写 `null`）；
  - 连跑两次字节一致；调用前后种子 SHA-256 不变。

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_answer_candidates.py -q`

- [ ] **Step 2: 最小实现**

- [ ] **Step 3: 复跑**

**停止条件：** 任何把「提审即通过」的默认值写进代码的做法都**停** ——
`candidate_status` 的默认值只能是 `pending`。

**回滚：** 删该模块与测试；产物目录可整目录删除（不含任何已审核数据）。

---

## Task 4: 人工审核后的 promote / apply

**Files:**
- Create: `训练集/compiler/promote.py`
- Create: `训练集/compiler/tests/test_promote.py`
- Modify: `训练集/compiler/io.py`（`load_targets` + `load_scenarios(paths, tools, targets)`）
- Modify: `训练集/compiler/replay.py`（`validate_trajectory`，Task 3/4/5 共享）
- Modify: `训练集/compiler/answer_candidates.py`（审核元数据 + 改调共享验证）
- Modify: `训练集/compiler/models.py`（`TargetRow`）
- Modify: `训练集/scenarios/README.md`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `promote(candidates_path, scenario_paths, targets_path, ledger_path,
  *, tools=None, replay=None) -> PromoteSummary`。
- Consumes: Task 2 的 `evidence_binding`、Task 3 的候选行、`replay.validate_trajectory`。
- ★ 落盘目标是**独立机器拥有文件** `训练集/scenarios/targets.jsonl`（设计 §6.2 / §6.3），
  种子文件一律不动。
- ★ 同时产出 `load_scenarios(paths, tools, targets=...)` 的**合并**能力（按 `scenario_id`
  合并到内存 `ScenarioSpec`）与三条硬规矩的校验。

- [x] **Step 1: 写红灯测试**

  `test_promote.py` 断言：

  - `candidate_status != "approved"` 的行**一律不晋升**，进台账并给理由码（TDD #13）；
  - **promote 会重新重放并重算 binding**；与候选里存的不符 → 不晋升
    （断言「工具被调用过」以证明它真的重放了，不是照抄文件里的哈希）；
  - promote **逐字搬运** `content` / `kind` / `evidence_binding_sha256`
    （晋升前后逐字比对，改一个字即红）；
  - promote 由 `approved` **确定性构造** `review_status="reviewed"`；
    **它自己不得从任何其它状态推出 `reviewed`**；
  - **每次晋升写审计台账**（条数、`scenario_id`、批次、理由码与产物**逐条对得上**）；
  - 连跑两次写出字节一致；
  - **三条硬规矩**（设计 §6.3）各一条红灯：
    - 同一 `scenario_id` 出现两次 → 装载报错并指出行号；
    - 孤儿 `scenario_id` → 装载报错；
    - 同 ID 后写 → **不静默覆盖**，报错。

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_promote.py -q`

- [x] **Step 2: 最小实现**
- [x] **Step 3: 复跑**

**红灯实测（Task 4 执行记录）：** 首次收集即 `ImportError`（`promote` / `validate_trajectory`
`TargetRow` 都还不存在）—— 按 Task 3.1 的口径，**不接受「逻辑上必红」**，
先补上纯接口骨架（常量 + 签名 + 字段，行为留空）再跑，得到真实行为红灯：

```text
test_promote.py            35 failed / 4 passed
test_replay.py              6 failed / 12 passed
test_answer_candidates.py   9 failed / 118 passed   ← Task 3 原有的 113 条仍全绿
合计 50 failed
```

实现后：`test_promote.py` 39 passed，compiler 全套 **320 passed**。

**停止条件：** 若发现需要「promote 顺手修一下 kind 或 content/hash」，**停**并报告 ——
那说明审核环节没审全，不该由搬运环节兜底。

**回滚：** 删 `promote.py` 与测试、删台账与 `targets.jsonl`；未晋升的候选原样还在。

---

## Task 4.1: promote 的数据完整性修补（2026-10-14）

**定位：** **不是扩展功能**，而是修 Task 4 验收中发现的**三个数据完整性缺陷**。

| 缺陷 | 修法 |
|---|---|
| ① 已有 `targets.jsonl` 里的**孤儿** target 能一路被搬进新文件 | 入口加 **A 组**检查（复用 `io.orphan_target_ids`，不在装载路径才判） |
| ② 台账每批**覆盖式**写：第二批会把第一批的历史冲掉 | 台账改**累计**；**B 组**：`batch_id` 不可变，同批次重跑整体拒绝 |
| ③ 两文件不是事务，崩在中间后无人发现 | **C 组**：入口双向核对 targets ↔ ledger，不一致即抛 `AuditInconsistency` 停下 |
| ④ ledger 的 `replayed` 同时表示「没重放」与「重放了但 0 次」 | 拆成 `trajectory_checked` + `tool_calls_executed` |

**Files:**
- Modify: `训练集/compiler/promote.py`
- Modify: `训练集/compiler/io.py`（抽出 `orphan_target_ids`）
- Modify: `训练集/compiler/tests/test_promote.py`
- Modify: 设计 §6.2 / §6.9、`训练集/scenarios/README.md`、`docs/WORK_PROGRESS.md`

**不改：** `compile.py` / `binding.py` / `citations.py` / `replay.py` / `answer_candidates.py` /
七个种子 / 正式 `targets.jsonl`。

- [x] **Step 1: 写红灯并实际运行**（不接受「逻辑上必红」）
- [x] **Step 2: 最小实现**
- [x] **Step 3: 复跑**

**红灯实测：** `test_promote.py` **22 failed / 35 passed**（全部是行为红灯，无收集错误 ——
被改的模块本来就存在）。实现后 **56 passed**，compiler 全套 **337 passed**。

**停止条件：** 若发现需要「promote 顺手修一下 kind / content / hash / targets」，
**停**并报告 —— 那说明审核环节没审全，不该由搬运环节兜底。
★ 本轮同样**不实现自动恢复**：C 组只负责**可靠发现**，恢复留给人。

---

## Task 5: 确定性 full SFT 编译与 quarantine

**Files:**
- Modify: `训练集/compiler/compile.py`
- Create: `训练集/compiler/tests/test_full_sft.py`
- Modify: `训练集/compiler/tests/test_replay.py`（`compile_scenario` 轨迹测试 → `build_trajectory`）
- Modify: `训练集/compiler/tests/test_compile.py`、`tests/test_admission.py`（出口与调用签名）
- Modify: `训练集/scenarios/README.md`、设计 §7、`docs/WORK_PROGRESS.md`

**Interfaces（Task 5 定稿：显式依赖注入）：**

```python
build_trajectory(spec, *, system_prompt, replay=None) -> (messages, probed)
compile_full(spec, *, system_prompt, replay=None) -> dict
compile_dataset(inputs, output, manifest_path, quarantine_path,
                *, targets_path, system_prompt) -> BuildSummary
```

★ **裁决：接口矛盾用显式依赖注入解**。旧 `compile.py` 从 `llm_client` 导入
`SYSTEM_PROMPT`，而本计划又要求 `compile.py` 不依赖 `llm_client`。
于是 `system_prompt` **必填、无默认值、不复制不缓存**，由调用方显式传生产那句；
`targets_path` 同样**必填**（不扫目录猜、不静默当空）。
`compile.py` **不得导入 `llm_client` / `answer_candidates`**，也不得新增提示词常量。
- ★ **待合并点已合**：`answer_candidates._validate_trajectory` 已在 Task 4 迁到
  `replay.validate_trajectory`，本任务**直接复用**，不再另写校验器。
- ★ **不设 `artifact_kind` 可选 mode，不保留 `trajectory_prefix` 生产模式**（设计 §7.3）。
- ★ `test_replay.py::test_no_tool_scenario_has_only_system_and_user` **最小修改为测
  `build_trajectory`**：语义（没步骤就不产生工具轮）不变，被测对象从公开接口换成内部函数。
  **不为保住一个测试而保留旧公开接口。**

- [x] **Step 1: 写红灯测试**
- [x] **Step 2: 最小实现**
- [x] **Step 3: 复跑全套**
- [x] **Step 4: 网络隔离的机械证明**

**红灯实测（不接受「逻辑上必红」）：** 首次收集即 `ImportError`（新接口尚不存在）——
先补**纯接口骨架**（常量 + 签名，行为留空）再跑，拿到真实行为红灯：
`test_full_sft.py` **50 failed / 8 passed**。实现后 **58 passed**；compiler 全套 **394 passed**。

**停止条件：** 若为了让某个场景通过而想放宽 binding 判据或自动补 target，**停**并报告。

**回滚：** `compile.py` 的改动分四块（拆函数、出口 mode、新 reason、full 追加），
逐块可撤；`test_full_sft.py` 整文件可删。

**Task 5.1 补丁（同轮验收后）：** quarantine 的 `evidence_mismatch` 明细**删掉
`mismatch_kind`** —— 它要靠**重新推导**校验逻辑才能得出，属「在 `compile.py` 里再写一套
分类器」。保留 `expected_state` / `observed_state` / `failed_tools`（当次轨迹的客观快照，
不决定 reason）与 `detail`（共享校验器的**原始错误文本**）。新增两条行为红灯：
步数不一致、工具名不一致（都必须是 `evidence_mismatch`，且记录里没有 `mismatch_kind`）。

---

## Task 6: manifest、可复现性与 provenance

**Files:**
- Modify: `训练集/compiler/compile.py`（provenance + 稳定路径 + 安全落盘）
- Create: `训练集/compiler/tests/test_manifest.py`
- Modify: `训练集/compiler/tests/test_admission.py`（仅被出口/签名推翻的部分）
- Modify: `训练集/scenarios/README.md`、设计 §7.3.1、`docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: manifest 增加 `system_prompt{sha256,chars}` / `inputs[].name` /
  `targets{name,exists,rows,sha256}` / `quarantine by_reason`（八项固定顺序）/
  `target_provenance`（四个分布）；`BuildSummary.inputs` 改为**逻辑名称**。

★ **provenance 只记「足以解释这份数据由什么生成」的东西**，并且**不记**：
时间戳、随机 `run_id`、用户名、机器名、临时目录、绝对路径、`ToolResult` 的 `repr`、
**完整 system prompt**、`reviewed_by` 等人工身份信息。
`system_prompt` 的哈希算在 **UTF-8 原始字节**上 —— 不 trim、不规范化换行。

★ **路径稳定性**：一律只记 `Path(path).name`；多个场景输入**重名**在工具调用与写盘前
明确拒绝；**不得**用字符串替换临时目录伪造稳定性。
两个不同根目录、同名同内容 → 三份产物**逐字节一致**。

★ **`quarantine.by_reason`** 八项恒定出现、顺序 = 准入优先级、和 == `rows`、
来自真实 quarantine 行、未知 reason **整体失败**。

★ **`target_provenance`** 只统计**真正进入 output** 的 target；`null` 保持 JSON `null`；
条目排序稳定；**output 行仍不带这些审计字段**。

★ **安全落盘**：路径互斥先查（重放与写盘前）→ 同目录临时文件 + flush + fsync →
基于 staged 文件算哈希 → `output` → `quarantine` → **`manifest` 最后** replace。
**跨文件仍不是事务**；失败清理临时文件，不留 `.tmp`；**不实现自动恢复**。

- [x] **Step 1: 写红灯测试**
- [x] **Step 2: 最小实现**
- [x] **Step 3: 复跑**

**红灯实测：** `test_manifest.py` **21 failed / 8 passed**（无收集错误）。
实现后 **29 passed**；compiler 全套 **427 passed**。

**停止条件：** 任何为让哈希稳定而跳过真实内容的做法（如把隔离行换成计数）都**停**。

**回滚：** manifest 段改动集中，可按 diff 回退。

**Task 6.1 补丁（同轮验收后）**：

1. **replace 失败后的临时文件清理**：原来只有 stage 失败那条路清理临时文件，
   replace 中途失败会把 `.tmp` 留在磁盘上。改为**一份共用的清理函数**
   （`_discard_staged`）在两处都用，并**记录已成功替换的目标**，
   失败时如实报出「已替换 X、未替换 Y」，**不回滚、不补写**。
   三条红灯分别让**第 1 / 2 / 3 次** `os.replace` 失败（用**计数替身**，
   不拿「父目录是个文件」冒充 —— 那测到的是 stage 失败）。
2. **文档承诺修正**：「任何失败都不留半成品、不留 `.tmp`」过头了 ——
   改为「stage 失败：正式文件不变；replace 失败：**可能已有部分正式文件被替换**，
   三份之间没有事务」。
3. **Qdrant `UserWarning` 定性并消除**：不是环境噪音，是
   `test_full_sft.py::test_a_step_count_mismatch_is_evidence_mismatch` 的夹具
   走 `real_target` 真重放了 `search_regulations`，从而
   `indexing._get_client()` 创建了 `QdrantClient` 并在后台线程探测服务器版本。
   **根因是纯单测漏了替身**，因此只改**该测试的夹具**（换成不碰 Qdrant 的工具，
   并改用 `bare_spec`）—— **不修改生产 Registry / RAG / 工具代码，不加任何 warning filter**。

---

## Task 7: 少量 fixture 验证（不做批量生产数据）

**Files:**
- Create: `训练集/scenarios/tests_fixtures/`（或复用 `tmp_path`，**优先**）
- Modify: `训练集/compiler/tests/test_full_sft.py`
- Modify: `训练集/scenarios/README.md`（补 `assistant_target` 与 `full_sft` 的说明）
- Modify: `docs/WORK_PROGRESS.md`

**要点：**
- 用**不超过 5 条**人工 fixture 端到端验证「候选 → 审核 → promote → full SFT」；
- fixture 里**不得**写入任何真实校内事实答案的批量内容 —— 它们只用于验证机制；
- 全流程不得触碰 `训练集/scenarios/*.jsonl` 的既有种子。

- [ ] **Step 1: 写红灯测试**（端到端链条）
- [ ] **Step 2: 补 README 的字段与产物说明**
- [ ] **Step 3: 复跑**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests -q`

**停止条件：** 若 fixture 开始需要「先跑一批 LLM 才有素材」，**停** ——
那是 Task 8 的事，且属于外部前置。

**回滚：** 删 fixture 与新增测试；README 的补充是纯增量。

**Task 7 实际执行（2026-10-15）：**

- **只新增一个文件** `训练集/compiler/tests/test_pipeline_fixture.py`（26 条）；
  **未动任何生产 `.py`**（`test_full_sft.py` 本轮也没改 —— 它的角色已由新文件承担）。
- 5 条 fixture 全为合成内容，覆盖 `no_tool` / `clarify` / `single_tool` /
  `react_multi_step` / `stop_and_recover`（第 5 条人工裁决为 **rejected**）。
- 端到端**经过真实文件边界**：候选 → 人工审核工作副本 → promote → targets + 台账
  → compile_dataset → full_sft + quarantine + manifest。
- 确定性重放派发器贯穿 candidate / promote / compile 三阶段，**三阶段逐字同值**；
  执行期间 `socket` 与 `QdrantClient` 构造被换成「调用即失败」，实测 0 次。
- **首次运行不是全绿**（1 passed / 25 errors，随后 2 failed → 1 failed），
  **三次红灯全部是测试夹具自身错误**，无接口契约不兼容、无生产缺陷。
- 两条**行为理解修正**（非代码缺陷）已记入 README 与 WORK_PROGRESS：
  ① `steps` 非空就有 evidence binding（`tool_failed` 也是）；
  ② compile 会重放「已复核但无 target」的场景，故 `stop_and_recover` 归 `target_missing`。
- `训练集/compiler/tests` **457 passed**；
  `-W error::UserWarning` 下 **457 passed, 2 warnings**（无 UserWarning）；
  `backend/tests/test_citations.py` 仍是 1 failed（**已知陈旧**，未变）。

---

## Task 8（已拆分）：真实候选生成与真机验收

★ **原 Task 8 在服务器可用之前整体无法执行。** 2026-10-15 按依赖把它拆成三段：

| 段 | 内容 | 前置 |
|---|---|---|
| **8A** | 本地实现并验证**唯一**的 LLM 候选生成 CLI（**不发真实请求**，全走注入替身） | 无 —— **已完成** |
| **8B** | 服务器恢复后的**双模型小样本盲对照**（`base-qwen` vs `guet-qwen`） | 服务器 + 用户授权 |
| **8C** | 选定模型与 prompt 后生成**正式** pending 候选 | 8B 的结论 |

★ **口径更正**：Task 7 的 457（现 502）passed **只证明离线流水线闭环**，
**不**证明模型生成的答案质量。模型质量由 Task 8B 的人工盲读判定。

### Task 8A：候选生成 CLI（2026-10-15 完成）

**Files:** `scripts/generate_answer_candidates.py`（新增）+ 对应测试。

- CLI **只做**：读参数 → 装载场景 → 真实重放 → `draft_candidate` → `write_candidates`；
  风险分类 / 证据目录 / binding / 来源渲染 / `batch_id` 正则 / 轨迹校验 / manifest 哈希
  **一律复用 `compiler/`**（测试断言 import 的是同一个函数对象）。
- 只产 `pending`；**没有** `--approve` / `--promote` / `--compile` / 改 `review_status` 的开关。
- `batch-id` / `model` / `prompt-version` / `vllm-url` **必填无默认**。
- 截断规则：**已复核**场景按 `scenario_id` 升序 → 取前 N 条（排序**先于**截断）。
- 失败一律整体失败（非 200 / 超时 / 缺 `choices` / 空 `content` → `LlmCallFailed`），
  **不留半成品**；token 只从环境变量 `GUET_VLLM_API_KEY` 读。
- 产物**只有三份**：`<batch>.candidates.jsonl` / `.redteam.jsonl` / `.manifest.json`。

### Task 8A.1：manifest 单一化与真机前安全收口（2026-10-15 完成）

**裁决：选方案（b）** —— 删除 `<batch>.run.json`，把 `model` / `prompt_version` /
`sampling` / 输入哈希 / 场景统计**并进现有 manifest**。理由：manifest 是该批候选
**唯一**的审计入口，不许有两个可漂移的真相源。

- `write_candidates(batch_id, candidates, redteam, out_dir, *, provenance=None)`
  —— 新槽**受约束**：`phase` / `artifact_kind` / `batch_id` / `candidates` / `redteam`
  **不得**被覆盖（当场拒，不做「后写的赢」）；`None` 时 manifest **连键序都**与从前相同。
- **批次不可覆盖**：同 `batch_id` 的三份产物任一已存在 → 在重放与 LLM 调用**之前**拒绝，
  **不提供 `--force`**。门放在 **CLI 入口**，不改 `write_candidates` 的旧调用方语义。
- **采样参数**：候选生成固定 `temperature=0`（稳定、可审核的监督标签），
  `top_p` / `top_k` / `repetition_penalty` 显式定义、显式发送、显式记入 manifest；
  请求体与 manifest **同一份定义**。8B 的两个模型必须用完全相同这一套。
- **写盘边界**：三份产物**不构成事务**，不宣称原子。已写出的原样留着，
  错误里**如实列出**已写出/未写出，不做伪回滚、不留 `.tmp`。
- 输入逻辑名重名 → 在任何重放与 LLM 调用前拒绝（复用 `compile.py` 的实现）。

★ **Task 8B 探针必须验证服务器接受完整 payload。** 若服务器拒绝某个采样字段，
**停止并报告**，不得静默删掉参数继续跑 —— 本轮只做离线请求体测试，没猜服务器行为。

### Task 8B 预案（**只写在这里，不执行**）

★ **服务器恢复后不要直接选一个模型全量生成。** 先做**同一组 6 条 reviewed 场景**的盲对照：

- 同一批场景分别交给 `base-qwen` 与 `guet-qwen`；
- 两组用**不同的试验 `batch_id`**，产物只用于比较，**全部保持 `pending`**。

**本批固定的 6 条**（2026-10-15 用户裁决后 `review_status` 已置为 `reviewed`）：

| # | scenario_id | bucket | 步数 | 末态 |
|---|---|---|---|---|
| 1 | `seed-no-tool-001` | `no_tool` | 0 | `no_evidence` |
| 2 | `seed-clarify-001` | `clarify` | 0 | `no_evidence` |
| 3 | `seed-single-tool-001` | `single_tool` | 1 | `success` |
| 4 | `seed-create-ppt-001` | `create_ppt` | 2 | `success` |
| 5 | `seed-stop-recover-001` | `stop_and_recover` | 2 | `success`（第 1 步 `tool_failed` 后恢复） |
| 6 | `seed-evidence-state-001` | `evidence_state` | 1 | `no_evidence` |

★ **本批规模是 6 条，不是 5 条。** 5 条装不下 §二 的两层含义
（`no_tool`/`clarify` 二选一、`stop_and_recover`/状态区分二选一），
硬压成 5 条必然缺一类。**不临时新增或伪造场景补覆盖。**

★ **删除「必须包含 `react_multi_step`」的要求。** 现有 `seed-react-001` 只是**作者写死的
静态两步轨迹**，observation 驱动的条件步骤**尚未实现和验证**，
因此**本批不验证真正的自主 ReAct**，`seed-react-001` **保持 `pending`**，不进本批。

**本批的覆盖边界（如实登记，不得声称通过）：**

| 状态 | 本批是否覆盖 |
|---|---|
| `success` | ✅（单工具、两步恢复、create_ppt 取证） |
| `no_evidence` | ✅（`evidence_state` 与两个无工具轮） |
| 中间 `tool_failed` 后恢复 | ✅（`stop_and_recover`） |
| `partial_evidence` | ❌ **未覆盖** |
| `irrelevant_result` | ❌ **未覆盖** |
| 最终 `tool_failed` | ❌ **未覆盖** |

★ 上表三行 ❌ 是**本批夹具的边界，不是模型结论**。不得因为「本批没暴露出问题」
就推断这三个状态区分能力合格。

人工逐条读，比六项：事实是否完全受证据约束 / kind 是否匹配 /
`no_evidence` 与 `partial` 与 `tool_failed` 是否分得准 / 是否漏关键条件与数值 /
是否模板化·编来源·可见推理 / JSON 合规率与 redteam 率。

★ **两个方向都不许预设**：`guet-qwen` 可能更熟现有风格，也可能把旧训练缺陷重新蒸馏进新数据；
`base-qwen` 可能更干净，也可能证据遵循与领域表达更弱。
★ 比较后**只选一个模型 + 一个 `prompt_version`** 进 8C；
**不得**把两种模型生成的答案混进同一个正式批次。
★ 若两者都不合格 —— **停在模型/prompt 选择层**，不开始全量生成。

### Task 8B.2：结构化候选协议与第二轮真机试产（2026-10-15）

**范围：** 只改候选生成请求协议与 prompt 行为边界；不改解析器、工具、RAG、
场景种子、模型或正式训练数据。

- 请求体新增 vLLM 0.9.1 原生 `response_format=json_schema`，严格限定为
  `{content: string, used_evidence_ids: string[]}`；请求与 manifest 共用同一份定义。
- 不加重试、不截取 Markdown、不修补坏 JSON；服务器拒绝 schema 时整体失败。
- `direct_answer` 禁止编造窗口名、排名、最受欢迎对象等校内事实；
  `clarification` 必须追问缺失槽位，不能用“查不到/去官网”替代；
  `grounded_answer` 不得补充证据外评价、建议或因果解释。
- 本地新增 5 条红灯，修复后 compiler 全套 `527 passed`。
- 真机 schema 探针：`base-qwen` / `guet-qwen` 均 HTTP 200，返回键与类型正确。

第二轮同场景结果：

| 模型 | candidates | redteam | 阶段门 |
|---|---:|---:|---|
| `base-qwen` | 6 | 0 | 机械格式门通过，仍待人工逐主张核证 |
| `guet-qwen` | 5 | 1 | **失败**：`create_ppt` 为 `missing_grounding` |

LoRA 另有两项事实/行为硬失败：把“红色预警”误写成进入退学试读期（证据规定是
“退学预警”），并在 `no_tool` 中臆设“麻辣烫窗口、汤面窗口”。因此不生成缺项的
A/B 盲审，不进入 8C，也不从 LoRA 批次挑剩余样本放行。

**下一阶段建议：** 不继续为旧 LoRA 调 prompt；先对 base 的 6 条做逐主张人工核证。
只有 base 的事实、条件、例外、来源与 kind 全部通过，才可裁决 8C 使用 base 作为
离线候选生成器。该选择不改变最终 Agent 仍通过新一轮 LoRA 训练得到的单模型部署路线。

### Task 8B.4：create_ppt 监督语义裁决（2026-10-15，**已裁决并落地**）

**裁决（用户）：** `create_ppt` 是 **tool-terminal** 场景，训练样本应以模型产生的
`create_ppt` function call 收尾，**不追加 observation，也不追加文本 assistant**。

**裁决文档：** `docs/superpowers/specs/2026-10-15-create-ppt-supervision-ruling.md`

**正确训练序列：**

```text
system → user → function_call(search_regulations) → observation → function_call(create_ppt)   ← 结束
```

★ **概念订正**：`create_ppt` 的 function call **不是**「模型看不到的上下文」——
它是**模型该生成的监督目标**（运行时第 2 轮的输出）。真正多余的只有它**之后**的
observation 与末轮 assistant。

**运行时侧对照：** `backend/tests/agent_platform/test_runtime_ppt_vs_full_sft.py`
—— 比的是「运行时第 2 轮**输入** == 训练样本去掉末尾调用后的**前缀**」与
「训练样本**末尾** == 运行时模型返回的 create_ppt 调用」，**不是**整串相等。

**落地清单（全部已完成）：**

| # | 文件 | 改法 |
|---|---|---|
| 1 | `训练集/compiler/models.py` | 新增 `TOOL_TERMINAL_BUCKETS = frozenset({"create_ppt"})`（**唯一真相源**） |
| 2 | `训练集/compiler/compile.py` :: `build_trajectory` | 新增 `is_tool_terminal_step`；终态那一步**只写 call、不写 observation** |
| 3 | `训练集/compiler/compile.py` :: `compile_full` | tool-terminal 桶**不追加**末轮 assistant |
| 4 | `训练集/compiler/compile.py` :: `_admit` | tool-terminal 桶过完第 2 档后**直接返回**（跳过 target 四档） |
| 5 | `训练集/compiler/replay.py` | 新增 `validate_tool_terminal_shape`（规则 A–D），由 `validate_trajectory` 调用 → 归入**现有** `evidence_mismatch` |
| 6 | `训练集/compiler/compile.py` | manifest 删掉 `has_final_assistant`，改记 `output_terminal_roles`（两项恒定出现，和 == rows）；`target_provenance` 只统计带 target 的行 |
| 7 | `scripts/generate_answer_candidates.py` | `_select` 剔除 tool-terminal；manifest 加 `skipped_tool_terminal` / `_ids` |
| 8 | `训练集/scenarios/README.md`、设计 §7.2/§7.3 | 订正「末轮必须是 assistant」为「末轮必须是模型产生的一轮」 |

**运行时一行不改** —— 「交子图后不回流」是有意设计。

**红灯 → 绿：** 新增 `训练集/compiler/tests/test_tool_terminal.py`（17 条）
与 CLI 侧 2 条；先跑出 **15 条真实行为红灯**，实现后全绿。

**测试基线（2026-10-15 实测）：**

```text
训练集/compiler/tests            527 → 546 passed，2 pre-existing DeprecationWarning
backend/tests/agent_platform     110 → 117 passed
```

**另立任务（**不在本轮改**）：** `agent_decide` 的 **text-wins** 规则 ——
流式轮里模型若既说话又喊工具，`tool_calls` 被显式清空、调用被丢，**任何工具都不会执行**。
属「静默 fallback」家族，**必须在生产验收前处理**。现状由
`test_a_text_plus_call_round_silently_drops_the_create_ppt_call` 钉住。

**Task 8B.3 的验收口径随之改变：**

- **5 条 answer-bearing 场景**生成文本候选并**人工逐主张审核**；
- **1 条 `create_ppt` 场景**走 **tool-terminal 结构验收**（不生成文本候选）；
- **不是**「生成六条文本候选并让六条都过」。

### Task 8B.3：生成质量修复（待裁决）

**进入条件：** Task 8B.4 的裁决明确。最低修复范围（用户给定）：

1. 最终正文**禁止**出现内部 `evidence_id`；
2. 来源选择必须**覆盖答案中的每条事实主张**；「检索上下文出现过」不算；
3. **同名课程**多个结果时必须消歧，不得静默选第一条；
4. 数值与条件词**逐字保语义**（「达到3门」≠「3门及以上」；「5门及以上」保留「及以上」）；
5. 按 8B.4 裁决为 `create_ppt` 加**独立 bucket 规则**，不套通用 `grounded_answer` 模板；
6. **不许改解析器**去修补模型坏输出；
7. 新 prompt 用**新 `prompt_version` + 新 `batch_id`**，不覆盖旧试产批次；
8. 先写行为红灯再做最小实现；**全套 compiler 测试无新增失败**。

重跑环境：`QDRANT_COLLECTION=school_docs_phase5` / `EMBED_DEVICE=cpu` /
`RERANK_DEVICE=cuda`，base 作离线候选生成器（LoRA 已判不合格，不重复对比）。
新批次生成后**再次逐主张审计，六条全过才提进 8C**。

---

## 原 Task 8 文本（保留，措辞已并入上面三段）

**Files:**
- Create: `scripts/generate_answer_candidates.py`（命令行入口，**唯一**调用 LLM 的地方）
- Create: `训练集/_work/phase6/answer_candidates/<batch>.candidates.jsonl`
- Create: `训练集/_work/phase6/manifests/<batch>.candidates.json`

**前置条件（缺一不可，缺则停）：**

1. 用户明确授权服务器使用；
2. vLLM 起来且模型可应答（探针先发一条，确认**不是**底座先验）；
3. 明确本次用哪个模型（`guet-qwen` 还是 `base-qwen`）与提示词版本号；
4. 明确 `batch_id`。

**步骤：**
- [ ] 探针：一条问答确认链路通；
- [ ] 小批（≤5 条）试产，人工逐条读；
- [ ] 全量生成；**全部落 `pending`**；
- [ ] 输出 manifest：模型、提示词版本、binding、条数、拒绝数；
- [ ] **停** —— 审核是人的事，不在本任务内。

**停止条件：** 生成结果里有任何**未经证据支撑的校内事实**（编号、金额、时限、机构名），
**立即停并报告**；不得「先把有问题的挑出来再放行剩下的」。

---

## TDD 验收清单（18 条 → 任务映射）

| # | 验收 | 归属 |
|---|---|---|
| 1 | `no_tool` → `direct_answer` 且 binding 为 `null` | Task 1 / 5 |
| 2 | `clarify` → `clarification` 且 binding 为 `null` | Task 1 / 5 |
| 3 | 工具型 target 缺 binding 时拒绝 | Task 1 / 5 |
| 4 | `pending` target 不进候选集 | Task 5 |
| 5 | `rejected` target 不进候选集 | Task 5 |
| 6 | 场景 `reviewed`、target 未 `reviewed` 仍不进候选 | Task 5 |
| 7 | observation 改变后旧 target 被隔离 | Task 5 |
| 8 | 调换多步 observation 顺序 → 哈希改变 | Task 2 |
| 9 | 工具名 / 参数 / status 改变 → 哈希改变 | Task 2 |
| 10 | 相同 observation 文本但不同工具/参数 → 不同 binding | Task 2 |
| 11 | `tool_failed` 与 `no_evidence` 的 kind 不得互换 | Task 1 |
| 12 | LLM 候选生成后仍为 `pending` | Task 3 |
| 13 | promote 只接受明确审核通过的候选 | Task 4 |
| 14 | 完整样本以 `assistant` 结尾 | Task 5 |
| 15 | 最终训练消息中不存在可见 `Thought` | Task 3（机械面）/ 5 |
| 16 | 连续编译两次输出与 manifest 字节一致 | Task 6 |
| 17 | 空 output/quarantine 仍记录存在性、行数、SHA-256 | Task 6 |
| 18 | 缺 target 时进 quarantine，而非继续输出半成品（`trajectory_prefix` 已不再是生产产物） | Task 5 |

---

## 停止条件（全局）

任一情况立即停：

- `compile.py` 执行路径出现网络调用；
- 非人工流程把 `assistant_target.review_status` 写成 `reviewed`；
- binding 不一致仍放行；
- 需要改设计或种子才能让测试变绿（**先报告，不许改判据**）；
- 需要动 Task 5.3 的范围（中间轮 assistant、自主 ReAct）；
- 需要服务器写入/上传/训练/部署而未获授权。

## 回滚方式

- 当前目录**不是 Git 仓库**，回滚靠逐文件替换 + 哈希核对；
- 建议开工前记录以下文件的 SHA-256 作为回滚锚点：
  `训练集/compiler/models.py`、`compile.py`、`训练集/scenarios/*.jsonl`；
- 新增文件（`binding.py` / `answer_candidates.py` / `promote.py` / 各 `test_*.py`）
  **整文件删除**即可回滚；
- `compile.py` 的改动分四块，逐块可撤（见 Task 5 回滚）；
- 过程中**不改** `训练集/scenarios/` 下的既有种子——真要改，先单独报告。
