# GUET Agent 单轨重构设计

**日期：** 2026-10-13  
**状态：** 待用户审核  
**目标：** 直接重构现有项目，建立唯一的 LangGraph Agent 主链路、独立工具能力包、可诊断 RAG、可审计数据工厂，以及从 `base-qwen` 重新训练的 `guet-agent` LoRA。

## 1. 背景与决策

当前项目已经具备制度检索、培养方案查询、跨专业比较、空教室指引和 PPT 草稿生成能力，但 Agent、工具、RAG、PPT、训练数据与测试脚本分散在多个目录，接口和消息形状相互耦合。继续在现有布局上增量增加功能，会进一步放大维护成本和训练噪声。

本轮确认以下决策：

1. 不保留新旧两套 Agent，不设置兼容开关，直接把代码移动到最终位置。
2. 所有 Agent 生产代码统一放入 `backend/agent/`。
3. 每个工具是独立能力包，位于 `backend/agent/tools/<tool_name>/`。
4. 每个工具以 `@tool` 修饰的 `tool.py` 作为唯一公开入口；辅助实现放在该工具的 `scripts/`。
5. 多工具共享的契约、证据台账、状态、错误语义和调用日志放在 `backend/agent/core/`。
6. 不做动态插件扫描；工具由 Registry 静态、显式注册。
7. 主 Agent 使用新的 `guet-agent` LoRA；该 LoRA 从 `base-qwen` 重新训练，不叠加当前 LoRA。
8. 新 `guet-agent` 同时负责主图决策、PPT 规划、结构和逐页撰稿；`base-qwen` 只作为训练起点与诊断对照。
9. `create_ppt` 由主 Agent 自主选择，不再依赖关键词规则路由。
10. Easy Dataset 只生成候选问答和改写，不直接产生最终训练集。
11. 不重跑旧系统基线，不以旧模型或旧指标作为新系统的上线门槛。

## 2. 范围与非目标

### 2.1 第一版能力范围

第一版只迁移并验收五类能力：

- `search_regulations`：校规与制度检索。
- `query_course`：单专业课程定位卡。
- `compare_course`：跨专业课程比较。
- `query_empty_classroom`：维持教务系统指引卡，不接入真实账号登录。
- `create_ppt`：进入 PPT 子图，连续取证并生成可审阅草稿。

### 2.2 非目标

- 不新增邮件、日程、教师名单等工具。
- 不在本轮实现真实空教室登录和验证码自动化。
- 不拆分微服务，不增加新的部署单元。
- 不使用动态工具目录扫描。
- 不训练或展示 `Thought:` 形式的隐藏推理文本。
- 不让 Easy Dataset 直接决定工具、参数、证据或 ReAct 轨迹。
- 不默认使用 HyDE 生成检索事实。
- 不以旧 `guet-g1` 的成绩约束新架构。

## 3. 目标目录

```text
backend/
├─ agent/
│  ├─ core/
│  │  ├─ contracts.py
│  │  ├─ evidence.py
│  │  ├─ errors.py
│  │  ├─ observation.py
│  │  └─ telemetry.py
│  ├─ runtime/
│  │  ├─ graph.py
│  │  ├─ state.py
│  │  ├─ model.py
│  │  ├─ registry.py
│  │  └─ budgets.py
│  ├─ tools/
│  │  ├─ search_regulations/
│  │  │  ├─ tool.py
│  │  │  ├─ schemas.py
│  │  │  ├─ renderer.py
│  │  │  ├─ scripts/
│  │  │  │  ├─ normalize.py
│  │  │  │  ├─ aliases.py
│  │  │  │  ├─ query_rewrite.py
│  │  │  │  ├─ embedding.py
│  │  │  │  ├─ bm25.py
│  │  │  │  ├─ fusion.py
│  │  │  │  ├─ dedupe.py
│  │  │  │  ├─ rerank.py
│  │  │  │  ├─ gate.py
│  │  │  │  ├─ chunking.py
│  │  │  │  ├─ indexing.py
│  │  │  │  └─ trace.py
│  │  │  └─ tests/
│  │  ├─ query_course/
│  │  ├─ compare_course/
│  │  ├─ query_empty_classroom/
│  │  └─ create_ppt/
│  └─ prompts/
│     ├─ agent_system.py
│     ├─ ppt_planner.py
│     └─ ppt_author.py
├─ api/
│  ├─ chat.py
│  └─ ppt.py
└─ app.py

data_pipeline/
├─ sources/
├─ easydataset/
├─ scenarios/
├─ compiler/
├─ audits/
├─ manifests/
└─ tests/
```

