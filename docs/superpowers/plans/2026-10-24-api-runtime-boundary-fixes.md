# API运行时已证实缺口修复执行方案

**状态：本地430项去重检查完成后，用户于2026-10-25重新授权服务器测试。冻结39轮真实API验收全部执行，129次请求，结果已回收、隔离环境已清理。四项工程修复及自然补问取得真实证据；无关扩写等内容质量问题仍在，未宣布整体质量全面达标。详见`_work/phase8/api-runtime-fixes-20261025/server-validation/验收结论.md`，未部署生产。**

## 2026-10-25实施时的职责收敛

按随后确认的“释放API模型正常互动、收缩重复语义判断”调整实施：API的同一次规划调用增加request_input对话动作，模型提交自然问题列表，可选提交score_conversion目标；TaskBoundary仍唯一提交用户参数及缺口，模型不能直接写待办或注入成绩。未命中快捷规则的表达可以通过该动作进入补问与已有待办，不另加分类调用。业务工具schema和本地/LoRA通道保持原约束。

普通补问不再要求符合单问句正则。若模型同时提出补问和业务调用，本轮先补问，业务调用留在审计中而不执行，避免因已有输入缺口而查询错误对象。真正学校事实、适用范围和计算结果继续走原证据边界。

主图保护反例新增实际失败证据：当前用户“不是人工智能，改查软件工程”时，旧校验仍允许被否定的人工智能。因此允许在entity_calls增加明确否定来源检查；这属于已复现缺口，不重写绑定器。

原草稿回放、编译图、聊天入口、输入持久化和两种API协议分别留证。只读快照和各轮log/XML位于`_work/phase8/api-runtime-fixes-20261025`；最终判断以该目录的修复证据报告为准，不将过程中的失败重试删除或写成首次全部通过。

**Goal:** 保留统一API与现有检索成文职责，修复真实验收暴露的状态丢失、指标误拒、换算输入缺口及身份范围混读，以原记录回放、反例和一次冻结验收证明收益。

**Architecture:** 复用AgentState、ConversationContext/PriorContext、TaskBoundary、PendingTask、NextAction与既有context_action提交通道。主图入口统一上下文快照，实体校验验证参数来源，任务解释提交目标/参数/缺口，answer_service决定继续执行或交付，context_store通过现有revision提交待办。writer继续只负责有据正文，不决定任务完成与身份事实。

**Tech Stack:** 现有Python、LangGraph、Pydantic、pytest、FastAPI/SQLite本地入口测试；真实验收继续使用隔离MySQL、公开知识库和当前DeepSeek API配置。按原会话逐阶段执行；本计划不要求新建聊天、子代理、服务、依赖或配置平台。

## 当前依据和新增定位

本轮输入：[真实验收结论](../../../_work/phase8/unified-api-live-20261024/验收结论.md)、[关键缺口证据](../../../_work/phase8/unified-api-live-20261024/关键缺口证据.json)。保留原文件及hash，测试夹具仅摘录必要公开数据，记录来源文件/turn_id/请求序号，不把完整服务日志搬进产品测试。

2026-10-24本地进一步定位：run_stream初始化写入prior_context、pending_task，routers/chat也传入二者；AgentState却没有声明这两个字段。最小StateGraph(AgentState)只读复现结果：

| 字段 | State声明 | 节点实际收到 |
| --- | --- | --- |
| prior_context | false | false |
| pending_task | false | false |
| conversation_context | true | true |

提示来自conversation_context，所以模型看到人工智能；工具校验读取state.prior_context，所以拿不到。entity_calls._bind_arg已有“唯一active可信前序实体”绑定分支，不应在尚未修好状态传递前重写该校验器。既有test_runtime_multiturn_context部分测试直接传字典调用验证函数，绕过编译图；新的回归必须经过真实编译主图及聊天持久化路径。

四项问题并不需要同一种处理：new-15是数量和编号类型混淆；new-17有上述具体状态缺失；new-19是目标识别依赖参数齐全、且成功检索可清掉待办；new-20是主体身份与引用材料混读。修复这些已知职责交接，比再建分类器或改writer提示更直接。

## 固定边界与取舍

