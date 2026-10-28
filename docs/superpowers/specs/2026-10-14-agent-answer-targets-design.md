# 阶段 6 / Task 5.2：答案目标与完整 SFT 编译设计

**日期：** 2026-10-14
**状态：** 待用户审核（本轮只写设计，不含实现）
**方案：** B —— 「答案候选生成」与「确定性训练集编译」彻底分离
**范围：** 完整 assistant target、证据绑定、候选/审核分离、确定性 full SFT 编译。
**不含：** 真正的 observation-driven ReAct、批量调用线上模型、上传/训练/部署。

---

## 1. 目标与结论

设计时编译器产出的是 **`trajectory_prefix`**（`system → user → (function_call → observation)*`），
**没有末轮 assistant**，因此**不是**可用 SFT 样本。Task 5.1 / 5.1.1 已经把准入与计数修对，
但「谁写答案」这个缺口一直开着。

★ **现状（Task 5 完成）**：出口**只有 `full_sft`**，末轮 assistant 逐字来自已复核的
`assistant_target.content`；`trajectory_prefix` **已不是生产产物**（见 §7.3）。

**结论：让 LLM 在线进入 `compile.py` 是错的。** 那会让同一批场景每次编译得到不同字节，
`manifest` 的哈希失去意义，审核也无从落地。所以采用 B 方案：

```text
ScenarioSpec
  → 真实工具重放
  → 生成 answer candidate        ← 独立的、可重跑的入口，会调 LLM
  → 记录 模型 / 提示词版本 / 证据绑定哈希
  → 人工或独立规则审核            ← 只有人能写 reviewed
  → 把审核通过的 assistant_target 冻结进 ScenarioSpec
  → 确定性编译完整 SFT 样本        ← compile.py，不调 LLM，字节可复现
```

**明确禁止：** `ScenarioSpec → compile.py 在线调用 LLM → 直接写训练集`。

判别标准很简单：**`compile.py` 的执行路径里不得出现任何网络调用**。

---

## 2. 两种审核状态（互不等价）

| 状态 | 挂在 | 表示什么 | 谁能写 `reviewed` |
|---|---|---|---|
| 场景级 `review_status` | `ScenarioSpec`（**已有**，Task 5.1 起生效） | 工具选择、参数、步骤、期望状态、轨迹**已审核** | 人 |
| 目标级 `assistant_target.review_status` | `AssistantTarget`（**新增**） | 最终 assistant 内容**已审核** | 人 |

**只有两者均为 `reviewed`，才允许进入完整 SFT 候选集。**

★ **生成程序无权把 `assistant_target.review_status` 写成 `reviewed`。** LLM 生成的答案
一律只能是 `pending`。这条由 Task 3 的红灯测试钉住。

---

## 3. `AssistantTarget` 契约

放在 `训练集/compiler/models.py`，与 `ScenarioSpec` 同文件同风格
（Pydantic 2、`ConfigDict(extra="forbid")`、中文 docstring、★ 标注纪律）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 最终 assistant 文本。**非空**（复用 `reject_blank` 的做法） |
| `kind` | `TargetKind` | 见 §4。由 `(bucket, ScenarioSpec.expected_evidence.state)` **推导**，不是自由填写 |
| `review_status` | `ReviewStatus` | **复用** `models.py` 已有的 `ReviewStatus`，不手抄第二份 |
| `evidence_binding_sha256` | `str \| None` | 见 §5。工具型必须非空；`no_tool` / `clarify` 必须为 `None` |
| `source` | `Literal["human", "llm_candidate", "deterministic"]` | 内容从哪来 |
| `generator` | `str` | 生成器标识 + 版本，如 `answer-candidate/1` |
| `model` | `str \| None` | `source=llm_candidate` 时必填；人写/确定性可为 `None` |
| `prompt_version` | `str \| None` | `source=llm_candidate` 时必填 |

★ **不做过度抽象**：暂不设计 `generation` 参数子对象、不做多候选打分、不做 A/B 字段。
真需要时另立字段。

**模型级校验 —— ★ 只校验字段自身（Task 1.1）**

1. `content` / `generator` 非空；
2. `source == "llm_candidate"` → `model` 与 `prompt_version` 必须非空；
3. `source != "llm_candidate"` → 二者必须为 `None`（避免「人写的答案却挂着模型名」）；
4. `review_status` 合法（由 Literal 保证）；
5. `extra` 字段拒绝；
6. `evidence_binding_sha256` 若非 `None`，必须**严格匹配 `^[0-9a-f]{64}$`**。

★★ **模型层不判断「这个 kind 该不该有绑定」，也不判断「这个场景有没有工具轮」。**
理由见 §5.3：那两条一旦在这里拒掉，`target_kind_mismatch` 与
`evidence_binding_mismatch` 会变成**永远不可达**的 reason ——
审计信息在装载阶段就丢了，而 quarantine 统计会显示「从来没发生过这类问题」。

**推论：能装载 ≠ 能进训练集。** 结构合法但语义不匹配的 target **必须能装载**，
以便由 compiler admission 产出一个**稳定的 reason**。

---

## 4. `TargetKind` 与场景类型的合法映射

```python
TargetKind = Literal[
    "direct_answer", "clarification", "grounded_answer", "partial_answer",
    "no_evidence_answer", "irrelevant_result_answer", "tool_failed_answer",
]
```

### 4.1 推导规则（**唯一权威**）

```python
def target_kind(bucket: str, expected_state: ToolStatus) -> TargetKind:
    if bucket == "no_tool":  return "direct_answer"
    if bucket == "clarify":  return "clarification"
    return {
        "success": "grounded_answer",
        "partial_evidence": "partial_answer",
        "no_evidence": "no_evidence_answer",
        "irrelevant_result": "irrelevant_result_answer",
        "tool_failed": "tool_failed_answer",
    }[expected_state]
```

★ **`expected_state` 取 `ScenarioSpec.expected_evidence.state`（场景**声明**的期望），
不取「运行末态的另一种真相」。** 理由与纪律：

1. 现有门**已经**要求「每步实际状态 == 该步 `expect`」且「末态 == `expected_evidence.state`」
   （`compile.py:196-210`）。**两者相等是推导的前提**，不是重复校验。
2. 若再引入一个「运行末态真相源」，同一件事就有了两处事实 —— 二者一旦漂移，
   `kind` 会按其中一个写、`quarantine` 按另一个判，**分不出谁对**。
3. 所以顺序是：**先把状态校验全部跑通，再按声明推导 kind**。校验未过 → 该场景根本走不到
   推导这一步（进 `evidence_mismatch`）。