目录约束：

- 工具外部只能调用该工具的 `tool.py`，不能导入其 `scripts/`。
- 一个工具不得直接导入另一个工具的内部脚本；工具组合通过 Registry 或对方公开入口完成。
- `compare_course` 通过公开课程查询接口组合多个专业结果，不复制课程数据库访问逻辑。
- `create_ppt` 通过 Registry 调用其他工具，不读取其内部文件。
- `scripts/` 在本项目中表示工具内部实现模块，不局限于命令行脚本。
- 每个被迁移的旧实现必须在生产引用切换并通过测试后删除，不保留第二份生产代码。

## 4. 公共契约

### 4.1 ToolResult

所有工具返回统一的结构化结果：

```python
class ToolResult:
    tool: str
    ok: bool
    status: Literal[
        "success",
        "no_evidence",
        "partial_evidence",
        "irrelevant_result",
        "tool_failed",
    ]
    data: dict
    sources: list[Source]
    error: ToolError | None
```

状态语义不得混用：

- `success`：调用成功且证据足以支持本次结果。
- `no_evidence`：调用成功，但没有获得相关证据。
- `partial_evidence`：只支持问题的一部分。
- `irrelevant_result`：有返回，但内容与问题不相关。
- `tool_failed`：参数、工具注册、超时、鉴权、网络或解析等调用/执行失败。

`tool_failed` 不得渲染成“未检索到依据”，`no_evidence` 不得渲染成“学校没有规定”。

### 4.2 Evidence

Evidence 只能从 `ToolResult.sources` 构造，禁止从 observation 文本反解析。稳定身份优先级为：

1. `chunk_id`；
2. 原文 `text_hash`；
3. `title + line + heading_path`。

Evidence 在主图和子图之间共享，同一任务只能有一本追加式台账。

### 4.3 Observation

`agent/core/observation.py` 是唯一渲染入口。它根据工具名分派到工具自身的纯渲染函数。训练与运行时必须调用同一入口：

```text
ToolResult → ObservationRenderer → observation / ToolMessage.content
```

工具渲染函数不得执行检索、访问数据库或修改状态。

## 5. 统一 Agent 主图

所有请求进入同一 LangGraph 主图：

```text
START
  ↓
agent_decide
  ├─ final_answer → END
  ├─ clarify → 等待用户
  └─ tool_calls
         ↓
     validate_calls
         ↓
     execute_tools
         ↓
     append_evidence
         ↓
     agent_decide
```

### 5.1 状态

```python
class AgentState(TypedDict):
    messages: list
    tool_calls: list
    tool_results: list
    evidence: dict
    steps_used: int
    tool_calls_used: int
    pending_clarification: dict | None
    task_id: str
    conversation_id: int
    final_answer: str | None
```

### 5.2 单轮与 ReAct

不建立两套运行入口。简单请求自然在一次工具调用后结束；复杂请求允许模型读取 observation 后继续调用工具。

运行和训练只保存可观察轨迹：

```text
user
assistant(tool_calls)
tool(observation)
assistant(tool_calls or final)
```

不保存可见思维链。

### 5.3 预算与停止

首版默认：

- 普通任务最多 4 个 Agent 步骤。
- 最多执行 6 次工具调用。
- 相同工具与相同参数不得重复执行。
- 连续两次工具调用没有新增 Evidence 时停止研究。
- 参数校验失败不得用原参数重试。
- 网络、超时等可恢复故障最多重试一次。
- 达到预算时，只返回已有证据支持的内容和明确缺口。
- `create_ppt` 启动后由 PPT 子图接管，主图不与子图交叉循环。