- 不增加新的意图分类模型、规则服务、独立上下文存储或整套任务框架；在现有接口中修复。
- 不改变API供应商/model/采样、writer输出Schema、知识库原文、人工裁决、检索阈值和B0/B1关联配置；保持此次候选B1的验收范围，生产默认B0不变。
- 不直接交付未经证据处理的规划文本，不用模型自然语言补问代替结构化缺口，不取消来源、未知指标、错实体和真实计算校验。
- CET成绩换算仍限现有CET4/CET6能力。识别到其他考试时说明本次自动计算能力边界或查询相应原文，不能套CET范围；不在此次新增雅思/托福计算公式。
- 每个阶段独立保留修改前失败、修改后通过、保护性反例；失败时在本地收敛，不把后续阶段的偶然输出当作前一阶段修复证据。
- 本地不调用付费API；所有生成与检索网络在回放测试中拦截。语义事实沿用冻结原文，模拟通过不能写成真实模型质量通过。
- 新增持久化字段默认空、旧上下文可读。旧代码extra=forbid不自动兼容新待办字段：回滚必须先清理/转换此次新增换算待办，再退回旧代码；保留实体、消息和其他待办，不清空整段历史。

## 关键决定的唯一负责方

| 决定 | 唯一负责位置 | 其他层的角色 |
| --- | --- | --- |
| 本轮读取哪份会话状态 | graph.run_stream入口，ConversationContext作为存在时的主来源 | 提示、校验、任务解释消费同一快照 |
| 工具参数是否可追溯 | entity_calls.validate_calls | 模型与漏斗提交候选；不得自行执行 |
| 任务目标、用户参数、缺哪些输入 | task.interpret_task生成的TaskBoundary | 计算与成文包读取，不再各自重猜目标 |
| 缺口是否允许成文/应补问 | answer_service.decide_next | 检索success不能覆盖缺口 |
| 是否进行实际运算 | 现有calculation_task与calculate工具 | 只选择已绑定规则、执行有来源计算 |
| 待办是否写入/清除 | context_store.record_turn + expected_revision | 运行时提出context_action，禁止模型直接写状态 |
| 指标编号与来源是否合规 | structured_records及现有正文渲染边界 | writer自由组织有据正文，不承担校验算法 |

## 阶段1：修复编译主图丢失可信状态

**Modify:** backend/agent/runtime/state.py、graph.py。

**Test:** backend/tests/agent_platform/test_runtime_multiturn_context.py、test_unified_api.py、test_funnel_runtime.py；需要独立文件时新增test_compiled_context_state.py。

**接口决定：** run_stream公开参数保持；AgentState增加prior_context、pending_task声明，与现有object | None风格一致。有conversation_context时从它获取规范快照；调用者额外传入的prior/pending必须一致，冲突在模型/工具调用前显式失败。无conversation_context的既有调用继续支持显式prior_context。先复用现有entity_calls，不新增绑定层。

- [ ] 用真实编译主图复现new-17：首轮成功查询人工智能/计算机网络并落库，下一轮“那同一个专业，操作系统课程支撑哪些指标？”；记录API提出正确参数而验证失败的修改前结果。
- [ ] 断言校验节点能收到与提示同一份prior_context；持久化加载→图初始化→节点→工具的实体ID与来源一致。不得以测试手工塞节点字典替代。
- [ ] 补齐schema和入口规范化；实体校验须为allow、narrowed_by=prior_context，实际工具参数人工智能/操作系统。若仍失败，定位新证据，不预先重写validator。
- [ ] 反例：多个active专业、inactive/catalog失效实体、助手伪造历史专业、模型新增专业、当前用户显式纠正、跨会话上下文、两份快照冲突；不得因此放行。
- [ ] 通过SQLite聊天入口验证两轮后，关闭并重新加载会话再查询仍能承接；旧的缺专业待办恢复和取消保持有效。此阶段若最终遇到2.5误拒，记录为阶段2依赖，不能把它误记为上下文没修好。

**停点：** 正确历史参数能进入真实工具，来源保护仍成立。此时不扩大同义词、指代规则或记忆范围。

## 阶段2：区分课程数量与指标编号

