# Qwen 9B 历史代码补强与 API 模型能力限制审计表

日期：2026-10-26。文件位置为本轮清理后的行号；已删除项明确写“清理前”。表名沿用用户用语，不意味着所有历史模型都确为 9B。“来源不明”不等于规则不合理；“旧模型期间存在”不等于已证明专为该模型错误设计。

**证据等级：**直接代码/受控回放可证明程序行为；历史文档可证明当时记录的设计目标；同证据真实模型记录可证明该批样本表现。没有 Git 提交，不能补造历史引入时间；没有本轮付费实验，不给出新的模型质量提升百分比。

| 文件、函数和行号 | 规则实际用途 | 能否追溯到 Qwen 9B | 原始问题/动机 | 当前 API 限制证据 | 建议与本轮处理 | 风险与对应验证 |
| --- | --- | --- | --- | --- | --- | --- |
| `backend/llm/llm_api_config.py:111` `get_api_config` | 空配置曾切回本地生成 | 可追溯 Qwen 兼容通道；不是 9B 专属 | 保留旧模型验收回放 | 默认 API 已不走它；空路径仍可重新启用，存在配置歧义 | 已移除空路径回放；现在明确报错 | 旧脚本若依赖空配置须改用 API；有空路径拒绝测试 |
| `backend/llm/llm_client.py:234` `chat_with_tools` / `stream_chat`（清理前 259/349 行） | 规划和流式中的本地 HTTP 生成分支 | 注释明确 Qwen/vLLM；重罚重复的注释具体为 14B，非 9B | fp16 重复、vLLM 默认采样与训练不一致 | API 默认早已显式配置参数，未证明旧参数仍混入；分支本身多余 | 已删除本地分支及 top_k/repetition_penalty/chat_template_kwargs 注入 | 两协议离线传输、截断、工具参数完整性检查 |
| `backend/llm/final_answer_profile.py:18` `resolve_protocol` | 历史“Qwen 规划 + DeepSeek 成文”的固定模型配对 | 混合架构时期可追溯；不是旧 9B 能力补丁的直接证据 | 固定成文实验可复现、兼容旧提供方 | API 路径已有解耦；旧分支会限制显式历史模式切模型 | 移除旧模型/提供方配对；仍校验正文协议兼容 | 协议混用仍拒绝，API 模型 ID 由同一 profile 决定 |
| 清理前 `backend/llm/deepseek_client.py:10 answer` | 单独 DeepSeek Responses 成文传输 | Qwen/API 混合架构的历史通道 | 只替换最终成文而保留本地规划 | 统一 API 后成为第二套传输；无当前默认质量损失证据 | 已删除，由 llm_api_client 负责 | 保留 strict Schema 与可选 null 还原测试 |
| `backend/agent/tools/create_ppt/scripts/generate.py:9` `_json_reply` | 旧 PPT 内容函数的本地生成备选 | 能追溯 vLLM 分支；无法证明特定 9B 错误动机 | 兼容旧生成部署 | 空配置可重新连接旧模型 | 已删除本地分支，统一 task=ppt API | 保留 JSON 对象类型及材料/页面约束，未改 PPT Master 工作流 |
| `backend/application/runtime_identity.py:11` `verify_runtime` / `lifespan.py` | 模型权重身份、/models 探测及训练上下文预算检查 | 可追溯本地模型和训练样本分布 | 避免错模型、训练时预算与部署不一致 | 默认 API 已绕过；不应继续作为 API 启动前提 | 删除本地生成探测/预算接线；保留知识库一致性 | API 启动不调用生成接口；向量与文档不符仍拒绝 |
| `scripts/runtime_service.py:41` `commands` / `deploy/runtime-profile.json` | 启动 Qwen 权重、vLLM、GPU/served-name 配置 | 明确 Qwen 专属运行项 | 宿主 GPU 加载生成模型 | API 无需这些资源；当前启动配置仍含旧字段 | 已清理；增加 scripts/local_api.py | 不部署；完整本地运行仍需 MySQL/Qdrant |
| `backend/agent/runtime/tool_execution.py:21` `execute_tools`（清理前第 32 行截断） | 每轮仅保留前 3 个调用 | 明确写明绑定旧训练单轮形态；不能精确归因到 9B | 旧训练只有单工具轮，并限制一次发出过多调用 | 同轮 5 个已验证调用，旧规则只保留 3，独立于总预算 6 | 已移除每轮截断；总预算保留 | 测试 5 个均处理、总预算以外均有未执行观察；不证明模型选工具更准 |
| `backend/agent/runtime/message_protocol.py:39` `round_messages` | 一条工具调用对应一条工具结果与正确 ID | 曾因 vLLM 400 固化；也是 API 协议必要条件 | 防止调用/结果数量与 ID 不匹配 | 无证据说明该保护阻碍合法 API 调用 | 保留；移除把训练形状视为业务正确性的说明 | 两协议请求绑定与消息无修改回归 |
| `backend/agent/runtime/registry.py:149` `schemas` / `training_schemas` | 模型可见工具 Schema 与历史导出 | 字段顺序冻结有明确 LoRA 分布动机；并非特指 9B | 避免训练列与运行工具文本漂移 | 未见 API 因 JSON 键顺序被执行层拒绝；不能宣称已证实能力限制 | 运行时不再声称受训练键序约束；离线历史导出保留 | 业务字段语义和执行验证保留；改为自动生成 Schema 须另测 |
| `backend/agent/runtime/registry.py:93` `call` | Pydantic 参数、已注册工具和来源版本校验 | 来源不明；不能标为 Qwen 补丁 | 执行前阻止空/非法参数及未授权源状态 | 目前没有“合理调用仅因旧模型规则被拒”的直接样本 | 保留；逐例检查语义规范化与校验时机 | 不得跳过权限和实际参数类型检查 |
| `backend/agent/runtime/graph.py:92` `agent_decide` / `backend/agent/runtime/grounded_answer.py:751` `safe_non_fact` | 无证据直答需命中白名单，否则合成校园检索 | 当前版本动机只能追溯到事实边界；Qwen 9B 具体来源不明 | 阻止模型凭记忆编造学校事实 | 离线明确复现：礼貌改写、能力介绍的模型直答被覆盖 | 建议通用任务/来源需求区分；本轮不改语义路由 | 全放行负对照会发布虚构学校截止日期；需真实 A/B |
| `backend/prompts/campus-v9.txt:8` / `:36` | 全局“事实只能来自证据”、无证据分支 | 证据约束时期可见；具体 9B 失败的引入因果不明 | 限制无据学校政策成文 | 范围过宽可能压制普通互动；未进行真实 Prompt 消融 | 建议将学校政策事实与普通交互分清；暂保留 | 改 Prompt 与改后置白名单必须分组实验，避免互相掩盖 |
| `backend/agent/runtime/graph.py:219` `build_agent_graph` / `backend/agent/runtime/entity_validation.py:19` `resolve_entities` | 规划前实体解析，失败可终止请求 | 有实体目录设计记录；未证实是 9B 专属补丁 | 避免同名实体和失效目录被静默选取 | 确可让模型尚未判断就终止；本地目录源不一致为实际限制 | 保留身份真实性；建议评估是否所有任务都应前置解析 | 不能以放松核验伪装本地课程工具可用 |
| `backend/agent/runtime/answer_service.py:37` `fixed_lookup` / `backend/agent/runtime/funnel.py:27` `context_route` | 精确短路与参数接续可绕过规划 | 有分层路由设计；来源不是特定 9B 故障的充分证据 | 减少明确查询的额外规划、不猜缺失实体 | 覆盖面依赖正则和待办时序，可能与 API 语义判断冲突；未证明当前具体误路由 | 建议作为单独消融因素；本轮保留 | 保持纠错、主题切换与上下文歧义对照 |
| `backend/agent/core/task.py:41` `question_type` / `interpret_task` | 程序任务维度、个人/参考范围与换算输入判定 | 旧模型时期使用，但具体补丁因果部分来源不明 | 范围不混用、缺参数先问、实际计算有义务 | 真实历史 API 曾有前序专业与换算目标未提交；另有后续修复 | 建议收缩重复语义决策，先标明当前哪些字段仍由代码裁定 | 不能删除已确认的来源范围、实际计算和个人条件约束 |
| `backend/agent/runtime/answer_service.py:59` `decide_next` | 已确认缺口决定补问、部分交付或调用计算器 | 文档有旧 9B 与 API 同时验证；不是纯旧模型补偿 | 不让模型自评覆盖率成为业务状态依据 | 可能在成文前限制表达；现有真实语义收益未重新测量 | 保留必要状态；实验区分用户信息缺口与资料缺口 | 缺输入、工具故障和无证据不得映射同一种含糊失败 |
| `backend/agent/runtime/composition.py:23` `body_contract` / `backend/llm/llm_client.py:302` `answer_with_evidence` | 默认段落+来源/计算 ID 的结构化成文 | 精简来自旧 9B 复杂契约失败，也有 API 同输入验证 | 移除 claims/status/coverage 等重复自评任务 | 当前默认已是较小正文协议；不能再把全部旧字段算成 API 现行负担 | 保留最小绑定；旧协议仅明确选择时使用 | 合法 JSON 与真实引用不等于语义正确，仍需人工判读 |
| `backend/agent/runtime/grounded_answer.py:422` `render_draft` | 旧 claims/slot_coverage/blocks、多处归一化和状态重组 | 旧模型期间可追溯，但多项也由事实安全驱动 | 补齐覆盖、避免正文重复、处理缺口与例外 | 历史复杂度高；默认 API 当前不要求该全套字段 | 建议后续审查实际调用者后收敛；本轮保留版本兼容 | 不借移除 Qwen 删除仍有 API 对照用途的业务协议 |
| `backend/agent/core/structured_records.py:66` `check_enumerations` / `composition.render_body` | 课程编号完整性与引用对应 | 旧唯一性用于旧协议；后续 API 真实误拒绝已证实 | 避免漏指标、未知编号及引用错位 | 较新 API 真实样本曾正确而被唯一性误拒绝；当前 body 已修正为可重复 | 保留完整/正确绑定；不恢复唯一性限制 | 已有 API 重复枚举反例；新增消融必须保留未知编号等负例 |
| `backend/agent/runtime/composition.py:73` `render_body` / `answer_service.complete_answer` | 格式/来源失败后最多一次结构或内容修复，再终止 | 可追溯旧模型输出失败，也保留 API 必要检查 | 不交付无效草稿；结构修复不能改事实正文 | 当前确可覆盖最终输出，但未证明所有覆盖都不必要；历史某轮 API 有重复修复仍被拒 | 保留次数与来源；建议状态和错误分类单独消融 | 不得把 HTTP200 或修复后成文直接当质量成功 |
| `backend/agent/runtime/answer_service.py:191` `complete_answer` / `grounded_answer.NO_EVIDENCE` | 工具故障、源不可用、空证据的最终说明与状态 | 来源不明；不是证实的 9B 特例 | 避免查询失败冒充学校无规定 | 普通对话被错误送入后，空证据兜底造成用户所见故障 | 先改上游任务路由设计；不要单改兜底文案掩盖根因 | 保留 no_evidence / failed / needs_input 等状态差异 |
| `backend/llm/llm_api_config.py:69` `APIConfig` / `llm_api_client._result` | 限制尚未支持的推理历史模式，拒绝截断/半参数 | 明确为 API 适配实现；不是 Qwen 补丁 | 防止遗漏须回传的推理历史、执行不完整工具调用 | 对推理模式的能力限制真实存在，但属于未实现的传输能力 | 保留并明确支持范围；未来实现完整协议后再开放 | 不能仅删除校验就宣称支持所有 API 模型 |
| `backend/agent/runtime/budgets.py:11` `budget_exhausted` / `can_retry` | 总步数、总调用、去重、重试和时限 | 有初版架构设计；数值未充分调优，不是 9B 专属补丁 | 限制成本、循环与不可恢复错误 | 复杂任务可被预算截停；无真实当前 API 最优预算证据 | 保留；后续同模型同预算研究，不能与能力补偿混删 | 本轮只移除训练单轮截断，未扩大总预算 |

| `backend/agent/core/citations.py:371` `find_degeneration` / `routers/chat.py` | 连续相同文本检测、提前截断与来源整理 | 注释明确旧本地模型复读症状；不是已证实 9B 特指 | 避免重复输出并统一可见来源 | 存在覆盖模型结果的能力；本轮未证明正常 API 正文被该规则误伤 | 保留并列入单独消融；不能因年代早就全部删除 | 正常排比/枚举与真实连续重复分别测试；来源真实性仍保留 |
| `backend/agent/core/request_control.py:40` `bounded_stream` / `routers/chat.py:273` | 内外层流共享取消标记，异常时设置标记 | 来源不明；不是 Qwen 专属补丁 | 统一超时与取消，阻止断开后继续工作 | 离线嵌套回放：上游 ValueError 被处理成错误事件后，外层却抛 InterruptedError“请求已取消” | 建议单独修复原因传播；本轮不修改通用请求控制 | 已有 error-mapping.json；不能把异常取消当作无资料或模型语义失败 |

本表的历史资料、当前故障回放与支持边界详见[架构调查与故障归因](架构调查与故障归因.md)；真实 A/B/C 方案详见[历史补强消融实验设计](历史补强消融实验设计.md)。
