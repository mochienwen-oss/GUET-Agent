# Agent 查询工程与数据工厂（阶段 6）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成 `query + keywords` 分路检索、七工具场景编译器和训练前质量门，输出可重放、可审计、可隔离的 Agent LoRA 训练候选集。

**Architecture:** 生产侧只有一个 LangGraph Agent 和一个逻辑决策模型（`base-qwen` + 动态加载的 `guet-agent` LoRA）。6A 先把制度检索参数拆成语义查询与 BM25 关键词并通过阶段 5A A/B；6B 再从场景规格调用当前 Registry 编译轨迹；6C 用独立评测资产和机械门拦住契约漂移、证据越界、重复轨迹与泄漏。

**Tech Stack:** Python 3.13、Pydantic 2、LangChain Core tools、LangGraph、Qdrant、BM25、sentence-transformers、pytest、LLaMA-Factory JSONL。

**Spec:** `docs/superpowers/specs/2026-10-14-agent-dataset-phase6-design.md`

## Global Constraints

- 决策模型只有一个逻辑实例：`base-qwen` + 一个动态 LoRA；阶段 6 不 merge、不训练、不部署。
- 新数据使用 `agent.runtime.registry.schemas()` 的七工具契约；`training_schemas()` 只复现 v7 旧四工具。
- observation 只能由 `registry.call()` + `registry.render()` 产生，不手写、不让 LLM生成。
- Easy Dataset 只生成自然问法候选，不决定工具、参数、证据、答案或轨迹。
- 不训练可见思维链，不把 LangGraph 内部消息对象写进训练集。
- 6A 首版只有 `query` 与 `keywords`；不实现未验收的 `filters`。
- GPU 检索诊断固定 `embedding=cpu`、`reranker=cuda`。
- 服务器默认只读；未经用户明确授权不得上传、训练、部署或改生产配置。
- 当前目录不是 Git 仓库；每个任务以文件 SHA-256、测试命令和 `docs/WORK_PROGRESS.md` 记录检查点，不伪造 commit。
- 已知基线失败 `backend/tests/test_citations.py::test_block_suffix_does_not_ask_for_citation_markers` 不属本阶段；不得新增失败。

## Review Focus

1. `keywords=[]` 或旧调用方不传字段时必须逐字保持旧检索行为；Task 2 以参数捕获测试锁定。
2. 多轮 `prev_query` 拼接只能影响语义查询，不能悄悄把上一轮整句当 BM25 关键词；Task 2 覆盖。
3. 模型把答案中的精确文档标题塞进关键词会形成不可学泄漏；Task 3 与 Task 7 同时拦截。
4. 相同工具参数在 ReAct 轨迹中重复调用会浪费预算并教坏停止策略；Task 6 与 Task 8 覆盖。
5. 机械证据检查可能把“同义改写”误报为越界；Task 7 必须把不确定项送隔离队列，不得自动删除或放行。

---

## 6A：查询工程

### Task 1: 冻结阶段 6 查询契约

**Files:**
- Modify: `backend/agent/tools/search_regulations/schemas.py`
- Modify: `backend/agent/tools/search_regulations/tool.py`
- Modify: `backend/agent/runtime/registry.py`
- Modify: `backend/tests/agent_platform/test_runtime_registry.py`
- Modify: `backend/agent/tools/search_regulations/tests/test_tool.py`
- Create: `backend/agent/tools/search_regulations/tests/test_query_contract.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `SearchRegulationsArgs(query: str, keywords: list[str] = [])`；`search_regulations_result(query: str, keywords: list[str] | None = None) -> ToolResult`。
- Preserves: `training_schemas()` 仍逐字等于 v7 四工具金标准；运行时 `schemas()` 的 `search_regulations` 参数更新为阶段 6 契约。`ToolSpec` 只增加可选的旧训练参数快照，不建立第二个 Registry。

- [x] **Step 1: 写失败测试**

  `test_query_contract.py` 断言：`query` 必填非空；`keywords` 可省略；关键词逐项 strip、去空、按首次出现去重；超过 12 项或单项超过 32 字拒绝；字符串冒充列表拒绝。

- [x] **Step 2: 运行测试确认失败**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/agent/tools/search_regulations/tests/test_query_contract.py -q`

