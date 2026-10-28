# Task 8B.4：`create_ppt` 监督语义裁决

**日期：** 2026-10-15（2026-10-15 按用户裁决修订）
**状态：** **已裁决 = 方案 B（修正版）**，本文件为该裁决的实施依据
**范围：** `create_ppt` 场景在 `full_sft` 里的收尾结构；以及实施它的最小改动清单。

---

## 0. 裁决原文（用户，2026-10-15）

> create_ppt 是 tool-terminal 场景，训练样本应以模型产生的 create_ppt function call
> 收尾，**不追加 observation，也不追加文本 assistant**。

---

## 1. 真实运行消息序列

`seed-create-ppt-001` 的问句是「帮我做一份讲学校学业预警制度的 PPT，给大一新生班会用」。
线上真实路径（模型第 1 轮先取证、第 2 轮喊 `create_ppt`）：

```text
第 1 轮  agent_decide → model.decide(messages)          ← 非流式
         messages = [system(生产 SYSTEM_PROMPT), user(问句)]
         模型返回 tool_calls = [search_regulations]
    ↓ route_after_decide → "tools"
execute_tools  真的执行 search_regulations
         messages += [assistant(tool_calls=[search_regulations]), tool(observation)]
    ↓ route_after_tools → "decide"
第 2 轮  agent_decide → _stream_round(messages, "auto") ← 流式
         模型**输入**  = [system, user, assistant(tool_calls), tool(obs)]
         模型**应生成** = tool_calls = [create_ppt{goal, audience}]      ← ★ 这就是监督目标
    ↓ route_after_decide → "ppt"                       ← graph.py:388-389
ppt_handoff
         registry.call("create_ppt", args)             ← 只为**校验**参数
         reserve_task(...) → task_id
         _drain_ppt(...)  跑 PPT 子图，转发它的进度事件
    ↓ graph.add_edge("ppt", END)                       ← graph.py:331-332
结束
```

**模型一共只被问了两次**（`test_the_runtime_turn_ends_right_after_the_create_ppt_handoff`
断言 `len(seen) == 2`），第 2 轮之后再没有第三次。

实测槽位：

```text
模型第 2 轮输入 ：['system', 'user', 'call', 'obs']
模型应生成的目标：                  'call'   ← create_ppt，样本到此结束
```

### 1.1 ★ 概念订正（2026-10-15 用户指出，原分析有误）

原分析写过：「`create_ppt` 的 call 在运行时不产生消息，因此训练里有 call 是教了模型
看不到的上下文」。**这个结论不准确，本条作废。**

正确的是：

- **`create_ppt` 的 function call 本来就是 SFT 样本的监督目标**，不是下一轮输入上下文。
  它对应「模型第 2 轮应当生成什么」，**必须**出现在样本里。
- 真正多余的只有两样：**该调用之后的 observation**，和**末轮文本 assistant**。

所以正确的训练序列是：

```text
system
user
function_call(search_regulations)
observation(search_regulations)
function_call(create_ppt)          ← 样本在这里结束
```

运行时不产生消息的，是**这一条调用之后的**东西 —— 不是这条调用本身。

---

## 2. 当前 SFT 消息序列（**未修正前**）

`build_trajectory` + `compile_full`：

```text
system → user → function_call(search_regulations) → observation → function_call(create_ppt) → observation(【PPT任务】…) → assistant
```

槽位：`['system','user','call','obs','call','obs']` + 末轮 assistant

---

## 3. 逐项差异

| # | 位置 | 运行时 | 未修正的 `full_sft` | 正确处置 |
|---|---|---|---|---|
| 1 | `create_ppt` 的 call | 模型**应生成的输出** | 有 | ✅ **保留** —— 它是监督目标 |
| 2 | `create_ppt` 的 observation | **不存在**（`ppt_handoff` 渲染后丢弃） | 有 | ❌ **删掉** |
| 3 | 末轮 assistant | **不存在**（handoff 后不再调用模型） | 有 | ❌ **删掉** |
| 4 | 用户可见 PPT 文本 | 路由硬编码（`chat.py:157-161`）+ 子图产出 | 说是模型写的 | 归属错，随 3 一并消除 |

★ 第 1 行是**原分析弄错的那一条**，已订正。

★ 第 2/3 行是同一件事的两面：运行时在 `create_ppt` 之后**没有任何主图模型参与**，
所以那两格在生产里不存在。

### 3.1 派生冲突（另立任务，本轮不改）

`agent_decide` 的 **text-wins** 规则（`graph.py:103-108`）：流式轮里模型若
**既说话又喊工具**，`tool_calls` 被显式清空、调用被丢，`route_after_decide` 走
`answer` 分支，**任何工具都不会执行**。

这不是本任务引入的，但它是一条此前没被记录过的行为，且属「静默 fallback」家族
（本项目铁律禁忌）。已用特征化测试钉住当前行为
（`test_a_text_plus_call_round_silently_drops_the_create_ppt_call`）。

**处置：只登记 + 保留特征化测试，标为后续独立任务，必须在生产验收前处理。
不在 Task 8B.3 里顺手改 `graph.py`。**

