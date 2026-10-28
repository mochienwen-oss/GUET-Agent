# 阶段 6 / Task 8C.2：离线 Teacher 资格考试包设计

**日期：** 2026-10-15  
**状态：** 用户已批准  
**范围：** 固定、冻结、可复现的离线 teacher 资格考试；只为后续批量生成 `assistant_target` 选模型。  
**不含：** 批量生成、promotion、正式 compile、LoRA 训练、部署、真正 observation-driven ReAct。

---

## 1. 背景与裁决

Task 8C.1 已证明「场景 → 真工具重放 → 答案候选 → 人工审核 → promotion →
`full_sft`」的管线能够闭环，但正式训练集只有 6 行，不具备训练价值。

两轮真机校准也已经给出负面证据：

- 旧 `guet-qwen` 在事实、无工具问题和 `create_ppt` grounding 上出现硬失败；
- `base-qwen` 仍会泄露内部 evidence id，并且不能稳定完成同名对象消歧；
- prompt 增加文字约束后，上述问题仍然出现。

因此，不允许把现有 7B base 或旧 LoRA 默认为批量 teacher。先冻结资格考试包，
后续任何候选 teacher 都必须用完全相同的输入、工具观测、prompt、schema、采样参数和审核表参加考试。

---

## 2. 这个考试实际评什么

资格考试评估的是：**候选 teacher 能否基于已审核的工具轨迹和证据目录，生成可人工审核的
`assistant_target` 候选**。

它不评估：

- teacher 自主选择工具的能力；
- 生产 Agent 的路由或 function calling 能力；
- 真正的 ReAct；
- 最终 LoRA 的上线质量。

原因是现有 `generate_answer_candidates.py` 接收的是**已经审核的 `ScenarioSpec.steps`**，它负责重放工具并让
teacher 写末轮答案，teacher 不决定工具和参数。不得用本考试的结果宣称某模型的 function calling
已经通过。

---

## 3. 考试包的规模与来源

### 3.1 规模

- 目标 **24–30 条**；
- 覆盖率是硬门，条数只是上下界；
- 某一覆盖格无法用真工具稳定复现时，必须标成「未覆盖」，不允许伪造 observation 补齐数量。

### 3.2 来源

1. 现有 6 条 reviewed 校准场景作为锚点（`seed-react-001` 除外）；
2. `_work/phase6/questions/regulations.single_candidates.jsonl` 中的 955 条自然问法候选；
3. `测试集/agent_acceptance.json` 中已有的 Agent 验收问法，仅取 `question` / `want_tool` /
   `want_args` / `id` 作为候选来源，不把 `must_include` 或旧答案当作 teacher 金标；
4. 已有 RAG / Agent 验收中出现过的其他硬失败问法，但必须能追溯到当前语料与真工具结果。

现有来源若没有某个新工具的自然问法（当前需特别检查 `list_courses`），
允许编制**最少量的人工覆盖探针**，但必须以 `source_kind=human_coverage_probe` 单独标记，
记录「为什么现有来源无法覆盖」并在场景人审门单独批准。不得伪装成 EasyDataset 或历史验收来源，
也不得为让工具成功而偷换问句中的实体。

`regulations.multiturn_repair_queue.jsonl` 本轮**不进考试包**。其中的第 2/3 轮问法依赖历史上下文，
而现有 `ScenarioSpec` 只有单个 `question`，直接塞入会把指代不全的追问伪装成单轮问题。多轮数据需要单独的
history-aware 场景契约，不在本轮顺手扩展。

EasyDataset 生成的 assistant 答案一律不使用；只能把其自然问法作为候选来源。

---

## 4. 覆盖矩阵

每条场景可覆盖多个格，但冻结前必须显式给出覆盖统计。

### 4.1 工具与路由边界

- 7 个工具各至少 1 条符合该工具契约的真实轨迹（状态不强制都是 `success`）；
- `no_tool` 和 `clarify` 各至少 1 条；
- 至少 3 条易混淆问题，例如单专业查课 vs 多专业对比、空教室 vs 教室规定、单课 vs 专业整体要求；
- `create_ppt` 只验证 tool-terminal 结构，不生成文本 target。