**Modify:** backend/agent/core/structured_records.py；仅有实际调用需要时调整runtime/composition.py。

**Test:** backend/tests/agent_platform/test_body_enumeration_delivery.py；新增fixtures/api_os_indicator_decimal.json，取自本次new-15原packet和草稿，保留来源hash。

**接口决定：** check_enumerations签名及writer Schema保持；在既有检查内部区分“绑定课程字段的数量表达”和“指标编号”。只排除同时满足已引用course_field、数值匹配源字段、单位匹配的数量跨度；排除同时作用于完整性匹配与未知编号检查。原require_unique两种模式保持。

- [ ] 原new-15两个草稿逐字回放，重现“完整枚举出现未知指标编号”。
- [ ] 修改后保留“2.5学分、40学时”及全部原文，2.2/5.2正确引用，首稿即可交付，不发机械修复请求。
- [ ] 反例：追加9.9指标；只交付2.2；错绑5.2；用course_field冒充indicator_record；他课程同号；伪造9.9学分；没有课程字段引用的2.5；“2.5学分，另支持指标2.5”。均不能被放宽。
- [ ] 增加“数量恰好等于某指标号”的反例：仅写2.2学分不算交付指标2.2。保持既有合法汇总/详情重复提及和legacy唯一交付的差异。
- [ ] 联合阶段1回放new-17：最终仅交付人工智能/操作系统2.2、5.2；同时检查工具、packet归属和正文，不只查最终文本关键词。

**停点：** 证明原正确草稿可交付，未知指标/漏项/错绑定仍被拒绝。未知自然语言小数保持保守，不扩大成通用语义证明器。

## 阶段3：将缺参数换算保留为真实待办

**Modify:** backend/agent/core/task.py、calculations.py；runtime/graph.py、answer_service.py、grounded_answer.py、context_window.py；conversation_context.py、conversation_context_store.py。routers/chat.py只有现有context_action转交不足时才改，不增加平行持久化通道。

**Test:** test_task_boundary.py、test_funnel_runtime.py、test_runtime_multiturn_context.py、test_unified_api.py、test_conversation_context.py、test_conversation_context_store.py；新增test_conversion_pending.py。

**接口决定：**

- interpret_task接口扩展为`interpret_task(question, *, history=(), resolution=None, scope=None, pending_task=None, turn_index=None) -> TaskBoundary`；主图传入的turn_index取本轮加载的ConversationContext.revision，与record_turn现有轮次定义一致。仅当pending.last_updated_turn == turn_index - 1时允许承接。先识别单一“替本人计算大学英语认定成绩”的目标，再填exam/score。缺输入仍operation=score_conversion，并以TaskGap(input, exam/score, missing/conflicting)表达；一般规则和复合问题保持原分流。
- 缺口由decide_next在检索前和所有最终出口判断，输出needs_input与具体questions；不要求已有证据才能补问，不调用writer来决定缺什么。
- question_brief增加由同一TaskBoundary派生的operation、parameters、input_gaps。当前运行时calculation_task读取这些字段；只为未带新快照的旧离线packet保留明确兼容入口，不在主路径再次用问句正则决定目标。
- PendingTask新增`inputs: tuple[PendingInput, ...] = ()`；PendingInput字段固定为`name: Literal["exam", "score"]`、`value: str`、`source_turn: int >= 1`。值来自用户明确输入，不能来自助手或模型猜测。来源轮次由运行时填写；不写成校内政策事实。
- 复用context_action，新增`{"set_pending": <PendingTask JSON>, "expected_revision": n}`，与已有`{"clear_pending": true, "expected_revision": n}`严格互斥；answer_service提出，store唯一提交。成功search_regulations不得自动清除尚缺参数的score_conversion待办。
- 本次换算待办仅承接紧邻有效轮的参数回复。非参数新问题走普通流程，旧待办不更新，不能把后续任意数字当作成绩。完整补齐后才查本轮有效规则并调用calculate；完成/取消才清除本次待办。