这些数值属于运行配置，不写入训练答案；专项验收后可以调整。

### 5.4 统一模型与回退条件

生产默认只使用一个语言模型 `guet-agent`，承担：

- 主图决策、工具选择、参数生成与追问；
- observation 后继续调用或停止；
- 基于 Evidence 的短答案；
- PPT 研究计划、结构组织与逐页撰稿。

`base-qwen` 只作为 `guet-agent` 的训练起点和能力诊断对照，不是默认生产车道。LangGraph 继续负责状态、预算、循环、失败恢复和停止控制；工具负责真实执行；Evidence ledger 是事实来源唯一真相。

只有在混合训练经过数据比例调整后，仍能稳定复现以下任一任务干扰，才允许把 PPT 回退给 `base-qwen`：

- Function Calling 达标，但 PPT 规划或撰稿持续不达标；
- PPT 达标，但工具选择、参数或停止能力显著退化；
- 普通问答错误输出 PPT 结构，或 PPT 请求稳定退化成短问答；
- 7B 模型容量经受控实验确认无法同时满足两类验收门槛。

回退必须有同一测试集、同一工具与同一采样条件下的 A/B 证据，不能凭单个案例决定。

## 6. 工具体系

每个工具包至少包含：

```text
tool.py
schemas.py
renderer.py
scripts/
tests/
training_scenarios/
evaluation_cases/
README.md
```

`tool.py` 使用 `langchain_core.tools.@tool`，参数由 Pydantic Schema 验证。Registry 显式导入五个工具，启动时完成名称唯一性和 Schema 自检。

### 6.1 create_ppt

`create_ppt` 是主 Agent 可自主选择的工具。输入至少包含：

- `goal`；
- `audience`；
- 可选页数、风格和其他约束。

缺少会改变内容方向的参数时，主 Agent先追问，不启动子图。执行后进入 PPT 子图：

```text
intake → plan → research ⇄ evidence_check → structure → author → render → review
```

子图通过 Registry 调用其他工具。第一版输出仍是可审阅草稿，必须由用户确认后才能取得下载凭证；系统不得宣称自动证明了全部语义正确性。

内容页通常包含 2～4 条有信息量的事实主张。研究问题不能直接作为正文答案。每条可核验主张必须绑定 Evidence 或明确 gap。

## 7. RAG 设计

RAG 是 `search_regulations` 工具的内部能力，不暴露为全局散落模块。

### 7.1 流程

```text
原始问句
  ↓
实体与别名归一化
  ↓
原问句 + 规范化问句双路查询
  ↓
Dense + BM25 宽召回
  ↓
RRF 融合
  ↓
逐字去重 + 近重复族折叠
  ↓
Reranker
  ↓
门控
  ↓
邻块/流程块补全
  ↓
ToolResult + Sources + RetrievalTrace
```

只有两路查询均低质量时，才允许模型产生最多两条查询改写。改写必须保留专业名、课程名、文号等实体，只用于检索，不能成为证据。

### 7.2 文档处理

- 规章制度按章、条、款和标题路径切分。
- 流程文档保留步骤连续性，支持邻块补全。
- 培养方案正文按章节切分。
- 培养方案表格进入结构化课程数据库，不依赖普通 RAG。
- 先修后修关系图专门抽取为课程关系边；失败时标记不可用，OCR 堆叠文字不得入库。
- 双栏 PDF 根据坐标恢复阅读顺序后再生成 Markdown。
- 页眉、页脚、印发机关、印发日期等版式噪声在解析层删除。
- 每个块保留稳定 `document_id`、`chunk_idx` 和 `chunk_id`。

### 7.3 门控

门控输出 `supported / ambiguous / irrelevant / empty`。固定分数低于阈值不再直接等价于“语料没有答案”。只有确定性不存在的点名文档或宽召回池为空时，才能直接判为无证据。