### 4.2 `no_tool` / `clarify` 按 `bucket` 优先

★ `compile.py:137` 在无步骤时把 `observed_state` 写成 `NO_TOOL_STATE = "no_evidence"`。
**那是记账用的占位值，不是回答语义** —— 它回答的是「这条轨迹没有工具轮，所以没有条文证据」，
**不是**「查了但库里没有」。

于是 `no_tool` 场景会同时命中「→ `direct_answer`」与「状态是 `no_evidence` → `no_evidence_answer`」，
**必须定优先级：`bucket` 优先，且只对 `no_tool` / `clarify` 两个桶生效**（§4.1 的语句顺序即优先级）。
其余五个桶一律按声明状态推。

**理由**：`no_evidence_answer` 要教的是「**查了**，库里确实没有，照实说没查到」；
`no_tool` 要教的是「这个问题**本来就不该调工具**」。
把两者混为一谈，正好会教出「闲聊也答『学校没有规定』」这种最坏的兜底。

### 4.3 `stop_and_recover`：kind 看**声明的聚合状态**，不看「有没有哪一步失败过」

`seed-stop-recover-001` 第一步是被**声明**过的 `tool_failed`（`expect: tool_failed`），
而 `expected_evidence.state` 是 `success`。

按 §4.1，kind = `grounded_answer` —— **正确**：最终答复确实有依据。
轨迹里那一步失败是**被声明、被审核过的**，不是异常。

**规则：kind 只看 `expected_evidence.state`，不看「有没有哪一步失败过」。**

★ 混合多步轨迹里，「整条轨迹最终算有依据还是没依据」**由 `expected_evidence.state` 显式声明**，
不由编译期临时聚合。**更复杂的运行时聚合决策留给 Task 5.3**，本阶段不做。

### 4.4 状态不一致时的处置

`assistant_target.kind` 与 §4.1 推导出的 kind 不符 → **隔离**，理由 `target_kind_mismatch`。
**不得自动改写 `kind`，也不得按 kind 反推理由。** 不一致说明「答案是在另一种证据状态下写的」，
换句话就是「答案与证据对不上」—— 正是要拦的东西。

---

## 5. 证据绑定（canonical evidence binding）

### 5.1 为什么不能只哈希最后一条 observation

多步轨迹里，最终答复的依据是**整条有序执行轨迹**：换了工具、换了参数、或某一步状态不同，
即使 observation 文本碰巧一样，也**不是同一条训练依据**。

### 5.2 精确定义

对**整条有序执行轨迹**构造数组：

```python
rows = [
    {
        "step_index": i,              # 0-based，整数
        "tool": step.tool,            # 当前 registry 的工具名
        "arguments": step.arguments,  # 实际传入的参数（规范化后的）
        "status": seen["status"],     # ToolStatus
        "observation": seen["observation"],   # registry.render() 的真实输出
    }
    for i, (step, seen) in enumerate(zip(spec.steps, probed))
]
```

规范化：

```python
blob = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
sha256(blob.encode("utf-8")).hexdigest()
```

**必须逐条满足：**

| 要求 | 理由 |
|---|---|
| UTF-8 编码 | 中文 observation，编码不同则哈希不同 |
| `ensure_ascii=False` | 与项目其余哈希口径一致，且保留原字符 |
| `sort_keys=True` | 字典键序不影响哈希（`arguments` 是字典） |
| `separators=(",", ":")` | 去掉空格，避免缩进风格渗进哈希 |
| **保留数组顺序** | 调换两步的顺序必须换哈希 |
| 任一项变化即换哈希 | 工具名 / 参数 / 状态 / observation 任一不同 → 不同哈希 |

### 5.3 特殊规则

- `no_tool` / `clarify`：`evidence_binding_sha256` **必须为 `None`**（没有工具轮，没有依据可绑）；
- 所有**工具型** target（即 `steps` 非空的场景）**必须**提供 `evidence_binding_sha256`；
- ★ **binding 的有无由 `steps` 决定，不由 `kind` 决定，更不由 target 自己声明。**
  这正是 `evidence_state` 桶里 `no_evidence` 状态的场景：它有 `steps`，**是工具型**，
  binding **必须非空** —— 尽管它的 kind 叫 `no_evidence_answer`；
- ★ **归属明确（Task 1.1 修订）**：`steps` ↔ `kind` ↔ `binding` 的联合语义**不在模型层判**，
  一律由 **compiler admission（Task 5）** 按 §7.4 的固定优先级归类并写入 quarantine。
  **不许**只凭 `target.kind` 反推「有没有工具」——
  那样一旦 kind 写错，binding 的判据会跟着一起错，两个错互相掩护。

  ★ **为什么必须放到 admission**：若在 `ScenarioSpec` / `AssistantTarget` 里提前拒掉，
  `target_kind_mismatch` 与 `evidence_binding_mismatch` 就成了**不可达**的 reason，
  这类问题在统计上会显示为「从未发生」。**模型层放行 + admission 归类**，
  才能让每一个 reason 都真的可观测。
- **编译时必须重放工具并重新计算 binding**，与 target 里存的那份比对；
- 不一致 → 隔离，理由固定 `evidence_binding_mismatch`；
- ★ **不得**偷偷沿用旧答案、**不得**自动重新生成答案。

### 5.4 必然的代价（先说清楚）

语料、工具实现、渲染器任一改动 → observation 变 → **binding 全量失效 → 全部 target 被隔离**。
这是**设计意图**，不是故障：那批答案确实是按旧证据写的。
处理办法只有一个：重跑候选生成 + 重审。**不要**为了绕过它放宽绑定。

---

## 6. 答案候选队列与 promote

### 6.1 候选产物（不直接覆盖 `ScenarioSpec`）

`训练集/_work/phase6/answer_candidates/<batch_id>.candidates.jsonl`，一行一条：

```json
{
  "scenario_id": "...",
  "bucket": "...",
  "evidence_binding_sha256": "...",
  "proposed_target": { "content": "...", "kind": "grounded_answer",
                       "source": "llm_candidate", "generator": "answer-candidate/1",
                       "model": "...", "prompt_version": "..." },
  "candidate_status": "pending",
  "batch_id": "answer-candidates-a"
}
```

★ **`batch_id` 由调用方显式传入**，生成器**不得**按当前时间自动生成。
理由：产物要求**连跑两次字节一致**（Task 5.1.1 已建立该口径）；时间戳会让候选文件不可复现。

**状态迁移：唯一合法路径**

