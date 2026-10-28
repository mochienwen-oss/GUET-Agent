# 阶段 6：查询工程与 Agent 数据工厂设计

**日期：** 2026-10-14  
**状态：** 已获方向授权，待实施计划复核  
**范围：** 6A 查询工程、6B 数据编译、6C 训练前质量门；不含上传、训练、部署。

## 1. 目标与结论

阶段 6 的目标不是继续手工修补一份 JSONL，而是建立一条可重复执行的数据生产线：

```text
自然用户请求
  → 可训练的工具参数与 Agent 轨迹
  → Registry 真实执行
  → 唯一 observation 渲染
  → 证据约束答案
  → 机械质量门 + 少量人工红队
  → 训练候选集
```

模型架构采用**一个逻辑模型**：`base-qwen` 加载一个新的 `guet-agent` LoRA adapter。
LoRA 不是可独立运行的第二个模型；开发和验收阶段不做物理 merge，由 vLLM 动态加载 adapter。
同一个适配后模型负责工具选择、单步调用、多步 ReAct、PPT 规划与撰稿，节点提示词区分角色。
确定性代码负责参数校验、调用预算、停止条件、证据台账和产物校验。

不采用“base 规划 + LoRA 执行”的双决策模型。双模型会重复传递上下文、制造两套语义和评测面，
还会增加显存与延迟；只有后续受控实验明确证明单 adapter 存在不可接受的角色干扰时，
才评估“同一 base 上多个 adapter”，而不是并行部署两个完整模型。

## 2. 阶段边界

### 2.1 6A：查询工程

`search_regulations` 从单一 `query` 升级为：

- `query: str`：完整、语义自足的检索意图，供 embedding 与 reranker 使用；
- `keywords: list[str]`：短、可解释、去重后的领域术语，供 BM25 使用；为空时回退到 `query`。

首版**不增加 `filters`**。当前 Qdrant payload 虽有 `document_id/source/heading_path/block_type`，
但还没有被验收的公共过滤契约。让模型提前生成任意过滤条件，会造成召回被静默截断，
并把尚未实现的能力写进新训练分布。过滤字段必须在独立金标证明“字段稳定、值域明确、误过滤可测”后再加。

检索三路输入必须分开：

```text
embedding_query = query
sparse_query    = keywords.join(" ") if keywords else query
rerank_query    = query
```

原始用户问题始终保留在对话消息中；工具参数是检索计划，不替代用户原话。
不得用答案里的精确文件标题反向构造关键词。只有用户原话明确出现标题/文号，或受控词表包含的领域同义词，
才允许进入 `keywords`。

受控词表放在 `backend/agent/tools/search_regulations/data/query_expansions.json`，只收录经人工确认的
别名、简称和业务术语映射。词表用于数据生成与可选的确定性补全，不允许扩写数值、条款号或事实结论。

6A 必须以阶段 5A 的证据级集合做 A/B，至少守住：

- Candidate Recall@120 = 100%；
- Rerank Recall@5 >= 95%；
- gate false reject <= 3%；
- 14 条负例 false accept = 0；
- 47 条 diagnosis 全部 success。

运行 GPU 诊断时固定 `embedding=cpu`、`reranker=cuda`，避免两模型争抢显存导致进程卡死。

### 2.2 6B：数据编译

最终 JSONL 不是源文件。唯一可维护源是场景规格 `ScenarioSpec`，Easy Dataset 只提供自然问法候选，
不得直接决定工具、参数、observation、答案事实或 ReAct 轨迹。

每条场景至少记录：

- `scenario_id`、能力桶、用户意图、自然问法；
- 预期工具序列与参数；
- 证据状态：`success/no_evidence/partial_evidence/irrelevant_result/tool_failed`；
- 是否需要澄清、终止条件、最大工具步数；
- 生成来源、生成器版本、随机种子、人工复核状态。

编译器必须从 `registry.schemas()` 取得**当前七工具**契约，并把 schema 快照和哈希写入 manifest。
`training_schemas()` 只用于复现/校验 v7 旧四工具数据，不得用于阶段 6 新数据。

所有工具轮由 `registry.call()` 真实执行，再由 `registry.render()` 生成 observation；
不得手写或让 LLM 猜 observation。多步轨迹只保存可观察消息：

```text
system → user → function_call → observation →
function_call/assistant → observation/结束
```

不训练 `Thought:`、隐藏推理或框架内部 `AIMessage/ToolMessage` 对象。训练格式继续使用
LLaMA-Factory 已验证的 `function_call/observation` 角色，运行时由适配层映射到 LangGraph 消息。