---

## 4. 交付协议（澄清一处命名）

用户问句里提到的 `ppt_ready` **在当前代码里不存在**。真实协议是：

| 环节 | 事件 / 接口 | 位置 |
|---|---|---|
| 报进度 | `("ppt", {"ppt_task": {task_id, stage, progress, question}})` | `subgraph.py:440-443`、`475-479` |
| 生成完成 | `("ppt", {"ppt_draft": {task_id, stage:"draft", pages}})` —— **「待确认」，无下载地址** | `subgraph.py:521-523` |
| 出错 | `("ppt", {"ppt_error": {...}})` | `subgraph.py:490-491`、`506-507`、`513-517` |
| 子图追问 | `("delta", 缺失项问句 + "？")` | `subgraph.py:510-511` |
| 路由硬编码的一句 | `("delta", "正在制作 PPT；如需补充信息，我会在这里追问。\n")` | `chat.py:157-161` |
| 真正的下载 | `POST /ppt/task/{id}/confirm` → `{"stage":"ready", download_url}` | `routers/ppt.py:115-123` |

★ **用户看到的每一句关于 PPT 的话，都不是主图模型写的。** 这正是末轮 assistant 不该存在的理由。

---

## 5. 裁决 = 方案 B（修正版），以及为什么不选其他

`create_ppt` 场景的 `intent` 是「主 Agent 自主选择 create_ppt，先取证再建任务」，
`stop_condition` 是「取证完成且 PPT 任务建好即停」。模型在这条场景里的全部职责是：
认请求 → 先取证 → 发出 `create_ppt` 调用 → **结束**。
建任务、追问、规划、起草、渲染、签发凭证全部是运行时拥有的。

**B 让样本成为运行时的忠实录像**：
`system → user → call(search) → obs → call(create_ppt)`，收尾。

| 方案 | 结论 | 理由 |
|---|---|---|
| **A** 末轮 assistant 写交付状态文本 | **不选** | ① 会顶掉 PPT 功能：`agent_decide` 有文本就以文本为准，调用被丢（§3.1，已实测）；② 那句话运行时是**路由**写的，训练模型去写会重复或需先动生产 |
| **C** 追加由 `ppt_ready`/`ppt_error` 映射的交付文本 | **不选** | ① `ppt_ready` 不存在；② 编译器离线且确定性（设计 §7），跑不了子图，照事件编文本就是编造；③ 产出归属是**子图**，不是主图模型 |
| **备选** 本阶段不把 `create_ppt` 编进 `full_sft` | 保留为退路 | 零编译器改动，但少覆盖一个桶；**用户已选 B** |

---

## 6. 实施清单（修正版）

★ 结论仍然是**编译器改，运行时一行不改** —— 「交子图后不回流」是有意设计
（`graph.py:8`、`215-217`），且有 113 条测试钉着。

### 6.1 终态规则**只有一个真相源**

```python
# 训练集/compiler/models.py
TOOL_TERMINAL_BUCKETS: frozenset[str] = frozenset({"create_ppt"})
```

由以下模块**共同引用**，任何一处都**不得再手写 `"create_ppt"` 判据**：
`compile.py`、`replay.py`、`scripts/generate_answer_candidates.py`、各测试。

### 6.2 必须改的三处，而不是只改 `compile_full`

★ **只让 `compile_full` 不追加 assistant 是不够的。** `build_trajectory` 对每一步都追加
`function_call` + `observation`；只取消末轮 assistant，样本会以
`function_call(create_ppt) → observation(【PPT任务】…)` 收尾，**仍不符合真实运行轨迹**。

| # | 文件 | 改法 |
|---|---|---|
| 1 | `训练集/compiler/models.py` | 新增 `TOOL_TERMINAL_BUCKETS`（**唯一真相源**） |
| 2 | `训练集/compiler/compile.py` :: `build_trajectory` | 仍重放终态工具（用于校验参数与 `status`）、仍写 `call_message`；但**终态那一步不写 observation**，该 `function_call` 必须是**最后一条消息** |
| 3 | `训练集/compiler/compile.py` :: `compile_full` | tool-terminal 桶**不追加**末轮 assistant |
| 4 | `训练集/compiler/compile.py` :: `_admit` | tool-terminal 桶跳过第 3–6 档（target 存在性 / target 审核 / kind / binding：**没有末轮文本就没有 target 可审**）。第 1–2 档**照旧** |
| 5 | `训练集/compiler/replay.py` :: `validate_trajectory` | 新增 tool-terminal **结构硬门**（见 §6.3） |

### 6.3 结构硬门（归入现有 `evidence_mismatch`，**不新增第九种 reason**）

| 规则 | 判据 |
|---|---|
| A | tool-terminal 桶的 `steps` 非空，且**末步**工具必须是终态工具 |
| B | 终态工具**只能出现在最后一步** |
| C | **非** tool-terminal 桶**不得**出现终态工具 |
| D | tool-terminal 桶**不得**携带 `assistant_target` |