这一组覆盖只保证考试材料中有不同工具的 observation，不得宣称 teacher 自主路由正确。

### 4.2 证据状态

必须尝试覆盖：

- `success`；
- `no_evidence`；
- `partial_evidence`；
- `irrelevant_result`；
- 最终 `tool_failed`；
- 中间 `tool_failed` 后恢复成功。

只有当真工具重放稳定产出对应状态时才能记为已覆盖。不得手写 ToolResult 或 observation 来造状态。

#### 4.2.1 三个证据状态的口径（Task 4.3 补齐）

状态由**工具适配层**从检索已经产出的 `reason` 机械映射而来（`search_regulations/tool.py`
的 reason → `ToolStatus` 唯一映射表）。适配层只做映射：不复制检索阈值、不按 `rerank_score`
再判一次、不读 trace 反推，也不建立第二份「相关性真相源」。

- `success`：有内容通过当前相关性门（该工具只看候选块是否非空）。
- `no_evidence`：**没有可用候选**，或用户点名的资料未收录。
  对应 `reason ∈ {empty, absent:<文档>}`（以及候选并集为空时的 `no_dense_signal`）。
- `irrelevant_result`：检索**已经找到过候选**，但相关性门判定这些内容不能支持当前问题 ——
  返回内容与提问不是一回事。对应 `reason ∈ {rerank_low, score_low, grader_no}`。

三者的边界必须写进 rubric，模型不得越界扩写：

| 说法 | 是否成立 |
|---|---|
| `ToolStatus` 是 HTTP/进程状态 | ✗ 它是**证据充分度**状态 |
| `irrelevant_result` = 工具执行失败 | ✗ 工具正常执行完了 |
| `irrelevant_result` = 学校没有规定 | ✗ 只能说「返回的内容不能作为回答依据」 |
| `no_evidence` = 学校没有规定 | ✗ 只能说「本次检索没有给出可用依据」 |

即：`no_evidence` 与 `irrelevant_result` **都不得被扩写成全局否定结论**；
二者的区别是「有没有出现过候选」，不是「学校有没有规定」。

`create_ppt` / `query_empty_classroom` 等**指针型**工具不适用本表（它们恒 `success`）。

#### 4.2.2 映射是穷举契约（Task 4.3.1 收紧）

上面的 reason 表**不是白名单式兜底，是穷举契约**：空结果只允许落在表内。

- 表外的空结果 `reason`（未知字符串 / `None` / 空串）意味着**检索加了新的空结果出口而映射没跟上**
  —— 这是**契约漂移**，不是可以"从宽处理"的运行态。
- 此时 `search_regulations_result` **必须抛 `ValueError`**，且错误消息含：
  未知 reason 的 repr、已知 `no_evidence` reasons、已知 `irrelevant_result` reasons、
  以及「不得静默归为 `no_evidence`」这条边界。
- **不得静默降级为 `no_evidence`** —— 那会把「有内容但不相关」说成「库里没有」，
  正是 §4.2.1 要区分的两件事；也不得在工具内自行构造 `tool_failed`（那是 Registry 的职责）。
- `registry.call` 的既有 `except Exception` 边界把这个异常统一转成
  `ok=False` / `status=tool_failed` / `error.code=tool_failed`，并渲染统一的故障措辞
  （「**不代表学校没有相关规定**」）—— 与工具执行失败走同一条路，不新增第二套失败语义。
- **约束**：检索新增任何空结果 reason 时，必须**同步扩展映射表与测试**；否则上游会在第一次
  真实命中时抛错。这是有意的"吵醒"设计，优于悄悄给出错误状态。

因此 `tool_failed` 也只表示「本次调用没成功」，同样**不代表学校没有规定**。

### 4.3 高风险语义

至少覆盖：

- 数字、不等号和条件词（「达到」「以上」「不低于」「不超过」）；
- 同名课程或同名对象的消歧；
- 一个答案中有多条独立事实主张；
- 多来源证据覆盖；
- 不存在的专业/课程/规定；
- 无工具主观问题，不得编造校内窗口、排名或口碑；
- 缺失关键槽位时只追问必要信息；
- 模型自写来源、内部 evidence id、可见推理标记。

