# Campus Agent v1 运行时契约与数据衔接设计

日期：2026-10-18  
状态：待最终审阅  
范围：方案 A——会话内多轮、上下文压缩、动态工具、最小 Skill、证据约束回答

## 1. 目标

在不推翻现有语料、索引、实体目录、七工具、单轮问题池和资格链的前提下，补齐一个可稳定训练和扩展的 Campus Agent v1。

第一期必须支持：

- 同一会话内任意轮数的任务承接；
- 用户补参、纠错、条件变更和未完成任务恢复；
- 按 token 预算管理历史，而不是写死两轮、三轮或最近六条；
- 结构化可信上下文、较早历史摘要和最近完整消息三层上下文；
- Registry 动态工具 schema；
- 最小 Skill manifest 和工具白名单；
- 单轮与多轮数据分别验收、统一编译和混合训练；
- 所有事实和工具参数继续受 catalog、replay、evidence binding 与人工审核约束。

第一期不实现：

- 自动跨会话长期记忆；
- 多用户共享记忆；
- 企业 RBAC 和通用审批平台；
- 任意外部写操作；
- 通用插件市场或多 Agent 编排；
- 自动将用户隐私、偏好写入长期记忆。

只预留空的 MemoryProvider 接口，不启用自动跨会话读取或写入。

## 2. 保留资产

以下资产直接沿用，不重建、不重新生成：

- 最新 91 份 Markdown、`school_docs_phase5`、BM25、embedding/reranker；
- entity catalog、alias/family sidecar、resolver 和实体调用校验；
- 七工具 Registry、ToolResult 状态、renderer、PPT 子图；
- replay、evidence ledger、binding、qualification、promote、compile、quarantine；
- 已清理的 1,158 条单轮场景及其 replay identities；
- 14 条多轮资格草稿；
- 160 组、320 条多轮追问身份清单；
- EasyDataset 的 user 消息和会话结构。

EasyDataset assistant、未审核 Teacher 输出、失败工具原始参数、LLM 猜测均不得成为可信事实来源。

## 3. 关键决策

### 3.1 Teacher 生成门与正式入库门分离

`batch_role=training` 时：

- pending 场景可以生成 pending candidate；
- reviewed 场景可以生成 pending candidate；
- rejected 场景不得生成 candidate。

`batch_role=qualification` 时仍只允许冻结且 reviewed 的资格包。

promote 必须同时满足：

1. scenario 为 reviewed；
2. candidate 为 approved；
3. `reviewed_by` 非空；
4. scenario、candidate、replay/evidence binding 一致；
5. 未命中 qualification/eval exclusion。

pending candidate 永远不能写入正式 targets。

### 3.2 工具参数由模型完整输出

前序上下文只用于验证模型已经给出的参数是否可信，不替模型补齐缺失参数。缺少必填参数时应澄清；不得将校验层拒绝伪装为 ToolResult。

### 3.3 摘要没有事实权限

自然语言摘要仅帮助模型理解较早对话主题。实体、数字、制度事实和工具结论必须来自结构化可信状态或真实 observation，并带可复现身份。

### 3.4 工具与 Skill 不写死在系统提示词

- 系统提示词：身份、行为、安全、证据和回答格式等稳定规则；
- Registry：当前请求可用的工具 schema；
- Skill：按需加载的工作流说明、工具白名单和副作用策略。

新增普通只读工具不应要求重训；只有出现稳定的决策缺陷时才补训练数据。

## 4. ConversationContext v1

运行时、replay 和 compiler 共用同一语义模型：

```json
{
  "conversation_id": "...",
  "context_version": "conversation-context/1",
  "revision": 1,
  "prior_context": {
    "entities": [],
    "facts": [],
    "unresolved": []
  },
  "pending_task": null,
  "user_corrections": [],
  "compressed_history": null,
  "recent_messages": [],
  "skill_context": null
}
```

### 4.1 结构化可信状态

可信实体来源仅限：

- `user_explicit`；
- `resolver_selected`；
- `tool_canonical_args`；
- `human_correction`。

可信事实来源仅限：

- `tool_observation`；
- `human_correction`。

工具来源实体和所有事实必须带 evidence identity。用户纠正后旧实体应变为 inactive；多个同类型 active 实体无法唯一确定时必须澄清。

### 4.2 待完成任务

`pending_task` 用于跨用户轮次恢复，至少记录：

- 原任务类型；
- 缺失字段；
- 未解决 mention；
- 允许继续使用的已确认实体；
- 创建轮次和最后更新轮次。

它不得存放未经证据确认的业务事实。

### 4.3 最近完整消息

按 token 预算保留最近完整轮次，不使用固定条数。必须保持工具调用与对应 observation 成对，不得从中间截断一条工具轨迹。

### 4.4 较早历史摘要

```json
{
  "summary": "...",
  "covered_turns": [1, 8],
  "source_hash": "...",
  "summary_sha256": "...",
  "summary_version": "conversation-summary/1"
}
```

摘要是可重建派生产物，不覆盖原始消息。摘要错误不得改变结构化实体、事实、工具权限或系统规则。

## 5. 上下文压缩

当 system、Skill、结构化状态、历史和当前问题的预计 token 总量超过预算时：

1. 永久保留系统规则、结构化状态、pending task 和最近完整轮次；
2. 将更早的普通对话交给独立 `ConversationSummaryProvider`；
3. 保存摘要版本、覆盖轮次和来源哈希；
4. 更新 conversation binding；
5. 原始消息继续保留在数据库中供审计和重建。