更换 embedding 或 reranker 后必须重新标定阈值，旧阈值不得沿用。

### 7.4 RetrievalTrace

每次检索记录原始查询、规范化查询、dense候选、BM25候选、融合候选、精排候选、最终块、门控原因和各阶段延迟。Trace 用于诊断，不整体暴露给模型。

### 7.5 检索模型训练条件

- 正解不在候选池，查询改写仍无效：考虑训练 bi-encoder。
- 正解在候选池但排序低：训练 reranker。
- 正解已排高但被门控挡：只改门控。
- 原文没有：补语料。
- 分块破坏答案：修解析或切片。

培养方案近重复块不能被随机标成普通负样本。

## 8. 数据工厂

### 8.1 中间场景

项目使用与训练框架无关的场景 JSON 作为唯一数据真相，至少记录：

- 场景 ID、意图、风险等级；
- 用户轮次；
- 期望行为；
- 工具与参数；
- 证据要求和禁止断言；
- 来源与人工审核状态。

LLaMA-Factory 的 `messages + tools` 只是编译产物。

### 8.2 Easy Dataset 边界

Easy Dataset 可以生成候选问答、口语/正式/教师/学生问法、追问、多轮候选和易混淆反例。它不得决定工具名、参数、observation、来源、事实答案、ReAct 顺序或无证据结论。

### 8.3 编译流程

```text
语料
→ Easy Dataset 候选
→ 场景规范化
→ 工具轨迹设计
→ 真实工具重放
→ Evidence 核验
→ 单轮/ReAct轨迹编译
→ 去重与泄漏检查
→ 分层人工审核
→ 训练/验证数据
```

训练 observation 必须逐字等于当前工具真实调用后的唯一渲染结果。

### 8.4 风险审核

- ReAct、部分证据、无结果、无关结果和工具失败：100% 人工核验。
- 普通单工具成功：机械全检，按工具、问法和来源抽检20%。
- no_tool、澄清、越界：规则全检，分层抽检20%。
- 最终测试集：100%人工定题、标注和复核。

机械检查失败的样本进入隔离区，不自动改写成看似合理的答案。

### 8.5 首轮数据

先编译300条试验数据，验证 Schema、工具重放、训练加载和短程过拟合；再扩展到约800条做工具能力与 PPT 能力的任务干扰探针；两类能力同时通过后，才扩展到约2,000～3,000条高质量轨迹。建议初始比例：

- 单工具25%；
- 并行工具7%；
- 多步ReAct 18%；
- 缺参追问10%；
- no_tool 8%；
- no/partial/irrelevant evidence 10%；
- tool_failed、参数修复与停止控制7%；
- PPT研究规划7%；
- Evidence到逐页PPT内容8%。

比例可根据独立验证集故障簇调整，不能为了凑比例复制模板。

## 9. 测试与验收

### 9.1 测试集

新测试集包含：

- Function Calling：调用决策、工具、参数、追问和no_tool。
- ReAct：依赖前序结果的多步任务、停止和失败恢复。
- Retrieval：目标文档、目标块、排序和门控。
- Answer Quality：证据一致性和表达质量。
- PPT：自主入口、连续取证、主张证据、渲染和人工确认。

测试集按文档族、意图和问法模板与训练集隔离，不随机按行切分，也不由训练数据生成器直接产生。

### 9.2 Agent 门槛

- 是否调用工具≥90%。
- 工具选择≥90%。
- 参数实体正确≥90%。
- 缺参追问≥85%。
- no_tool≥95%。
- 不必要调用率≤10%。
- 四种证据/失败状态区分≥95%。
- ReAct任务成功率≥80%。
- 必需工具覆盖率≥90%。
- 停止准确率≥90%。
- 重复同参调用率0%。
- 超预算失控0%。
- 测试集内无证据事实断言0条。

### 9.3 RAG 门槛