- [x] **Step 3: 最小实现 Pydantic 与冻结 PARAMETERS**

  在 `schemas.py` 增加 `keywords: list[str]` 及校验；`PARAMETERS` 只新增可选数组字段，不加入 `filters`。

- [x] **Step 4: 更新工具公开签名与数据留痕**

  `tool.py` 把规范化关键词传给检索层，并在 `ToolResult.data` 中同时保存 `query` 与 `keywords`；不改变 observation renderer 的文本格式。

- [x] **Step 5: 锁定旧四工具与新七工具的分界**

  更新 Registry：`ToolSpec` 增加 `training_parameters: dict | None = None`，`training_schemas()` 对冻结工具优先使用该快照；仅 `search_regulations` 注册旧参数。测试断言旧 v7 金标准继续通过、运行时 schema 含 `keywords`，并明确新训练不得调用 `training_schemas()`。

- [x] **Step 6: 运行定向测试并记录哈希**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/agent/tools/search_regulations/tests/test_query_contract.py backend/agent/tools/search_regulations/tests/test_tool.py backend/tests/agent_platform/test_runtime_registry.py -q`

  Expected: 全绿；更新 `WORK_PROGRESS.md` 的 Task 1 文件哈希与结果。

### Task 2: 分离 dense、BM25 与 reranker 输入

**Files:**
- Create: `backend/agent/tools/search_regulations/scripts/query_plan.py`
- Modify: `backend/agent/tools/search_regulations/scripts/retrieval.py`
- Modify: `backend/agent/tools/search_regulations/scripts/trace.py`
- Create: `backend/agent/tools/search_regulations/tests/test_query_plan.py`
- Modify: `backend/agent/tools/search_regulations/tests/test_retrieval_trace.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: Task 1 的规范化 `query`、`keywords`。
- Produces: `QueryPlan(semantic_query: str, sparse_query: str, keywords: tuple[str, ...])`；`build_query_plan(query: str, keywords: list[str] | None, prev_query: str | None) -> QueryPlan`。
- Preserves: `retrieve_frozen(query, prev_query=None, trace=None, *, keywords=None)` 的旧位置参数语义。

- [x] **Step 1: 写 `build_query_plan` 失败测试**

  覆盖无关键词回退、关键词 join、多轮语义拼接、上一轮文本不进入显式 sparse 关键词、空关键词不产生空 BM25 查询。

- [x] **Step 2: 实现纯函数查询计划**

  `query_plan.py` 不导入 Qdrant/模型/config，只做字符串与不可变结构构造。

- [x] **Step 3: 写检索分路捕获测试**

  monkeypatch `_encode_query`、`bm25.query_vector`、`_rerank`，断言 dense/reranker 收到 `semantic_query`，BM25 收到 `sparse_query`；`keywords=None` 时三路仍收到旧 query。

- [x] **Step 4: 最小修改检索签名与内部调用**

  `_retrieve_chunks(query, limit, trace=None, *, sparse_query=None)`；`retrieve(..., *, keywords=None, prev_query=...)`；`retrieve_frozen` 把 `keywords` 设为 keyword-only，避免破坏旧调用方第二位置参数 `prev_query`。

- [x] **Step 5: Trace 加入可审计输入但不参与排序**

  `RetrievalTrace.to_dict()` 增加 `sparse_query` 与 `keywords`；只记录，不反向修改检索决策。