摘要提供者可以使用同一模型的独立摘要调用，但主 Agent 不在回答过程中同时负责更新摘要。摘要失败时退回最近完整窗口和结构化状态，不得丢失可信状态。

摘要中的 prompt injection、工具指令、数字和实体不会自动获得权限。

## 6. 运行时数据流

```text
current user message
  -> load ConversationContext
  -> select Skill
  -> render stable system + Skill + context + recent messages
  -> model emits complete tool arguments
  -> resolver + validate_call(current query, prior context, Skill allowlist)
  -> execute / clarify / answer
  -> append evidence
  -> derive trusted entities, facts and unresolved mentions
  -> update pending task and corrections
  -> compact older history when token budget requires
  -> persist new context revision
```

同一次请求内可确定性修复的参数错误可以有限重试。缺少用户信息、一对多歧义、用户纠正和显式稍后重试使用跨用户轮次恢复。高风险或有副作用的工具不得盲目自动重试。

## 7. 持久化

一期在 Conversation 对应记录中保存 versioned context JSON 和 revision；Message 继续保存原始 user/assistant 文本。实现前应按现有数据库迁移方式做最小兼容迁移，不引入独立长期记忆数据库。

写入使用 revision 防止同一会话并发请求互相覆盖。context 损坏或版本未知时失败关闭，不静默降级为无上下文。

## 8. 最小 Skill v1

```json
{
  "skill_id": "campus_information_query",
  "version": "1",
  "description": "...",
  "instructions": "...",
  "allowed_tools": [],
  "required_inputs": [],
  "side_effect_level": "none",
  "approval_policy": "none"
}
```

第一期仅注册：

1. `campus_information_query`：允许六个查询工具；
2. `create_presentation`：允许 `search_regulations` 和 `create_ppt`，使用既有 PPT 副作用/澄清策略。

Skill 不能调用 allowlist 外工具。Skill ID、版本、允许工具和实际工具调用必须进入审计记录。

## 9. 数据衔接

### 9.1 单轮

保留 1,158 条问题。预计 1,111 条生成 answer-bearing candidate，47 条 tool-terminal 不调用 Teacher。所有 candidate 初始为 pending。

### 9.2 多轮

保留当前 14 条资格草稿并补 4–6 条长上下文/压缩/Skill 安全题，总数约 18–22 条。资格包通过真实 replay 和人工审核后冻结。

160 组身份清单随后物化为约 250–350 个当前轮 target：

- 每条只预测当前 assistant；
- 历史 assistant 只能来自 approved target；
- 当前 observation 必须真实 replay；
- 数字前提必须有 evidence identity；
- 短对话的 compressed history 可以为空；
- 少量 4–12 轮样本验证压缩和纠错。

### 9.3 首版训练比例

目标约为：

- 单轮 answer-bearing：1,000–1,100；
- 多轮 current-turn：250–350；
- tool-terminal：约 47。

正式训练前统一人工审核、promote 和 compile。除非单独要求基线，不先做纯单轮正式训练。

## 10. 实施顺序

1. 拆分 training candidate 生成门和 promote 双审核门；
2. 定义 ConversationContext v1；
3. 接入 chat/runtime 持久化和跨轮恢复；
4. 接入 token 预算与 ConversationSummaryProvider；
5. 注册两个最小 Skill；
6. 收口并冻结多轮资格包；
7. 物化首批多轮场景；
8. 生成单轮与多轮 Teacher candidate；
9. 机械门和人工审核；
10. promote、compile、quarantine/manifest 审核；
11. 单次混合训练和统一验收。

本地可逆工作可以连续推进；服务器、Qdrant、GPU、LLM、同步、正式冻结、正式资产写入和训练仍分别遵守显式授权边界。

## 11. 最小验证策略

默认只做：

1. 改动模块定向测试；
2. 一个真实生产路径集成测试；
3. 受保护资产身份检查；
4. 必要的确定性重建。

关键验收集合：

- Teacher gate：5 条；
- ConversationContext 契约：6 条；
- 真实多轮图：2、4、6 轮及纠错、歧义、伪造证据共 6 条；
- 压缩：6 条定向 + 1 条 8–12 轮集成；
- Skill：4 条；
- 多轮资格：18–22 条集中双轮 replay。

只有修改 ScenarioSpec、replay/binding、promote、compile 或正式训练资产时，才在阶段收口时集中跑一次 compiler 全套。不要为每个小 Task 重跑全套，不人为制造大量红灯，不更新非必要进度文档。

## 12. 停止条件

出现以下任一情况立即停止并请求裁决：

- runtime 与 compiler 对同一上下文得出不同校验结果；
- 摘要被当成无证据事实；
- 用户纠正后旧实体仍 active；
- 多个 prior 实体被静默 top-1；
- Skill 调用 allowlist 外工具；
- 系统替模型补齐必填工具参数；
- EasyDataset assistant 进入事实区；
- pending/rejected 数据可以进入正式 targets；
- replay 漂移或声明状态不符；
- evidence identity 无法复现；
- 正式数据命中资格或 eval 同事实排除集。

## 13. 完成标准

Campus Agent v1 完成必须同时满足：

- 单轮工具调用和证据回答通过；
- 2、4、6、8–12 轮场景通过；
- 用户纠正会使旧实体失效；
- 长历史可压缩且摘要无事实权限；
- pending task 能跨用户轮次恢复；
- alias 一对多继续澄清；
- 工具 schema 动态注入；
- Skill 有工具白名单和审计身份；
- 新增普通只读工具不要求修改系统提示词；
- 单轮和多轮 Teacher 分别通过资格门；
- 只有人工审核后的 targets 能进入 compile；
- quarantine、manifest 和混合训练验收通过。