- Candidate Recall@120≥98%。
- Rerank Recall@5≥95%。
- 门控误拒率≤3%。
- 明确越界题误放率≤5%。
- 所有 `hits=0` 均可从 RetrievalTrace 定位故障层。

这些门槛针对新人工金标准集。若初次基线差距过大，应报告故障簇并修系统，不得通过修改测试集降低门槛。

### 9.4 PPT 门槛

- 主 Agent 能自主选择 `create_ppt`。
- 子图至少能完成两个不同工具的连续取证任务。
- 每条事实主张绑定Evidence或gap。
- 内容页通常有2～4条有效主张。
- 研究问题不直接充当答案。
- 页数符合请求或明确说明偏差。
- 机械QA通过。
- 用户确认前不签发下载凭证。

## 10. 可观测性与安全

每次请求记录 `request_id`、会话、模型、步骤、工具、参数摘要、工具状态、RetrievalTrace ID、Evidence ID、停止原因、延迟和token用量。

日志不得记录账号密码、Cookie、完整鉴权头或其他敏感凭据。工具参数日志需要按Schema标记敏感字段并脱敏。

## 11. 实施阶段

### 阶段1：目录与公共契约直接重构

只做迁移所需静态盘点，不重跑旧系统基线。建立最终目录、迁移公共契约、切换唯一生产入口并删除被替代实现。

### 阶段2：五个工具直接迁移

按 `query_course → compare_course → search_regulations → query_empty_classroom → create_ppt` 顺序逐一迁移。每个工具完成金标准、`@tool`入口、内部脚本、测试、生产引用切换和旧实现删除。

### 阶段3：统一 LangGraph 主图

实现统一循环、预算、停止、失败恢复、Evidence 台账以及自主 `create_ppt`。

### 阶段4：建立检索诊断体系

创建人工检索金标准和 RetrievalTrace，形成池外、排序、门控、分块与语料覆盖的故障报告。

### 阶段5：定向改善 RAG

按阶段4证据依次处理归一化、查询融合、去重、分块、门控；只有故障层明确后才训练reranker或embedding。

### 阶段6：建立数据工厂

完成Easy Dataset导入、场景Schema、真实工具重放、证据核验和训练格式导出，先交付300条试验集。

### 阶段7：重构测试集

交付Function Calling、ReAct、Retrieval、回答质量和PPT五类独立测试集，完成泄漏检查。

### 阶段8：训练 guet-agent

从 `base-qwen` 训练统一的 `guet-agent`。依次运行300条格式/过拟合探针、约800条任务干扰探针，再扩展完整数据；首轮只做SFT，不使用DPO。Function Calling、ReAct、普通回答、PPT规划和PPT撰稿必须由同一个 `guet-agent` 接受验收。`base-qwen` 只作诊断对照；只有满足§5.4的证据条件，才允许提出双车道回退方案。

### 阶段9：端到端验收与部署

覆盖普通问答、多轮、无证据、工具失败、ReAct、PPT、下载确认、超时恢复、权限和路径安全。验收后唯一主链路上线。

### 阶段10：固化工具扩展模板

把工具目录、测试、训练场景、评测用例和README固化为脚手架。后续新增工具先实现契约和测试，再补数据，不允许恢复散落注册方式。

## 12. 完成定义

项目重整完成必须同时满足：

1. Agent生产代码和五个工具均位于最终目录，旧实现无生产引用。
2. 所有请求经过唯一LangGraph主图。
3. 单轮Function Calling和受控ReAct通过独立验收。
4. RAG每个失败可以定位到具体层。
5. Easy Dataset输出不能绕过真实工具重放和证据审核。
6. 新测试集与训练集无泄漏。
7. 同一个新 `guet-agent` 同时达到 Function Calling、ReAct、回答质量与 PPT 内容门槛；若提出双车道，必须满足§5.4的回退条件。
8. PPT能自主启动、连续取证、生成内容充分的可审阅草稿，并在用户确认后下载。
9. 新工具可以按统一模板增加，不再修改多个散落注册点。