---

## 5. 产物与不可变性

资格考试包放在：

```text
_work/phase6/teacher_qualification/
  qualification.scenarios.jsonl
  qualification.rubric.jsonl
  qualification.manifest.json
  selection-report.md
```

### 5.1 `qualification.scenarios.jsonl`

- 只存已人工审核的 `ScenarioSpec`；
- `review_status` 必须是 `reviewed`；
- 工具、参数、步骤顺序、`expect`、`expected_evidence.state` 必须经真工具重放验证；
- 不携带 `assistant_target`；
- 不并入 `训练集/scenarios/`，避免在 teacher 尚未通过时污染正式场景集。

### 5.2 `qualification.rubric.jsonl`

每个 `scenario_id` 恰好一行，只记审核依据，不记可直接抄写的完整金标答案：

```json
{
  "scenario_id": "teacher-qual-001",
  "coverage_tags": ["tool:search_regulations", "state:success", "risk:numeric_boundary", "risk:multi_claim"],
  "must_preserve": ["达到 3 门", "5 门及以上"],
  "must_not_claim": ["3 门及以上"],
  "required_disambiguation": [],
  "review_points": ["每一档阈值必须有证据", "不得出现内部 evidence id"]
}
```

`rubric` 只辅助人工审核和风险排序，**不是自动语义放行器**。机械可判的项（JSON 合法、evidence id
泄露、可见推理、未知证据 id）继续由现有 redteam 门拦截；事实覆盖和语义保真由人判。

`coverage_tags` 中的 `tool:*` / `bucket:*` / `state:*` 必须由 `ScenarioSpec` 机械推导并交叉校验，
不接受人工凭空声明覆盖；只有 `risk:*` 由人工标注。无工具步的 `no_tool` / `clarify`
只计 bucket 覆盖，不把它们用于记账的 `no_evidence` 当成真工具证据态。

### 5.3 `qualification.manifest.json`

必须记录：

- phase / artifact kind / pack version；
- 场景数和覆盖统计；
- 两个 JSONL 的行数与 SHA-256；
- 候选问法来源文件的逻辑名和 SHA-256；
- 当前工具 schema SHA-256；
- 语料/索引身份（`school_docs_phase5`、789 points、corpus fingerprint）；
- 冻结时使用的 prompt version 和 sampling，但不写完整 prompt。

不记时间戳、绝对路径、主机名、用户名或凭据。同一份输入必须能逐字节复现 manifest。

### 5.4 `selection-report.md`

对每个入选/未入选候选记录原因，至少包括：来源行、去重关系、覆盖格、工具重放结果、人工审核状态。

---

## 6. 冻结流程

1. **机械预选**：从 955 条单轮问法中去精确重复，按意图/实体/数字/风险标签分层；
2. **场景编制**：人或辅助程序提出 `ScenarioSpec`，但不得写 assistant 答案；
3. **真工具重放**：用当前 Registry 重放每一步，校验工具名、参数、状态、末态与证据目录；
4. **场景人审**：人确认问题自然、路由合理、参数忠于问句、证据能支持后续回答；
5. **覆盖门**：覆盖矩阵不足则停止，不为凑 24 条放宽质量；
6. **冻结**：按 `scenario_id` 稳定排序，写出两个 JSONL、manifest 和选择报告；
7. **不可变验收**：连续构建两次必须四份产物逐字节一致。

冻结包一旦通过，接入 teacher 时不得再根据模型的强弱改题、换证据或改 rubric。需要修题时必须升 pack version，
保留旧 manifest。

---

## 7. Teacher 接入与盲审

### 7.1 统一输入

每个候选 teacher 必须使用：

- 同一份冻结 scenarios；
- 同一份冻结工具 schema、语料和索引身份；
- 同一 prompt version；
- 同一 JSON schema；
- 同一 sampling（候选生成保持 `temperature=0`）；
- 同一 max tokens；
- 不同 batch id，不覆盖旧批次。