| 步骤 | 谁做 | 能写什么 |
|---|---|---|
| 生成 | 生成器 | `candidate_status = "pending"`、`proposed_target.review_status = "pending"` —— **仅此二值** |
| 裁决 | **人** | 只改 `candidate_status` → `"approved"` / `"rejected"` |
| 晋升 | `promote.py` | 见 `candidate_status == "approved"` 才**确定性地构造**出 `review_status = "reviewed"` 的冻结 target |
| 搬运 | `promote.py` | **不得**修改 `content` / `kind` / `evidence_binding_sha256` |
| 留痕 | `promote.py` | **每次晋升写审计台账** |

★ 生成器**没有**任何写 `reviewed` 的路径；人可以改 `candidate_status`，但**不能**直接写
target 的 `review_status` —— `reviewed` 只由 `promote` 依据 `approved` **推导**出来。
这样「谁审的」与「审成什么」落在两处互不覆盖的记录上。

### 6.2 promote / apply

独立、可审计的一步（`训练集/compiler/promote.py`），把**明确审核通过**的候选冻结进
`训练集/scenarios/targets.jsonl`：

1. 只接受 `candidate_status == "approved"` 的行；
2. 逐条**重新重放并重算** `evidence_binding_sha256`（**不信文件里那份**）；
3. 构造出 `review_status = "reviewed"` 的 `AssistantTarget`；
4. 写入 `训练集/scenarios/targets.jsonl`（**独立机器拥有文件**）；
5. 写审计台账（晋升了哪些、拒了哪些、各自理由码、批次）。

★ **promote 不生成内容、不改内容、不改 kind、不改 binding** —— 它只搬运。

**精确接口（Task 4 定稿）：**

```python
promote(candidates_path, scenario_paths, targets_path, ledger_path,
        *, tools=None, replay=None) -> PromoteSummary
```

★ **场景来源必须显式传**（`scenario_paths`）：不猜目录、不写死七个种子路径。
`tools` 传给 `load_scenarios`（测试可传假集合，避免重量级 Registry）；
`replay` 为 `None` 时才用真实 `replay_step`。

**处理顺序（硬性）：预检全部只读内存、不碰磁盘；全部过了才写盘。**

```text
1  读入并解析全部 ScenarioSpec
2  读入并解析全部 CandidateRow
3  候选内 scenario_id 唯一            → 重复即整体失败
4  一次只晋升一批（候选文件内 batch_id 唯一）
5  读已有 targets（不存在=空集合；非法 JSON / 重复 ID 含行号 → 整体失败）
6  读已有台账（不存在/空=空集合；非法 JSON / 缺字段 /
              (batch_id, scenario_id) 重复 → 整体失败）
A  已有 targets 的场景完整性：孤儿 target → 整体失败（**入口就判**）
B  `batch_id` 不可变：该批次已入过账 → 整体失败（**重放与写盘之前**）
C  两文件互相一致（双向核对，见下） → 不一致 → 审计错，停下
7  逐条按 candidate_status 处理（见 §6.9）
8  可晋升行：重放 → 轨迹校验 → 重算 binding
9  在内存里构造完整的 targets 与**累计** ledger
10 全部检查过后，才建目录、写临时文件
11 fsync/close 之后 os.replace 原子替换        （不许边处理边 append）
```

★ 第 4 / 7 步里的逐行判定（`scenario_not_found` / `scenario_not_reviewed` /
`target_already_exists` / …）产的是**逐行原因码**（写进台账，运行继续），不是整体失败；
**A / B / C 三组是整体失败**。

**★ A / B / C 三组（Task 4.1 补的三道口子）**

| 组 | 判据 | 为什么必须整体失败 |
|---|---|---|
| **A** 已有 targets 的完整性 | 每一行都必须在场景集合里找得到主人 | 只在 `load_scenarios(targets=…)` 判不够 —— 孤儿会被原样搬进新文件 |
| **B** `batch_id` 不可变 | 该批次已在台账里 → 拒 | 否则第二批会把第一批的 **promoted 历史覆写成 skipped**，审计链当场断 |
| **C** 两文件一致性 | 双向核对（见下） | 两文件**不是事务**；不一致说明上次写盘崩在中间，继续跑只会让错误扩散 |

**C 的双向核对：**

1. 每个已有 target 必须**唯一**对得上一条 `decision="promoted"` 的台账行，且
   `promotion_batch_id` 相同、`promoted_target_sha256` 等于该 target 的**规范哈希**；
2. 每条 `promoted` 台账行必须对得上一个 target。

★ 不一致时抛**独立类型** `AuditInconsistency`（`ValueError` 子类），
**不自动补写、不静默按新批次继续、不改任何已有文件**。
**本轮不实现自动恢复** —— 先保证「能可靠发现」，不让错误状态继续扩散。

**approved 一致性门与判定顺序（设计 §7 的 1–9 条）：**

```text
bucket → kind → 候选顶层 binding vs target 的 binding → target 是否仍为 pending
      → 重放 → validate_trajectory → 重算 binding
```

★ 前四条是**静态**检查，先判（便宜）；重放代价最高，放最后。

★ **`target_not_pending`** 是 Task 4 在「建议原因码」之外**新增**的一条：条件 5
（`proposed_target.review_status == pending`）为假时若不单独归因，就只能挤进
`bucket_mismatch` 之类的错码里 —— 而它其实意味着「候选文件被人直接写成了 `reviewed`」，
是**越权**，不是配置错。宁可多一个码，不可混一个码。

**★ `replay_mismatch` 与 `evidence_binding_mismatch` 必须分开**（统计意义不同）：

| 码 | 触发 |
|---|---|
| `replay_mismatch` | 工具名 / 步状态 / 末态不符合 `ScenarioSpec`（`validate_trajectory` 报错） |
| `evidence_binding_mismatch` | 轨迹**合法**，但 observation / 参数变了 → 重算 binding 与候选不一致 |

★ **promote 不得把「来源真实」误报为「内容有据」。** `candidate_status=approved`
是**人工裁决**；promote 只验证身份、轨迹、状态、binding 与不可变字段。
合法的 `evidence_id` **不**自动证明答案里每个事实受支持（§6.6）。
当前阶段依赖人工审核；主张—证据检查是**后续 evidence QA** 的独立任务。
**不许**新增关键词匹配器冒充语义验证。

**写盘与失败恢复：** `targets` 与 `ledger` 先各自**完整**写到同目录临时文件，
两个都成功后才逐个 `os.replace`。