- [x] **Step 6: 跑检索单测**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/agent/tools/search_regulations/tests -q`

  Expected: 全绿且旧无关键词路径测试逐字不变。

### Task 3: 受控术语词表与查询质量门

**Files:**
- Create: `backend/agent/tools/search_regulations/data/query_expansions.json`
- Create: `backend/agent/tools/search_regulations/scripts/query_quality.py`
- Create: `backend/agent/tools/search_regulations/tests/test_query_quality.py`
- Create: `_work/phase6/6a-query-ab-manifest.json`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `audit_query_args(user_text: str, query: str, keywords: list[str], lexicon: dict) -> list[QueryIssue]`。
- `QueryIssue.code` 固定含 `blank_semantic_query`、`too_many_keywords`、`answer_title_leak`、`numeric_fact_expansion`、`unknown_expansion`。

- [x] **Step 1: 建立最小词表格式与失败测试**

  每条映射记录 `canonical/aliases/domain/reviewed`；首批只收录项目现有 route template 和阶段 5A 金标里可验证的术语，不批量生成。

- [x] **Step 2: 实现只读审计器**

  审计器只报告问题，不在运行时偷偷改模型参数；文档标题只有用户原话出现时才允许，数字/条款号不得由扩展新增。

- [x] **Step 3: 本地运行结构与无关键词回归测试**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/agent/tools/search_regulations/tests -q`

- [x] **Step 4: 在 GPU 上运行阶段 5A A/B**

  前置：用户明确授权服务器运行；固定 `embedding=cpu`、`reranker=cuda`。A 为 `keywords=[]`，B 为人工标注的受控 keywords；两侧使用同一语料、集合、模型和参数。

  Expected: B 不低于设计 §2.1 五项门槛；任一回退即停止 6A，不进入 6B。

  **实际（2026-10-14，本地 `--qdrant-local`，未连服务器）**：A 与 B 的 collection / 语料 sha256 /
  全部配置值 / 模型 / 设备逐字相同，唯一变量是 `keywords`。五项门 A、B **完全相等**：
  Candidate Recall@120 100%、Rerank Recall@5 100%、evidence Rerank@5 96.97%、
  gate false reject 0%、负例 false accept 0/14、diagnosis 47/47 success。
  关键词确实抵达 BM25（14 例中 10 例内部排名变化）。
  ★ 诚实边界：该 47 题上 A 已在天花板，B 无提升空间；7 例融合候选排名变差
  （A-015 6→24、B-009 1→34 最甚），被精排与最终选取吸收，未改变最终答案。

- [x] **Step 5: 固化 6A manifest**

  写入语料/集合/BM25/模型/schema 哈希、设备、指标、失败题和结论；不覆盖阶段 5A 原报告。

  **实际**：`_work/phase6/6a-query-ab-manifest.json` 由 pending 冻结为 `ab_passed`，
  含资产哈希、策略指纹、五项门逐条 A/B 对照、14 例逐题排名差异；阶段 5A 报告未动。

## 6B：场景驱动的数据编译

### Task 4: 定义 ScenarioSpec 与目录清单

**Files:**
- Create: `训练集/compiler/models.py`
- Create: `训练集/compiler/io.py`
- Create: `训练集/compiler/tests/test_models.py`
- Create: `训练集/scenarios/README.md`
- Create: `训练集/scenarios/*.jsonl`（仅最小种子，不批量生成）
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `ScenarioSpec`、`ToolStepSpec`、`ExpectedEvidenceState`；`load_scenarios(paths) -> list[ScenarioSpec]`。
- Required IDs: `scenario_id` 全局唯一；每条含 `bucket/provenance/review_status/max_steps`。