候选模型的名称、provider、endpoint 和凭据不进盲审材料，只进候选批次 manifest。

每个 teacher 批次都必须通过现有 replay 入口**独立真重放**，不得手写、缓存或回灌 observation。
各批对应场景的 evidence binding 必须一致；若因索引或工具漂移而不一致，本轮比较整体停止，
不把不同证据条件下的答案做 A/B。

### 7.2 盲审材料

盲审包只展示：

- 场景问题；
- 工具 observation / 证据目录；
- 匿名候选答案 A/B/…；
- rubric 的审核点。

人工裁决使用：「A 合格 / B 合格 / 多个合格 / 全部不合格」，不强迫每题二选一。模型身份在审核完成后解盲。

### 7.3 不可自动放行的项

以下项只能机械提醒，不能作为语义合格的自动证明：

- 词面上出现了来源标题；
- 答案中有某个关键词或数字；
- embedding / NLI / LLM grader 认为「相似」或「支持」；
- 候选引用了一个合法 evidence id。

这些信号可用于风险排序，但最终事实与证据的对应关系仍由人审。

---

## 8. 资格门

### 8.1 六类硬失败（任一条即整个 teacher 不合格）

1. 编造证据外事实、来源、校内窗口、排名或规定；
2. 泄露内部 evidence id 或可见推理；
3. 改变数值、比较符、条件或适用范围的含义；
4. 存在多个同名对象时静默选中其中一个；
5. 把 `tool_failed` / `no_evidence` / `partial_evidence` / `irrelevant_result` 互相说错，尤其把工具故障说成「学校没有规定」；
6. 输出不符合 JSON schema，或引用未知 evidence id。

硬失败不允许用平均分抵消。

### 8.2 通过条件

候选 teacher 必须同时满足：

- 硬失败 = 0；
- 机械 redteam = 0；
- 每一条事实主张都能在它选中的证据中找到直接支持；
- 全部必须条件、例外、否定和数值边界保留；
- `no_tool` / `clarify` / 各证据状态的回答语义正确；
- 非硬失败的语言质量指标合格率 ≥ 90%。

「合格」只表示可用于**生成 pending 候选**，不表示生成内容可跳过人工审核或直接进入 `targets.jsonl`。

---

## 9. 后续批量运行的权限边界

只有资格考试通过后，才能开始小规模正式批次，且：

- 每批只允许一个 teacher + 一个 prompt version；
- 产物全部保持 `pending`；
- 仍需人工审核后才能 promote；
- 先做 50–100 条小批，根据驳回原因决定是否扩大；
- 不得将不同 teacher 的候选混成同一批次；
- 不得因为 teacher 通过资格考试就关闭 redteam 门或事实人审。

---

## 10. 验收条件

资格考试包本身必须满足：

1. 24–30 条，或因真工具无法稳定复现某状态而显式少于 24 条；
2. 覆盖矩阵逐格可追溯，未覆盖项单独列出；
3. 所有场景已人审，真工具重放结果与 `expect` / 末态一致；
4. 不含 EasyDataset assistant 答案；
5. 不含多轮指代不全问句；
6. 不含伪造 observation；
7. scenarios 与 rubric 的 `scenario_id` 一一对应，无重复、无孤儿；
8. manifest 的行数、SHA、schema、语料与索引身份与实际一致；
9. 连续构建两次，四份产物逐字节一致；
10. 不修改正式五资产、七个正式场景、Agent/RAG/工具 schema/索引参数。

---

## 11. 已知边界与后续独立任务

- **真正 ReAct：** 现有 `ScenarioSpec` 是静态 steps，不支持 observation 驱动的条件下一步；
- **多轮：** 现有 question-only 契约不能保真重放 history-aware 追问；
- **function calling 模型验收：** 本考试不让 teacher 选工具，因此不能替代 Agent 路由/调用验收；
- **`agent_decide` text-wins：** 已知静默丢 tool call 问题必须在生产验收前单独修复；
- **条文级来源与 `.md` 后缀：** 展示债务不在本轮顺手修理。

这些未完成项必须在后续阶段各自立项，不得因为 teacher 资格考试通过就声称它们已解决。