★ 如实声明：**单文件原子；两个文件之间没有跨文件事务，存在一个极小的崩溃窗口。**
第二次替换失败时抛明确异常（说明「targets 已换、ledger 没换」），**并提示不要重跑本批次** ——
下次执行的 **C 组一致性检查**会把不一致指出来。这就是 C 组存在的理由。
**不为此引入数据库或事务框架。**

**幂等 / 重复执行：** `batch_id` 是**不可变批次**。同一 `batch_id` 再执行 → **整体拒绝**
（B 组），**不**把原来的 promoted 记录改成 `target_already_exists`。
`target_already_exists` 的正确场景是：**新批次**尝试晋升**已由别的批次晋升过**的场景 ——
这时给新批次写一条 `skipped`，旧批次的 promoted 历史**原样保留**。
★ 「幂等」在这里指**重跑不改变已有 targets 与台账**，**不**表示重跑再次成功写入。

### 6.3 `targets.jsonl` 的三条硬规矩

| 禁止 | 判据 |
|---|---|
| **target 重复** | 同一个 `scenario_id` 出现两次 → 装载即失败，并指出行号 |
| **孤儿 target** | `scenario_id` 在任何规格里都不存在 → 装载即失败 |
| **同 ID 后写覆盖** | 禁止「后来的静默盖掉先前的」；要替换必须显式先删或另立 ID |

**装载关联：** `load_scenarios(paths, targets=<path>)` 按 `scenario_id` 把 target
**合并到内存里的 `ScenarioSpec`**。
★ `targets.jsonl` **不写回种子文件**；人手维护的 `训练集/scenarios/*.jsonl` 保持人手维护。

**行的形状（Task 4 定稿）：**

```json
{"scenario_id": "c-001", "assistant_target": { "...reviewed target..." },
 "promotion_batch_id": "answer-candidates-a"}
```

- `extra="forbid"`；`scenario_id` / `promotion_batch_id` 非空；
- `assistant_target.review_status` **必须是 `reviewed`**（`pending` 的 target 不该在这里）；
- **不得**存 `question` / `steps` / `observation`，也**不得**存整条 `ScenarioSpec`；
- 一条 `scenario_id` 对应**唯一** target。

★ `promotion_batch_id` 是**逐行** provenance，不是文件级的 —— `targets.jsonl` 会跨批次
累积，每行记「哪一批把它冻进来的」。这样 §6.2 那条「targets 与 ledger 记录同一个
promotion batch_id」在本批次新晋升的行上逐行成立，崩溃后也能靠它逐行核对。
★ 这里**不复用** `check_batch_id` 的安全文件名正则：那个值只从
`CandidateRow.batch_id` 来（模型层已校验），在这里再写一遍就是第二份实现；
`promote` 在入口处对候选文件调一次即可。

**`load_scenarios` 的合并语义（`targets=None` 时行为逐字不变）：**

| 情形 | 结论 |
|---|---|
| `targets=None` | 不读 sidecar，与从前**逐字相同** |
| 显式给了路径但**文件不存在** | **报错** —— 不能静默当空（那是把「路径写错」伪装成「还没有 target」） |
| 空文件 | 合法；所有场景 `assistant_target is None` |
| 重复 ID | 报错并**指出两个行号** |
| 孤儿 target | 报错 |
| target 对应的场景**已有** `assistant_target` | 报错，**不覆盖** |
| 合并结果 | 重新过 `ScenarioSpec.model_validate`；**磁盘上的种子一律不改**；顺序仍按场景输入顺序 |

★ **「同 ID 后写覆盖」必须失败**，不采用「最后一条获胜」。

### 6.4 来源归属：模型**只指认 id**，标题由现有渲染器产出

★ **两条错法都不许用：**

1. **把 `ToolResult.sources` 全部列出来** —— 会把检索到但答案**没用**的干扰文档也标成依据；
2. **让 LLM 直接写文件标题** —— 它会编、会改写、会混淆。

**唯一流程（Task 3 已按此实现）：**

```text
ToolResult.sources
  → EvidenceLedger 构造稳定 evidence_id      ← 复用 backend/agent/core/evidence.py
  → evidence catalog（id/title/heading_path/摘要）连同 observation 进 prompt
  → draft 返回结构化 DraftAnswer { content, used_evidence_ids }
  → 校验 used_evidence_ids 全部在本次 catalog 里（不在 → redteam，不静默丢）
  → 按 id 取回真实 Source
  → 适配成 {"source": source.title, "heading_path": source.heading_path}
  → 用**现有统一渲染器** build_source_block() 产出来源块
  → 拼成完整 proposed_target.content → candidate_status = pending
  → 人工审核看到的是**成品**
```

★ **不得在 compiler 里重新实现 `short_title` / `clean_path` / 书名号 / 去重规则** ——
`answer_candidates.py` 只做「适配成 `build_source_block` 要的形状」这一步。

★ **来源块的形状以 `build_source_block()` 的**实际输出**为准。** 当前生产格式主要是
「`《简短真名》` + `heading_path`」，**不是**「第 N 行」——
设计早期写的「《…》第 N 行」是**错的**，已按实现订正。

★ **审核必须发生在拼接之后**：先审半成品、之后再自动拼来源行，等于把一段
**没人看过**的文字塞进训练集。

⚠️ **一处实测到的边界**：`strip_model_sources` 从**最后一条**模型来源行截断，
所以**更早**那条会留下（`finalize_answer` 刻意「宁可留痕，不可误删正文」）。
对训练候选来说「留痕」= 把一条编造来源写进目标，不可接受 ——
所以 `draft_candidate` 剥完**再查一遍**，仍有残留的一律进 redteam（`unstrippable_sources`）。

### 6.5 五类语义措辞规范

**规范的是「行为边界」，不是固定模板。** 句式可自由发挥，下列边界不得越过。
机械门只拦明显越界（关键词/结构），判不准的一律进人工红队队列。

| kind | 必须做到 | 严禁 |
|---|---|---|
| `grounded_answer` | 先给结论再分点；每点带证据里的数字、条件、例外；结尾标确定性来源行 | 补充证据里没有的校内事实；把观察不到的内容说成规定 |
| `partial_answer` | **明说哪部分查到了、哪部分没查到**；只对查到的部分下结论 | 把部分当成全部；用常识补齐缺口；不提「还有一部分没查到」 |
| `no_evidence_answer` | 说明「**查了**，当前资料里没有相关内容」；给出可核对的方向（如问教务/看当年通知） | **说「学校没有规定」**（那是对事实的推断，不是检索结论）；用模型记忆补一条校内规定出来 |
| `irrelevant_result_answer` | 说明检索返回的内容与问题不是一回事，必要时点出返回的是什么 | 把无关内容硬套成答案；假装它就是答案 |
| `tool_failed_answer` | 说明**这次查询没成功**；区分可重试与不可重试（参数问题就说清是参数问题） | **写成「本次检索未找到依据」**；把失败说成「库里没有」 |