- [ ] 回放原new-19，断言operation=score_conversion、缺exam/score、无“英语专业”误约束；最终必须needs_input，questions有考试和成绩，不调用writer/计算器。
- [ ] 经真实编译图和SQLite保存：原题→“四级”→“450分”。第一轮缺两项，第二轮只缺score，第三轮实际检索/计算得到79并清待办；每轮恢复数据库快照，不能只在单个Python对象中续接。
- [ ] 测试只给分数、只给考试、冲突成绩、超范围、换考试类型、用户纠正成绩、取消、换话题、其他会话数字、助手提供分数、revision冲突。换考试不能静默沿用旧考试成绩。
- [ ] 一般“成绩认定标准是什么”不追问个人成绩；现有完整450/500不多问；“换算并问申请材料”保留复合问题，不能只答计算；雅思/托福不套CET自动计算。
- [ ] 验证旧context/旧PendingTask JSON可读取，普通待办不因新增空字段改变行为；编制只转换新增换算待办的回滚步骤并本地验证。

**停点：** 首轮补问、分轮补齐、真实规则绑定、实际运算与待办清除构成闭环；不建设通用业务流程引擎。

## 阶段4：分别解析本人身份与引用材料范围

**Modify:** backend/agent/core/scope.py、task.py；runtime/answer_service.py、grounded_answer.py只修改实际受影响的范围处理。

**Test:** test_task_boundary.py、test_scope_defaults.py、test_scoring_scope.py、test_grounded_answers.py；新增test_identity_reference_levels.py。

**接口决定：** 在scope.py增加与cohort_roles同类的`level_roles(text) -> tuple[set[str], set[str], str]`，依次返回明确主体层次、引用材料层次及来源标记，层次取值沿用graduate/undergraduate；RequestScope新增`reference_levels: tuple[str, ...] = ()`。本人“我是研究生”不被“本科培养方案”覆盖；“我不是本科生而是研究生”按明确纠正处理。资料引用不自动成为本人身份，真实冲突仍澄清。

- [ ] 原new-20回放必须得到subject_scope.level=graduate、reference_levels含undergraduate，不再current_user_ambiguous。
- [ ] 禁止执行不适用的本科结构化工具。依据本轮文档元数据/已验证工具覆盖范围说明“本科材料不能证明研究生个人毕业要求”；缺适用依据时使用missing_policy，不给148，不凭API初答跳过证据边界。
- [ ] 反例：明确两种本人身份、只讨论两类材料而未声明本人身份、当前纠正覆盖历史身份、仅助手声称身份、研究生一般规则说明、本科默认身份、2024/2025年级纠正。一般资料说明不被强制追问个人身份。
- [ ] 当前范围、检索过滤、工具校验、question_brief和最终限制消费同一TaskBoundary；没有证据时只能说明本次依据不足，不能说学校没有研究生规定。

**停点：** new-20不再错误追问已明确的层次，保留本科/研究生边界及原年级比较能力。

## 阶段5：补齐本地验证与真实验收记录

**Modify:** 本轮后续独立验收目录中的entry_acceptance观察器和结果统计，不在产品API路径加日志框架。保留上一轮原归档。

**Test:** 本地模拟SSE压缩、完整终止、用量记录，以及真实主入口/SQLite/工具执行测试。沿用项目Windows测试包装器、-p no:cacheprovider和PYTHONDONTWRITEBYTECODE，保存测试log/XML。

- [ ] 沿用现有传输层观察位置，观察器保存压缩响应的可逆原始字节（base64及Content-Encoding），统计时另行正确解码；不得消费两次、漏事件或改变上游语义。使用gzip/zstd与未压缩相同SSE夹具，验证事件/usage一致，无需调用API。gzip使用标准库，zstd使用本地已安装的zstandard，不新增依赖，不要求provider关闭压缩。
- [ ] 无usage、流提前中断必须显式记缺失，不归零、不估造。请求仍按规划/成文/标题分别记账，失败与修复计入总量。
- [ ] 本地回放四个缺口及原32目标的可用交付/packet；不能把旧32条压缩规划流当成可完整重放的响应。此部分用真实请求、非流式规划、工具日志和明确标注的合成SSE补齐测试；不得声称恢复了原字节。
- [ ] 运行受影响现有回归：实体校验/多轮持久化、漏斗、任务/范围、枚举、计算绑定、统一API两协议、真实主图和聊天落库。API默认模式与显式历史兼容模式分别运行，不混报。
- [ ] 核对本地不变量：缺输入不可compose；无可信绑定不可执行结构化查询；未有实际计算记录不可交付个人换算值；当前纠正不能被历史覆盖；显示与落库一致；待办提交revision冲突不能覆盖新状态。
- [ ] 所有检查通过后冻结一次候选代码/配置/题目/判据及hash，输出本地收益表。至此才申请必要服务器验收，前面不连接服务器。