- [x] **Step 1: 为合法与非法场景写模型测试**
- [x] **Step 2: 实现最小 Pydantic 模型**
- [x] **Step 3: 为七能力桶各写 1 条人工种子**
- [x] **Step 4: 验证重复 ID、未知工具、空问句、非法状态全部失败**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_models.py -q`

  **实际**：**34 passed**。三条种子断言已对着真 Registry 复核：单专业 `compare_course`
  → `invalid_args`、补第二个专业 → `success`、`query_major_requirements(星际航行技术)`
  → `no_evidence`、`query_course(交通工程, 工程力学)` → `success`。

### Task 5: 实现 Registry 真实重放的**轨迹前缀**编译器

★ 它产出的是 `trajectory_prefix`：`system → user → (function_call → observation)*`，
**不是**完整训练候选 —— **没有末轮 assistant 回答**。manifest 固定写
`"artifact_kind": "trajectory_prefix"` 与 `"has_final_assistant": false`；
`no_tool` / `clarify` 只有 `system + user` 两轮，**不可直接用于 SFT**。
末轮 assistant 的成稿是独立的一项设计，不许在编译器里顺手接 vLLM。准入与结构硬门见 Task 5.1。

★ **该缺口已立项**：设计与计划见
`docs/superpowers/specs/2026-10-14-agent-answer-targets-design.md` 与
`docs/superpowers/plans/2026-10-14-agent-answer-targets-plan.md`。
**通过后，本任务的产物由 `trajectory_prefix` 升级为 `full_sft`**（见 Task 5.2）。

**Files:**
- Create: `训练集/compiler/replay.py`
- Create: `训练集/compiler/compile.py`
- Create: `训练集/compiler/manifest.py`
- Create: `训练集/compiler/tests/test_replay.py`
- Create: `训练集/compiler/tests/test_compile.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: `ScenarioSpec`、`registry.call()`、`registry.render()`、`registry.schemas()`。
- Produces: `compile_scenario(spec) -> CompiledSample`；`compile_dataset(inputs, output, manifest_path, quarantine_path) -> BuildSummary`。

- [x] **Step 1: 写 observation 必须来自 Registry 的失败测试**
- [x] **Step 2: 实现单工具与无工具轨迹**
- [x] **Step 3: 实现多步 `function_call → observation` 追加**
- [x] **Step 4: schema 快照逐行写入并记录 SHA-256**
- [x] **Step 5: 工具失败或期望状态不符时隔离，禁止混入输出**
- [x] **Step 6: 运行编译器定向测试**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests/test_replay.py 训练集/compiler/tests/test_compile.py -q`

  **实际**：18 passed（整套 compiler 测试 **54 passed**）。重放门用哨兵渲染器/替身调用
  反向证明 observation 既非写死也非缓存。★ 隔离判据补了**每步** `expect`（默认
  `success`）：只查末态会放过「先检索失败、再建任务成功」的 `create_ppt` 轨迹，
  而「出现 tool_failed 就隔离」又会误杀 `stop_and_recover` 桶。
  **未跑通**：`search_regulations` 在本机不可重放（本地 Qdrant `localhost:6333` 无服务），
  含规章检索的 3 条种子按判据被正确隔离 —— 见 WORK_PROGRESS 的待决项。

### Task 5.1: 编译器准入门与场景一致性修复

**Files:**
- Modify: `训练集/compiler/models.py`（`ScenarioSpec` 的 model-level 结构门）
- Modify: `训练集/compiler/compile.py`（review 分流、稳定 reason code、`artifact_kind`）
- Create: `训练集/compiler/tests/test_admission.py`
- Modify: `训练集/compiler/tests/{test_models,test_compile,test_replay}.py`
- Modify: `训练集/scenarios/stop_and_recover.jsonl` / `react_multi_step.jsonl` / `README.md`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: Task 5 编译器。
- Produces: `compile_dataset` 的 manifest 增加 `artifact_kind` / `has_final_assistant` 与三种
  `review_status` 计数；隔离行带稳定 `reason`（`review_pending` / `review_rejected` /
  `evidence_mismatch`）。

- [x] **Step 1: 先写失败测试** —— 覆盖 review 准入、`max_steps` 硬约束、桶语义最小硬门。

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest 训练集/compiler/tests -q`
  Expected: 新门全红。**实际：13 failed / 56 passed**（红全在 `test_admission.py`，
  含 stop_and_recover 旧种子那条；旧测试仍按老口径放行 `pending`）。

- [x] **Step 2: 最小实现结构门与准入分流** —— `pending` / `rejected` 一次工具都不调。
- [x] **Step 3: 修正 stop_and_recover 种子** —— 语义偷换（凭空引入土木工程、丢掉交通工程学）
  改为忠于用户原话的参数恢复；第二步只补回问句里已有的 `major`。保持 `pending`。
- [x] **Step 4: 如实标注 ReAct 边界** —— 只支持静态多工具轨迹前缀，observation 驱动的
  条件步骤未实现；`seed-react-001` 保持 `pending`。