★ **两对最容易被写混**，各由一个红灯测试钉住：

1. `no_evidence_answer` ↔ `no_tool` 的 `direct_answer`：
   后者**根本不该出现「没查到」的说法**（它本来就不该调工具）；
2. `tool_failed_answer` ↔ `no_evidence_answer`：
   前者是「查失败了」，后者是「查成功了但库里没有」。**二者不得互换**（TDD #11）。

### 6.6 ★ 语义边界（**必须写清楚，否则会被当成熟度**）

**`evidence_id` 合法，只证明一件事：这条来源来自本次 `ToolResult.sources`。**

它**不**证明：

- `Source` 的标题能被渲染出来 —— title 为空会被 `build_source_block` 整条过滤，
  渲染不出来的一律进 redteam（`unrenderable_source`）；
- `Source` 在语义上支持答案里的**每个事实主张** —— 这需要人读，或者专门的证据 QA。

**Task 3 是「来源身份完整性门」，不是事实蕴含判断器。**
`pending` candidate **必须**经过人工审核；「每个事实主张是否由所选证据支撑」
是**后续 evidence QA** 的独立任务。

★ **不许**加一个关键词匹配器就宣称语义核验做完了 —— 那会把「没做」包装成「已做」。

### 6.7 `batch_id` 是**安全文件名**，不是「起名风格」

`batch_id` 会被**直接当文件名**用，所以它的格式约束是安全约束：

```
^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$      # 字母/数字开头，1–128 位，只含字母数字下划线连字符
```

`/` `\` `..` 空格 冒号 盘符 空串 —— 每一个都能把产物**写到目录外面去**。

★ **一条规则一处实现**：`CandidateRow` 与 `write_candidates` 复用同一个
`check_batch_id()`，不各写一套（两处迟早漂移，漂移的那一侧会写到目录外）。
校验必须发生在**构造 prompt / 调用 draft / 建任何输出之前**。

### 6.8 必须防住的六种情况

| 禁止 | 判据 |
|---|---|
| 生成程序直接写 `review_status=reviewed` | Task 3 红灯：LLM 候选落盘后必须是 `pending` |
| observation 已变但沿用旧答案 | Task 5 红灯：binding 不一致即隔离 |
| 无证据答案用模型记忆补齐校内事实 | **人工门槛**；机械门只拦明显越界（Task 7 的审计器） |
| `tool_failed` 被写成「本次检索未找到依据」 | kind 与状态绑定；文案属人工审核面 |
| `no_evidence` 被写成「学校没有规定」 | 同上 |
| 答案含可见 `Thought`、内部规划或分析过程 | Task 5 的机械 denylist + 人工红队队列 |

★ 前三行与后两行的性质不同：**机械门能证明越界的写硬失败；不能机械裁决的进红队队列**，
不许自动判绿，也不许自动删除（沿用阶段 6 设计 §2.3 第 9 条）。

### 6.9 promote 的稳定原因码与台账

**台账是累计的**（Task 4.1）：先完整读入并校验已有台账，再在内存里追加本批，
最后**整体原子替换**。空/不存在的台账在首次晋升时视为空集合。
**不得**改成「每批一个无人汇总的散落台账文件」，也不得用时间戳 / 随机 `run_id` 掩盖重复批次。

**唯一键 = `(batch_id, scenario_id)`** —— 一批里一条场景只该有一行。
已有台账中该组合重复 → **整体失败**。

**每条输入候选在 `ledger.jsonl` 里恰好一行**（晋升与跳过都写）：

```json
{"scenario_id": "c-001", "batch_id": "answer-candidates-a",
 "decision": "promoted", "reason": "promoted",
 "reviewed_by": "human:owner", "review_note": null,
 "candidate_sha256": "…", "promoted_target_sha256": "…",
 "trajectory_checked": true, "tool_calls_executed": 1, "detail": ""}
```

- `decision` ∈ {`promoted`, `skipped`}；
- `candidate_sha256` = 对**该 `CandidateRow` 的 canonical JSON** 求 SHA-256；
- `promoted_target_sha256` = 对**最终 `TargetRow` 的 canonical JSON** 求 SHA-256，
  **未晋升时为 `null`**；
- canonical JSON 口径与 §5.2 **同一套**：`ensure_ascii=False`、`sort_keys=True`、
  `separators=(",", ":")`。

**★ `replayed` 已被拆成两个明确字段**（含义不清，故不留兼容字段）：

| 字段 | 含义 |
|---|---|
| `trajectory_checked` | 这条 approved 候选是否**真的进入**了轨迹校验（含校验失败的那些） |
| `tool_calls_executed` | 本次**实际**调用了 `replay_step` 几次 |

| 情形 | `trajectory_checked` | `tool_calls_executed` |
|---|---|---|
| `pending` / `rejected` | `false` | `0` |
| approved 但卡在**静态门**（bucket / kind / binding / 未 pending） | `false` | `0` |
| approved `no_tool` / `clarify`（`steps=[]`） | **`true`** | **`0`** |
| approved 单工具 | `true` | `1` |
| approved 两步 ReAct | `true` | `2` |
| approved 进入重放后 `replay_mismatch` | `true` | 已实际调用的次数 |

`Summary` 里同名的两个字段分别是**本批走完校验的候选条数**与**本批调用总次数**。

**不得包含**：时间戳、绝对临时路径、`ToolResult` 的 `repr`、未稳定排序的集合、
模型内部推理。

| 码 | 触发 |
|---|---|
| `promoted` | 全部一致性门通过，已冻结 |
| `candidate_pending` | 人还没裁决 —— **不重放**、不晋升 |
| `candidate_rejected` | 人已经否掉 —— **不重放**、不晋升；仍记 `reviewed_by` / `review_note` |
| `scenario_not_found` | 候选指向的 `scenario_id` 不在场景集合里 |
| `scenario_not_reviewed` | 场景自身还不是 `reviewed` |
| `target_already_exists` | 该 `scenario_id` 已由**别的批次**晋升过（**新批次**写这条；旧批次的 promoted 历史原样保留） |
| `bucket_mismatch` | `candidate.bucket != spec.bucket` |
| `target_kind_mismatch` | `kind` 与 `target_kind(bucket, expected_evidence.state)` 不符 |
| `candidate_target_binding_mismatch` | 候选**顶层** binding 与 `proposed_target` 里那份不一致 |
| `target_not_pending` | `proposed_target.review_status` 不是 `pending`（越权写入） |
| `replay_mismatch` | 轨迹不符合 `ScenarioSpec`（`validate_trajectory` 报错） |
| `evidence_binding_mismatch` | 轨迹合法，但重算 binding 与候选不一致 |

★ **任一不符都不自动修**：不修 `kind`、不修 `binding`、不重新生成内容、不改 `content`。
`promote` 的职责是**搬运与拒绝**，不是修补 —— 发现需要它顺手修，说明审核环节没审全，
应当停下来报告。

★ **别把 `target_not_pending` 与 §7.4 的 `target_review_pending` 混淆**：两者不在同一层。
前者是 **promote** 在**候选**上判的（`proposed_target` 竟然已经是 `reviewed` = 越权写入）；
后者是 **Task 5 的编译器**在**场景**上判的（合并进来的 `assistant_target` 还是 `pending`）。

---

## 7. 确定性编译器的最终职责

`compile.py` 最终**只**负责：

1. 校验 `ScenarioSpec`；
2. 重放真实工具；
3. 构建 observation；
4. 计算 evidence binding；
5. 校验 `assistant_target` 的**存在性、状态、kind、binding**；
6. 追加最终 assistant 消息；
7. 输出完整 SFT；
8. 对不合格场景写 quarantine 与真实 manifest。

**编译器不得：** 调用 LLM、改写答案、自动补 target、把 `pending` 升级为 `reviewed`、
在 binding 不一致时继续放行。

### 7.1 `build_trajectory` 是**内部函数**，不是旧数据产品

**精确接口（Task 5 定稿，依赖注入）：**

```python
build_trajectory(spec, *, system_prompt, replay=None) -> tuple[list[dict], list[dict]]
compile_full(spec, *, system_prompt, replay=None) -> dict
compile_dataset(inputs, output, manifest_path, quarantine_path,
                *, targets_path, system_prompt) -> BuildSummary