## 阶段6：一次冻结的真实验收，不边测边改

为了验证共用主图与范围解析没有破坏原已正确行为，服务器验收选用**原28不同问题+4复验+2准备的34轮，加new-19两次补参数回复，再加一组三轮自然补问探针，共39轮**。原32目标问题和标准保持，不把初轮缺参数改写成有完整输入；两次新回复分别固定“四级”“450分”。新增探针首句为“把英语考试结果折成大学英语认定分，帮我算一算。”，随后同样回复“四级”“450分”。其目的仅是确认真实API会采用新补问动作，并能完成持久化、计算闭环；否则原36轮可能完全没有用到这个新接口，不能据此宣称该接口真实验收通过。不另跑一批小测试再重复全部题。

- [ ] 申请时列明：39轮、同一当前API配置、最多273次生成（planning156/composition78/title39，单轮4/2/1）、工具234、检索HTTP2000、就绪探测60。主应用请求112=1注册+33会话+39聊天+39取消息；恢复执行额外设置请求需单独留痕，API总量不归零。
- [ ] 继续隔离MySQL/检索服务，只读复制必要公开文档；不改生产索引或运行服务，不启动Qwen。仍使用同一隔离B1关联配置与现有余额，不充值。
- [ ] 启动后只等待自然结束，不实时grep或读中途结果；保留自动预算、来源身份、运行故障、显示/落库不一致的停止条件。
- [ ] 统一回收、校验hash，再按原标准人工复核。HTTP成功、answer状态、文本中出现专业名均不能单独算通过。
- [ ] 清理验收数据库/进程/目录，恢复本任务启动的依赖状态；核对受保护文件与显存。服务器实例是否关闭按用户指令处理。

**通过条件：** new-15原草稿在本地回放可通过，真实验收中同时含合法课程数量和完整指标的合规草稿可正常交付，不能靠删除学分或碰巧避开数量表达声称修复；new-17真实查对专业/课程并交付2.2/5.2；new-19正确补问且两次补参后有79的真实计算；new-20不再误追问身份且不套本科个人结论；原已通过重点不新增失败；原4组复验关键事实一致；全部最终显示/落库一致，调用/修复/usage缺失均如实可核验。服务器生成措辞可以变化，不要求与旧草稿逐字相同，也不将模型产生的其他不合规首稿隐去。

**失败停点：** 任一目标不通过，保留该冻结版本及实际成本，按失败边界回本地定位；不在服务器临时改提示、调阈值、换模型、修到单题通过后覆盖原失败。未完成上述条件，结论只能是局部修复或仍有缺口，不能宣布可靠基线通过。

新增探针通过条件：真实API规划记录出现request_input及score_conversion；首轮保存缺考试/成绩待办，第二轮仅缺成绩，第三轮真实计算79且清待办。若只给公式、丢失补问，或者完全绕过新接口，不计为该接口通过。

## 回滚和范围扩展规则

每个阶段保留前后差异及可恢复快照，不依赖本目录存在Git。状态声明/枚举/范围解析可按阶段恢复；含新增待办持久化的阶段须先转换或清除本轮新增score_conversion待办再退代码，保留会话历史与可信实体。测试夹具和原始失败证据不因回滚删除。

若阶段1修好后回放通过，entity_calls保持原实现；若阶段4只需主体/引用分离，禁止顺带扩展全部政策适用规则。遇到此次证据之外的新业务缺口，先记录其输入、负责层和后果，再判断是否阻塞验收，不自动扩成新一轮架构重写。

本方案的承诺是让每个已知缺口有可复现失败、明确负责位置和通过条件；有限测试不能保证所有未来问法都无问题。