能力桶至少包括：

1. `no_tool`：闲聊、主观推荐拒绝、能力边界；
2. `clarify`：缺少会改变工具或参数的关键信息；
3. `single_tool`：七工具各自的正例与易混淆反例；
4. `evidence_state`：五种 ToolResult 状态的不同回应；
5. `react_multi_step`：前一 observation 决定下一次工具或参数；
6. `create_ppt`：主 Agent 自主选择工具，子图连续取证后交付；
7. `stop_and_recover`：重复调用、预算终止、不可重试参数错误与可重试运行故障。

旧 v7 只作为可追溯材料池：可抽取自然问法或经验证事实，但不得整份直接合并。
被采用的旧样本必须重新绑定当前七工具 schema，并重新执行工具得到 observation。

### 2.3 6C：训练前质量门

只有全部硬门通过，才能生成训练候选集并进入下一阶段：

1. **结构门**：JSONL 可解析、列统一、角色序列合法、function_call 紧跟 observation；
2. **契约门**：tools 列逐字等于本次 `registry.schemas()` 快照；参数通过 Pydantic 校验；
3. **重放门**：可重放工具的 observation 与当前 Registry 逐字相同；
4. **证据门**：答案事实由 observation 支撑；无证据、部分证据、无关结果、工具失败不得混义；
5. **轨迹门**：每一步推动任务，禁止相同参数重复调用；终止不超过预算；
6. **检索参数门**：`query` 语义自足，`keywords` 无答案泄漏、无不可学文件标题、无事实扩写；
7. **去重门**：问句、轨迹和答案模板分别检测，重复族按来源簇切分；
8. **泄漏门**：训练集不得与独立评测集逐字或高相似重合；
9. **人工门**：只审核机械规则不能裁决的红队队列和分层抽样，不要求逐句人工读全量。

独立评测资产分开建设，不从训练输出反推答案：

- `fc_gold`：工具选择与参数；
- `react_gold`：多步工具轨迹与停止；
- `grounding_gold`：全证据/部分证据/无证据/无关/失败；
- `ppt_gold`：自主选择 `create_ppt`、持续检索与产物证据；
- `course_gold`：培养方案字段，权威来源是用户已清洗的 `_md`；有歧义时列出具体 md 交用户裁决，
  不由 Agent 自行对照 PDF。

## 3. 目录与职责

```text
backend/agent/tools/search_regulations/
├─ schemas.py
├─ tool.py
├─ data/query_expansions.json
├─ scripts/query_plan.py
└─ tests/test_query_plan.py

训练集/
├─ scenarios/
│  ├─ no_tool.jsonl
│  ├─ clarify.jsonl
│  ├─ single_tool.jsonl
│  ├─ evidence_state.jsonl
│  ├─ react_multi_step.jsonl
│  └─ create_ppt.jsonl
├─ compiler/
│  ├─ models.py
│  ├─ replay.py
│  ├─ compile.py
│  ├─ validate.py
│  └─ tests/
└─ _work/phase6/
   ├─ candidates/
   ├─ manifests/
   ├─ audits/
   └─ quarantine/

测试集/
├─ fc_gold.jsonl
├─ react_gold.jsonl
├─ grounding_gold.jsonl
├─ ppt_gold.jsonl
└─ course_gold.jsonl
```

编译器属于训练数据工程，不放进 `backend/agent/`；生产 Agent 不依赖训练目录。

## 4. 成功标准与停止条件

阶段 6 完成时，应交付可从场景规格确定性重建的候选集、完整 manifest、隔离队列和五类独立评测集；
不要求已经完成训练。

以下任一情况立即停在对应阶段，不用“先生成再说”绕过：

- 6A 使阶段 5A 任一硬指标回退；
- 运行时 schema 与数据 tools 列不一致；
- observation 无法由当前 Registry 重放；
- 评测答案来自训练候选或机器抽取后冒充人工金标；
- 多步轨迹包含不可观察思维链或无进展重复调用；
- GPU 诊断未按 `embedding=cpu/reranker=cuda` 执行；
- 需要服务器写入、上传、训练或部署但未获得用户明确授权。

## 5. 非目标

- 阶段 6 不训练、不上传、不部署、不切生产集合；
- 不物理 merge base 与 LoRA；
- 不引入第二个决策模型；
- 不为尚未验证的 Qdrant 元数据设计通用 filters；
- 不用 Easy Dataset 自动裁决事实正确性；
- 不追求一次生成大规模数据，先让每个能力桶可重放、可测、可隔离。