```

★ **接口矛盾与裁决（Task 5 开头修正）**：旧 `compile.py` 从 `llm_client` 导入
`SYSTEM_PROMPT`，而计划又要求 `compile.py` 不依赖 `llm_client`。
**裁决 = 显式依赖注入**：

| 参数 | 规则 |
|---|---|
| `system_prompt` | **必填、无默认值** —— 不复制、不缓存、**不在 `compile.py` 内重新定义**；调用方显式传当前生产那句 |
| `targets_path` | **必填、不接受 `None`** —— 不扫目录猜、不静默当空 |
| `replay` | `None` 时用真实 `replay_step`；测试注入替身 |

★ `compile.py` **不得导入 `llm_client`**（也不得导入 `answer_candidates`），
**不得新增第二个提示词真相源**：项目里只有 `llm_client.SYSTEM_PROMPT` 一处。

- `build_trajectory` 只搭 `system → user → (function_call → observation)*` 并返回重放结果，
  **不追加 assistant、不写任何文件**；
- `compile_full` 在轨迹之上**追加** assistant，产出完整 SFT，且**函数级**过准入；
- `compile_dataset` **只有 `full_sft` 一个正式出口**。

★ `test_replay.py::test_no_tool_scenario_has_only_system_and_user` **最小修改为测
`build_trajectory`**：它钉的语义（「没有步骤时不产生工具轮」）不变，变的只是被测对象
从公开接口换成内部函数。**不为保住一个测试而保留旧公开接口**（`compile_scenario` 已删除）。

### 7.2 完整 SFT 的形状

```text
工具型:        system → user → (function_call → observation)* → assistant
no_tool:       system → user → assistant
clarify:       system → user → assistant
tool-terminal: system → user → (function_call → observation)* → function_call   ← 到此结束
```

★ **最后一条消息必须是「模型产生的一轮」**（2026-10-15 订正，原措辞为「最后一个角色
  必须是 `assistant`」）。

  原文是从**「末轮一定有答案文本」这个隐含假设**推出来的，不是一条独立规律。
  `create_ppt` 是第一个**以工具调用收尾**的桶，它把这个假设暴露了：线上主图在模型发出
  `create_ppt` 之后把这一轮交给 PPT 子图并**结束**（不回流），那条调用**之后**既没有
  observation（运行时渲染完就丢），也没有由主图模型写出的正文（那段文本是路由硬编码 /
  子图产出的）。

  因此订正后的判据是：末轮要么是 assistant 文本、要么是 assistant 的工具调用。
  **这是措辞订正，不是放宽** —— 半成品 `trajectory_prefix` 仍然不是产物（§7.3）。

  终态桶集合的唯一真相源是 `训练集/compiler/models.py::TOOL_TERMINAL_BUCKETS`；
  完整裁决与结构硬门见 `docs/superpowers/specs/2026-10-15-create-ppt-supervision-ruling.md`。

### 7.3 `artifact_kind`：只有 `full_sft` 一种

manifest 里如实写：

```json
"artifact_kind": "full_sft",
"output_terminal_roles": { "assistant": 5, "function_call": 1 }
```

★ **`compile_dataset` 只有 `full_sft` 一个正式出口。** 不设 `artifact_kind` 可选 mode，
**不保留 `trajectory_prefix` 生产模式** —— 半成品不再是可交付物，
免得「顺手跑一次拿前缀」再次变成事实上的数据出口。

★ **批级布尔 `has_final_assistant` 已删除**（2026-10-15，Task 8B.4 裁决）。它的自然含义是
「**每条**完整样本是否以 assistant 收尾」，而 tool-terminal 样本**不是** ——
混合批次下继续写 `true` **会误导审计**。改记**终态角色分布**：

- 两项**恒定出现**（计数 0 也写）、顺序固定（`assistant` → `function_call`），
  与 `quarantine.by_reason` 同一纪律；
- 硬约束：`assistant + function_call == output.rows`；
- 出现这两者之外的终态 → **整体失败**，不自动长出新格子；
- `artifact_kind` **仍是 `full_sft`** —— 两种收尾都是**完整监督样本**，不是半成品。

**缺 target / target 未审核 / kind 不符 / binding 不匹配 → 进 quarantine，不输出半成品。**
★ 这四档只对**有末轮文本**的场景成立；tool-terminal 桶没有文本答案可审，
四档**不适用**（轨迹本身仍由共享校验器把关）。

### 7.3.1 manifest 的 provenance（Task 6 定稿）

manifest 足以解释「这份 full_sft 是由什么输入生成的」：

| 字段 | 内容 |
|---|---|
| `artifact_kind` / `output_terminal_roles` | `full_sft` / 终态角色分布（两项恒定出现） |
| `system_prompt` | `sha256`（**UTF-8 原始字节，不 trim、不规范化换行**）+ `chars` |
| `schema` | 沿用 `schema_snapshot()` |
| `inputs[]` | `{name, sha256}` —— **逻辑名称**，不是绝对路径 |
| `targets` | `{name, exists, rows, sha256}` —— 记**整个 sidecar**，不只是用上的行 |
| `output` / `quarantine` | `{name, exists, rows, sha256}`（quarantine 另有 `by_reason`） |
| `target_provenance` | `source` / `generator` / `model` / `prompt_version` 四个分布 |

★ **不记录**：时间戳、随机 `run_id`、用户名、机器名、临时目录、绝对路径、
`ToolResult` 的 `repr`、**完整 system prompt**、`reviewed_by` 等人工身份信息。

**★ 路径稳定性**：`inputs` / `targets` / `output` / `quarantine` 一律只记
`Path(path).name`；`BuildSummary.inputs` 同样不得残留绝对路径。
**两个不同根目录、内部文件名与内容完全相同 → 三份产物逐字节一致。**
多个场景输入**重名**时在工具调用与写盘前**明确拒绝**（manifest 只记名字就分不开）——
**不得**用字符串替换临时目录来伪造稳定性。

**★ `quarantine.by_reason`**：八项**恒定出现**（0 也写），键顺序 = §7.4 的准入优先级；
八项之和 == `quarantine.rows`；出现**未知 reason → 整体失败**，
不自动长出第九类；分桶来自**真实 quarantine 行**，不是手写常量。

**★ `target_provenance`**：只统计**真正进入 output** 的 reviewed target
（被 quarantine 的、缺 target 的、pending/rejected 的、场景自身的 `provenance` 都不算）。
条目为 `[{"value": null, "count": 2}, {"value": "candidate/1", "count": 3}]`：

- 每个分布的 `count` 之和 == `output.rows`；
- `null` 保持 JSON `null`，**不写成 `"None"` / `"null"` / 空串**；
- 排序稳定（`null` 在前，其余字符串升序），**不依赖 `Counter` 的首次出现顺序**；
- 分布从**实际 emitted targets** 计算，不能手写；
- **output 行仍不得携带**这些审计字段（只有 `tools` 与 `messages`）。

**★ 安全落盘**：

1. **先查路径**（在工具重放与写盘**之前**）：`output` / `quarantine` / `manifest`
   互不相同，且都不与任一场景输入或 `targets_path` 同一个文件；
2. 三份产物各自写**同目录临时文件**（`flush` + `fsync`）；
3. 基于 **staged 文件**的真实内容算哈希，再写 manifest；
4. 固定顺序 `os.replace`：`output` → `quarantine` → **`manifest` 最后**。

**★ 失败边界（Task 6.1 修正，不写过头话）**：

| 阶段 | 正式产物 |
|---|---|
| 内存计算与 stage 全部完成之前 | **一个字节都不动** |
| **stage 失败** | 正式文件**不变**；临时文件清理 |
| **replace 失败** | **可能已有部分正式文件被替换**（三份之间没有事务）；清理尚存的临时文件；抛 `RuntimeError` 并**写明已替换 / 未替换各是哪些**；**不回滚、不补写** |

★ 旧版的措辞「任何失败都不留半成品、不留 `.tmp`」**过头了** ——
stage 失败确实如此，replace 失败**不是**。改为上表。
★ `manifest` 最后替换，因此中断时**旧 manifest 与新的 `output` / `quarantine` 哈希不一致**，
**后续消费者按 manifest 里的哈希核对即可发现**。**本轮只检测与明确报错，不实现自动恢复。**

### 7.4 隔离理由码与**固定优先级**

沿用 Task 5.1 已有三个：`review_pending` / `review_rejected` / `evidence_mismatch`。
Task 5.2 新增五个：

| 码 | 触发 |
|---|---|
| `target_missing` | 场景已 `reviewed`，但没有 `assistant_target` |
| `target_review_pending` | target 存在但 `review_status = pending` |
| `target_review_rejected` | target 存在但 `review_status = rejected` |
| `target_kind_mismatch` | `kind` 与 §4.1 推导值不符 |
| `evidence_binding_mismatch` | 重放后重算的 binding 与 target 里存的不符 |

**redteam 的原因码**（Task 3 产出的候选侧，与上面的隔离码不同层；见 §6.1）：

| 码 | 触发 |
|---|---|
| `invalid_draft_json` | draft 没返回合法 JSON，或缺字段 / 多字段 |
| `visible_thought` | content 含可见推理标记 |
| `unknown_evidence_id` | 指认了不在本次 catalog 里的 id |
| `missing_grounding` | `grounded_answer` / `partial_answer` 没指认任何来源 |
| `evidence_not_allowed` | 非 grounded/partial 的 kind 却指认了来源 |
| `empty_content` | 剥离自写来源行后正文为空 |
| `unstrippable_sources` | 剥完**仍有**模型自写来源行残留 |
| `unrenderable_source` | 指认的 id 合法，但渲染器产不出块（title 为空等） |

**★ 判定按固定优先级，每条场景只归入第一个命中的原因：**

```text
1. 场景审核     review_pending / review_rejected
2. 工具证据状态 evidence_mismatch
3. target 存在性 target_missing
4. target 审核   target_review_pending / target_review_rejected
5. kind          target_kind_mismatch
6. binding       evidence_binding_mismatch
```

**理由**：一条场景可能同时踩中多条（例如既缺 target、binding 又无从谈起）。
若按「哪条先算出来」归因，同一份输入换个实现顺序就会得到不同的 reason，
`quarantine` 的统计就失去意义。**固定优先级 = 归因可复现。**
且后一档的前提是前一档已通过 —— 例如 binding 只有在 target 存在且已审核时才有得比。

★ **每类原因码互不相同**，不许把「缺 target」与「证据不符」都写成一句话——
事后分不出被拦的到底是哪种。

**★ 准入只有一份实现（`_admit`）**：`compile_full` 直接调用时抛
`AdmissionError(reason, detail, extra)`；`compile_dataset` 捕获后写进 `quarantine`。
**不许让 `compile_full` 与 `compile_dataset` 各写一套准入判断**，也不为此建通用规则引擎。

**★ `evidence_mismatch` 那一行的明细只有四格**（`_mismatch_record`）：

| 字段 | 是什么 |
|---|---|
| `expected_state` | 场景**声明**的末态 |
| `observed_state` | 当次轨迹的**实际**末态（`steps` 为空时是 `no_evidence`） |
| `failed_tools` | 当次轨迹里报 `tool_failed` 的工具名 |
| `detail` | **共享校验器抛出的原始错误文本** |

★ **不产出 `mismatch_kind`**（Task 5.1 已删除）。想从这几格或错误文本反推
「是步错还是末态错」，就等于在 `compile.py` 里**再写一套**步骤数 / 工具名 / 状态 /
末态分类器 —— 那正是共享 `validate_trajectory` 要消灭的东西，而且推错了没人会知道
（步数不一致会被推成「末态错」）。**真正的原因由 `detail` 如实携带**，
前三个字段只是**当次轨迹快照**，**不决定 reason**。

**★ 第九类不是 reason，是 hard failure。** `reviewed` 的 target 里若仍含可见推理标记
（`Thought:` / `思考：` / `<analysis>`），那是**机器拥有的 sidecar 损坏** ——
抛独立的 `CorruptedTarget`，**整个批次中止**（不静默删文本、不自动改写、
**不混进 quarantine 的 reason 统计**）。

**★ 编译器的机械安全门**：输出 assistant 内容里不得出现上述三个标记；
编译器**不得**改写、润色、添加来源、删除来源或补全 —— final assistant **逐字**用
`target.content`。

**★ 网络隔离要**运行时**证明**：源码扫描（模块级 import 不含 `llm_client` / `openai` /
`requests` / `urllib` / `answer_candidates`）**不够** —— 还要在执行期间把
`socket.socket` 换成「调用即失败」，并断言最终 assistant、observation、tools
**三样都真的生成出来了**（否则会靠「根本没走主路径」假绿）。

---

## 8. 与现有实现的事实冲突汇总

**未静默迁就任何一条。** 位置精确到 `文件:行`。

| # | 冲突 | 证据位置 | **裁决后的处理** |
|---|---|---|---|
| 1 | `bucket` 与 `state` 对 `no_tool` 给出不同 kind | `compile.py:137`（`NO_TOOL_STATE`） | `bucket` 优先，只对 `no_tool` / `clarify` 生效；`NO_TOOL_STATE` **只作记账值，不作回答语义**（§4.2） |
| 2 | kind 推导对 `stop_and_recover` 有二义 | `训练集/scenarios/stop_and_recover.jsonl` | kind 由 `expected_evidence.state` 推导；**不建第二个「运行末态真相源」**（§4.1 / §4.3） |
| 3 | `compile_scenario` 不产生 assistant，且有测试钉着 | `compile.py:127-131`、`test_replay.py:96-100` | `build_trajectory` 降为**内部函数**；该测试**最小改为测它**（§7.1） |
| 4 | `ARTIFACT_KIND` 是模块常量，只表达一种产物 | `compile.py:39-41` | 出口**只有** `full_sft`；**不做可选 mode，不留 `trajectory_prefix` 生产模式**（§7.3） |
| 5 | 隔离只有三类 reason，Task 5.2 需要八类 | `compile.py:44-49`、`compile.py:177-210` | 新增五个码，按**固定优先级**归因（§7.4） |
| 6 | `extra="forbid"`，`assistant_target` 必须是声明字段 | `models.py:71` | `ScenarioSpec` 加**可选**字段；target 另存 `targets.jsonl`，装载时按 ID 合并（§6.2 / §6.3） |
| 7 | 生成的候选若带时间戳则文件不可复现 | 项目既有口径（Task 5.1.1「连跑两次字节一致」） | `batch_id` **由调用方显式传入**（§6.1） |

---

## 9. Task 5.2 与 Task 5.3 的边界

**Task 5.2 只解决：** 完整 assistant target、证据绑定、候选生成与审核分离、确定性 full SFT 编译。

**Task 5.2 不解决（留给 Task 5.3）：**

- 由第一步 observation **自主决定**下一次工具或参数的真正 ReAct；
- 中间轮 assistant 的工具决策生成（即 `system → user → fc → obs → assistant(决策) → fc → obs`）；
- **混合多步轨迹的运行时聚合决策** —— 本阶段「整条轨迹最终算有依据还是没依据」
  **一律由 `expected_evidence.state` 显式声明**，不在编译期临时聚合；
- 多步自主规划与停止策略的学习；
- 批量调用线上模型。

★ **不得借 Task 5.2 偷改 Task 5.3 的内容。** 尤其：**不要**为了「看起来像 ReAct」
就给中间轮也塞 assistant 文本 —— 那正是 Task 5.1 花力气标注掉的边界。

---

## 10. 非目标

- 不训练可见 `Thought:` 思维链；
- 不物理 merge base 与 LoRA；
- 不引入第二个决策模型；
- 不为未来需求做字段级过度抽象（多候选打分、A/B、成本核算等一律不做）；
- 不在本轮实现任何 .py。

---

## 11. 决策点状态

**已裁决（2026-10-14，用户裁定）**

| # | 事项 | 裁决 |
|---|---|---|
| 1 | promote 把 target 写到哪 | **独立机器拥有文件** `训练集/scenarios/targets.jsonl`；`load_scenarios` 按 `scenario_id` **合并到内存**（§6.2 / §6.3） |
| 2 | 是否增设 `refusal` kind | **暂不增加**；`no_tool` 一律 `direct_answer`（§4.2） |
| 3 | 是否沿用来源行机制 | **沿用**，但必须在**人工审核之前**拼好（§6.4） |
| 4 | 五类措辞边界 | 写成 **§6.5 措辞规范**：规范行为边界，不锁死成相同模板 |

**尚存待决**

- **无。** Task 1–7 均无待决前提。
- Task 8（vLLM 批量生成与真机验收）的外部前置另行确认：服务器授权、本次用哪个模型、
  提示词版本号、`batch_id`。

---

## 12. 成功标准与停止条件

阶段 6 完成时，应能交付：**从 `ScenarioSpec` 确定性重建的 full SFT 候选集**、
完整 manifest、隔离队列（八类 reason 齐全）、以及可追溯的候选→审核→晋升链条。

以下任一情况立即停在对应任务，不用「先生成再说」绕过：

- `compile.py` 的执行路径里出现任何网络调用；
- target 的 `review_status` 被非人工流程写成 `reviewed`；
- binding 不一致时仍放行；
- 评测答案来自训练候选，或机器抽取后冒充人工金标；
- 完整样本不以 `assistant` 结尾，或含可见思维链；
- 需要服务器写入、上传、训练或部署但未获明确授权。