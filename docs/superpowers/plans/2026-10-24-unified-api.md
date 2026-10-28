# Unified API Generation Implementation Plan

**Goal:** 规划、证据成文、标题、相关性评估与既有PPT生成统一由API配置驱动，后续通过配置切换模型/平台，不改变工具、计算、证据校验和三层漏斗职责。

**Architecture:** 一个无密钥JSON配置（deploy/llm-api.json），一个配置加载器和一个支持Responses/Chat Completions的传输适配器。llm_client保留现有调用接口，默认使用统一配置；显式将LLM_API_CONFIG_PATH置空才进入历史兼容通道，不在API失败时自动退回Qwen。writer提示/协议与提供方解耦，未修复的业务缺口不混入本任务。

**Authorization:** 用户已明确要求统一API并实施可切换配置，沿用主会话直接执行方式。本轮先本地实现/模拟验证，不部署生产、不发起新平台真实调用。密钥仅引用环境变量或backend/.env，不写配置和日志。

**Files:** 新deploy/llm-api.json、backend/llm_api_config.py、backend/llm_api_client.py、配置说明及针对性测试；必要修改config、llm_client、final_answer_profile、runtime_identity、main、PPT生成入口和部署样例。兼容不同平台指上述两种协议，非这两种协议不能仅换URL保证支持。

## Review Focus

- 只切最终成文却遗留vLLM：逐项覆盖规划非流式/流式、标题、评估、PPT生成；摘要提供者无现成网络调用，不增加新功能。
- 平台只改URL却漏协议/工具绑定：显式protocol，测试工具调用ID、arguments、工具结果往返和流式分片。
- provider-specific参数污染其他平台：配置显式parameters，不继承Qwen top_k/repetition_penalty；协议层映射输出上限和结构化格式，保留失败/截断处理。
- API失败静默换模型/正文无校验：无自动平台降级，renderer与真实计算/枚举/来源仍保留；配置错误/缺密钥在启动前可诊断。
- 切API后仍依赖9B就绪：跳过本地生成模型身份探测，保留知识库/数据库/BM25检查，日志明确有效profile/model；Docker挂载同一配置并传入密钥环境。

### Task 1: 唯一配置与协议适配

- [x] 定义active_profile/profiles/tasks，支持url、model、protocol、api_key_env、超时、采样与输出上限、结构化输出模式，拒绝覆盖消息/工具/模型等结构字段。
- [x] 实现同步/异步及流式调用；Responses工具消息转换、Chat工具分片、JSON Schema/JSON对象模式；不完整响应不当成功，日志不回显密钥或错误正文。
- [x] 两种协议模拟成功/失败、工具往返、缺密钥、平台切换、内容不变和stream中断测试。

### Task 2: 完整接入与可运行配置

- [x] 所有现有生成入口共用active profile；writer模板选择独立于供应商/模型。
- [x] 生成API启动验证与身份日志，旧vLLM检查仅历史兼容通道使用；知识库校验不绕过。
- [x] 更新.env示例、Docker配置挂载和使用说明，写明重启生效、密钥设置、两种协议切换和兼容能力边界。

### Task 3: 本地收口

- [x] 默认API路径的正常主图/工具/成文及辅助入口模拟验证；历史路径显式禁用API配置后跑受影响回归。
- [x] 保存测试/差异和结果说明，明确没有新真实语义验收，不把统一传输视为修复new-15/new-17/new-19/new-20。

本地收口：25项API路径+89项历史兼容回归，共114不同用例通过；两协议均走真实主图/本地工具/模拟API/SSE/SQLite。无新服务器或真实生成，无生产部署。[实现结果](../../../_work/phase8/unified-api-20261024/实现结果.md)。

### Task 4: 用户明确要求真实API跑一遍

用户要求“用调用api的方式跑一遍测试”，并要求不实时grep、程序结束统一读取。沿用28不同问题+4次复验、2准备共34聊天轮及原人工标准，保留B1隔离关联配置用于与上一候选同范围验证；不新增题目、语义补丁或模型采样策略。

- [x] 新工作目录_work/phase8/unified-api-live-20261024；只改既有验收引导/观察器以适配统一API，不在测试中改产品代码。按任务标记规划、成文、标题，不通过同一URL误认所有请求为成文。
- [x] 冻结当前统一API实现/配置及输入；总API238，planning136、composition68、title34，单轮分别4/2/1；工具204、Qdrant2000、Qdrant启动GET60、应用请求101。失败/修复计入，未知生成用途或端点停止。现有余额、不充值。
- [x] 本地验证调度和角色/总量/单轮上限、故障停止后，在既有autodl的/root/autodl-tmp/answer-stability-api-entry-u1024隔离运行。只启动必要Qdrant和MySQL依赖，不启动Qwen/vLLM。公开文档表复制到随机验收库，禁用真实教务凭据，生产源码/索引不变。
- [x] 发送原题、必要模拟历史、工具schema/工具公开观察、检索片段、冻结规划及成文指令到当前配置官方DeepSeek Responses；不发送真实个人记录、密钥、权重或整库。结果回收到_work/phase8/unified-api-live-20261024/returned。
- [x] 启动后仅等待进程自然结束，不查看中间日志；统一回收hash/请求/响应/草稿/工具/检索/计算/usage/SSE/落库证据，按原标准评审。清理测试服务/独立库/目录，MySQL若为本任务启动且无其他使用则恢复停用，不关闭实例。

本轮不把统一API等同于原业务缺口修好；new-15/17/19/20照原标准验收。停止条件保留来源/配置不一致、API鉴权/余额/HTTP错误、预算、运行故障及持久化不一致；成文后的真实校验失败保留并继续其余题，不能放宽判据凑通过。

真实测试收口：28不同问题、32目标+2准备完成；API126（planning62/composition33/title31），Qwen0，HTTP失败0。29 answer/1 failed/2 needs_input不等于准确率。new-15正确草稿误拒、new-17正确参数被实体绑定拒绝、new-19缺参数未形成任务待办仍阻塞，new-20安全边界保留但多余身份追问。首轮检索根地址GET观察器误停保留，恢复后预算继承；ASGI因新增一次隔离注册累计102。32条压缩规划流记录未能解码usage，217112 tokens仅94条已观测小计。两份归档及内部hash已验证；隔离服务/数据库/目录清理，39项源文件未变，MySQL恢复停用，GPU0；实例未关闭。见[本轮结论](../../../_work/phase8/unified-api-live-20261024/验收结论.md)。