★ 放在 `replay.validate_trajectory` 里，于是它们在 `_admit` 第 2 档抛错 →
`AdmissionError(REASON_EVIDENCE_MISMATCH, …)` → 进 quarantine 的**现有**那一类。
**quarantine 仍是八类，没有第九种。**

### 6.4 `create_ppt` 不再生成 answer candidate

既然该桶没有末轮文本 assistant：

- `generate_answer_candidates.py` **不为** `create_ppt` 调 LLM；
- 不生成 candidate、不进 redteam、不 promote、不需要 `assistant_target`、
  不参与 target binding；
- 编译时**直接从 reviewed 场景 + 工具重放**生成 tool-terminal 样本。

候选 manifest 增加明确统计（**不把它算成 pending / rejected / 生成失败**）：

```json
"scenarios": {
  "reviewed": 6, "selected": 5,
  "skipped_tool_terminal": 1, "skipped_tool_terminal_ids": ["seed-create-ppt-001"]
}
```

### 6.5 manifest 终态分布（**不做模糊兼容**）

```json
"output_terminal_roles": { "assistant": 5, "function_call": 1 }
```

- **删除**批级布尔 `has_final_assistant` —— 它的自然含义是「每条完整样本是否以
  assistant 收尾」，混合批次下继续写 `true` 会**误导审计**。项目目前**还没有正式
  `full_sft`**，无需保留错误兼容层；
- 硬约束：`assistant + function_call == output.rows`；未知终态角色 → **整体失败**；
  两项**恒定出现**（0 也写），与 `by_reason` 同一纪律；
- `artifact_kind` **仍是 `full_sft`** —— 两类都是完整监督样本，不是 trajectory prefix。

### 6.6 `target_provenance` 只统计**真正带 assistant target 的行**

tool-terminal 行**不参与**该分布（不得伪造成某个 target 来源）。
`compile_dataset` 只在 `target is not None` 时 append。

### 6.7 测试

- **删除**原先那条「整个 compiled slots == 运行时输入 slots」的断言 ——
  它把「模型输入」与「模型应生成的目标输出」混为一谈；
- **改为**正确比较：运行时第 2 轮输入 == 训练样本去掉末尾 `create_ppt` function call
  后的前缀；训练样本末尾 == 运行时模型返回的 `create_ppt` function call；
- 新增 tool-terminal 结构红灯（见 §7）；
- **最终 `backend/tests/agent_platform` 与 compiler 全套必须零失败**，
  **不用 xfail / skip / 过滤器掩盖**。

---

## 7. 红灯清单（各一条）

| # | 断言 | 落点 |
|---|---|---|
| 1 | `create_ppt` 样本末尾是 `function_call(create_ppt)` | `test_tool_terminal.py` |
| 2 | 末尾之后**没有** `create_ppt` observation | 同上 |
| 3 | **没有**末轮文本 assistant | 同上 |
| 4 | `create_ppt` 工具**仍被重放一次**，参数 / `status` 校验没被绕过 | 同上 |
| 5 | tool-terminal 桶缺少末尾终态 step → 明确拒绝 | 同上 |
| 6 | 终态工具出现在**非最后一步** → 明确拒绝 | 同上 |
| 7 | 非 tool-terminal 桶含终态工具 → 明确拒绝 | 同上 |
| 8 | 普通工具桶仍保持 `call → obs → assistant`，**无回归** | 同上 |
| 9 | 候选生成器对 `create_ppt` 的 **draft spy 调用数为 0** | `test_generate_candidates_cli.py` |
| 10 | `create_ppt` 无 target 仍可编译；普通桶无 target 仍按 `target_missing` 隔离 | `test_tool_terminal.py` |

---

## 8. 对既有 527 条与正式种子的影响

**本轮之前的审计轮次影响 = 0。** 本裁决**落地后**的预期影响：

| 面 | 影响 |
|---|---|
| compiler 527 条 | 需改 **4 处既有断言**（`test_admission.py:307`、`test_full_sft.py:202/240`、`test_manifest.py:160/163`、`test_pipeline_fixture.py:608`）—— 它们断言的是被删除的 `has_final_assistant`；**判据不变，只换字段名**。另需改 **1 处夹具**（`test_compile.py:132` 用了 `bucket="create_ppt"` 但末步不是终态工具）。 |
| 正式种子 | **一条都不改。** `seed-create-ppt-001` 结构已合规：`steps=[search_regulations, create_ppt]`，末步即终态工具。 |
| `targets.jsonl` | 目前**尚未晋升任何 target**，无孤儿问题。 |
| 运行时 / 后端 | **一行不改。** |

---

## 9. 本轮做了什么 / 没做什么

- **只读**：核对运行时代码 9 处、编译器 3 处、SSE 接入 2 处、种子 1 份、既有测试 5 份。
- **新增/修改**：本文件；`backend/tests/agent_platform/test_runtime_ppt_vs_full_sft.py`
  （订正比较口径、去掉故意红）；compiler 侧的 tool-terminal 红灯与实现。
- **未做**：未连服务器；未调真实 LLM；未生成候选；未 promote / compile 正式产物；
  未创建正式 `targets.jsonl` / `full_sft.jsonl`；未训练；未改运行时代码。