- [x] **Step 5: 重跑** —— **70 passed**（Task 5.1 前为 54 passed + 13 红）。

### Task 6: 生成七工具和 ReAct 的可审计候选

**Files:**
- Create/Modify: `训练集/scenarios/*.jsonl`
- Create: `训练集/_work/phase6/candidates/agent_v8.candidate.jsonl`
- Create: `训练集/_work/phase6/manifests/agent_v8.build.json`
- Create: `训练集/_work/phase6/quarantine/agent_v8.quarantine.jsonl`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Consumes: Task 5 编译器。
- Produces: 未通过 6C 前不得改名为正式训练集的 candidate。

- [x] **Step 1: 先做能力配额清单，不先定总条数**

  每个工具至少覆盖正例、易混淆反例、缺参；ReAct 覆盖 observation 后补查、换工具、停止；PPT 覆盖自主选择、澄清、连续取证和失败恢复。

  **实际**：写成 `训练集/scenarios/README.md` 的「能力配额清单」一节 —— 七工具 ×
  正例/易混淆反例/缺参 的覆盖表，加 ReAct 三类、PPT 四类、桶7 四类、evidence_state 五类、
  no_tool 三类、clarify 一类。**先定覆盖，不先定总条数**；6C 的 `validate.py` 把这页变成机械门。

- [x] **Step 2: 导入 Easy Dataset 问法候选并保留 provenance**

  只取自然问法；工具与参数由场景作者或受控规则给出。候选中不可学的标题注入、答案泄漏进入隔离。

  **实际（2026-10-14）**：路径到手后已导入，经 **Task 6 Step 3A** 修掉一次错误分流。
  产物在 `_work/phase6/questions/`：

  | 文件 | 行数 |
  |---|---|
  | `regulations.single_candidates.jsonl`（只有单轮首轮） | **955** |
  | `regulations.multiturn_repair_queue.jsonl`（第 2/3 轮追问，只用于后续重构） | **1946** |
  | `regulations.review.jsonl`（含数字，待人工核） | 7 |
  | `regulations.quarantine.jsonl`（机械可证越界的单轮） | 0 |

  ★ 多轮那份里的 **2919 条 assistant 回答一条未取**（模型凭自己知识库作答，未经工具与
  observation 约束）。旧口径 `regulations.questions.jsonl` 把 1909 条追问混进了接受池，
  **已作废，原地保留并标注「不可用于训练」**。分流入库器 `_work/phase6/import_questions.py`
  （注意：它在 `_work/phase6/`，不在本计划早先写的 `训练集/_work/phase6/` 下）。

- [ ] **Step 3: 从旧 v7 选择材料时全部重新重放**

  不直接复制 tools/observation；无法用当前七工具契约重放的样本隔离。

- [ ] **Step 4: 编译 candidate + manifest + quarantine**

  ★ **前置：Task 5.2**。本步的正式产物是 `full_sft`（带末轮 assistant），
  不再是 `trajectory_prefix`；缺 target / 未审核 / binding 不匹配进 quarantine。
- [ ] **Step 5: 报告桶分布与隔离原因，不用条数冒充质量**

## 6C：质量门与独立评测

### Task 7: 结构、契约、重放与证据门

**Files:**
- Create: `训练集/compiler/validate.py`
- Create: `训练集/compiler/evidence_audit.py`
- Create: `训练集/compiler/tests/test_validate.py`
- Create: `训练集/compiler/tests/test_evidence_audit.py`
- Create: `训练集/_work/phase6/audits/quality_report.json`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `validate_dataset(path, schema_snapshot) -> QualityReport`；硬失败与需人工裁决分开计数。

- [ ] **Step 1: 写五种 evidence 状态不混义测试**
- [ ] **Step 2: 写 tools/schema/角色/列统一测试**
- [ ] **Step 3: 写 observation 重放逐字测试**
- [ ] **Step 4: 实现保守证据审计**

  可机械证明越界的记硬失败；同义改写等不确定项进入红队队列，禁止自动判绿或自动删除。

- [ ] **Step 5: 运行全候选质量扫描并输出行号/scene_id**

### Task 8: ReAct 轨迹与去重门

**Files:**
- Create: `训练集/compiler/trajectory_audit.py`
- Create: `训练集/compiler/dedup.py`
- Create: `训练集/compiler/tests/test_trajectory_audit.py`
- Create: `训练集/compiler/tests/test_dedup.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: `audit_trajectory(sample) -> list[Issue]`；`cluster_samples(samples) -> list[SampleCluster]`。

- [ ] **Step 1: 拒绝同名同参数重复调用和超预算轨迹**
- [ ] **Step 2: 验证每步 observation 对下一步工具/参数有可观察影响**
- [ ] **Step 3: 对问句、调用序列和答案模板分别聚类**
- [ ] **Step 4: 按来源簇切分，禁止同簇跨 train/eval**

### Task 9: 建立独立评测资产

**Files:**
- Create: `测试集/fc_gold.jsonl`
- Create: `测试集/react_gold.jsonl`
- Create: `测试集/grounding_gold.jsonl`
- Create: `测试集/ppt_gold.jsonl`
- Create: `测试集/course_gold.jsonl`
- Create: `训练集/compiler/tests/test_eval_isolation.py`
- Modify: `docs/WORK_PROGRESS.md`

**Interfaces:**
- Produces: 五类独立、可追溯金标；每条含 `case_id/source/reviewed_by/review_status`。

- [ ] **Step 1: 先写评测 schema 与“空文件不得报绿”测试**
- [ ] **Step 2: 从当前工具边界人工设计少量高信息量案例**
- [ ] **Step 3: `course_gold` 只以用户清洗后的 `_md` 为权威来源**
- [ ] **Step 4: 发现 md 歧义时输出文件和字段给用户裁决，不自行查 PDF**
- [ ] **Step 5: 运行逐字 + 归一化 + 语义近重复泄漏扫描**

### Task 10: 阶段 6 总门与交接

**Files:**
- Create: `训练集/_work/phase6/manifests/stage6-final.json`
- Create: `训练集/train_agent_v8.candidate.jsonl`（仅全部通过后）
- Modify: `docs/WORK_PROGRESS.md`
- Modify: `docs/superpowers/plans/2026-10-14-agent-dataset-phase6-plan.md`（勾选实际完成项）

**Interfaces:**
- Consumes: Tasks 1–9 全部报告。
- Produces: 进入下一阶段训练所需的唯一候选路径、SHA-256、schema 哈希、桶分布、隔离队列和评测资产哈希。

- [ ] **Step 1: 运行阶段 6 全量测试**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/agent/tools/search_regulations/tests backend/tests/agent_platform 训练集/compiler/tests -q`

- [ ] **Step 2: 运行项目全量测试与工具检查**

  Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests backend/agent/tools -q`

  Run: `D:\venv-guet-ppt\Scripts\python.exe scripts/check_tools.py`

  Expected: 不新增失败；已知 citations 失败单列。

- [ ] **Step 3: 运行 6C 全量质量门**

  硬失败必须为 0；红队队列必须全部有人类裁决或明确隔离；评测泄漏必须为 0。

- [ ] **Step 4: 生成正式 candidate 与 final manifest**

  只复制已通过样本，不在此步修改样本内容。

- [ ] **Step 5: 更新交接状态并停止**

  `WORK_PROGRESS.md` 必须写明完成项、未完成项、全部路径/哈希/测试结果、服务器状态、下一阶段唯一入口；阶段 6 完成后停止，等待用户授权上传和训练。

## 自检结论

- Spec 覆盖：单模型策略、6A 分路检索、6B 七工具编译、6C 九类质量门均有对应 Task。
- 类型一致：`keywords` 在 Pydantic、工具、检索、Trace、场景和审计中均为 `list[str]`；旧 `prev_query` 位置参数不变。
- 主要风险均在 Review Focus 找到具体测试归属。
- 计划没有把训练/上传/部署混入阶段 6，也没有为未验收的 filters 预留空实现。
- 当前无 Git，已用哈希 + manifest + `WORK_PROGRESS.md` 代替不可执行的 commit 步骤。
