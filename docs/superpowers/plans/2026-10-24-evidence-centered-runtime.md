# Evidence-centered runtime Implementation Plan

> **For agentic workers:** Execute the approved design sequentially in the main thread. No parallel code writers. User has already authorized local implementation; do not request the same approval again.

**Goal:** 任务、证据和交付各有唯一负责人，精简模型成文职责并保持旧默认对照。

**Architecture:** 在现有core/runtime/tools接口上增加必要契约和应用决策函数。业务判断独立于LangGraph；固定工具和模型建议共用验证、执行与预算；HTTP消费统一最终答案。

**Tech Stack:** Python、LangGraph、Pydantic、FastAPI、SQLAlchemy、现有RAG与模型适配。

**Spec:** docs/superpowers/specs/2026-10-24-evidence-centered-runtime-design.md

## Global Constraints

不新增依赖、通用平台/长记忆/语义模型/PPT重构；不改冻结训练/schema/资格/别名/family资产或索引。复用原始验收材料。默认协议保持。服务器及外部API均需新授权。

## Review Focus

- 明确身份、文件引用、比较、纠正及真实歧义；引用证据可解释而不可错误个人适用。
- 已知身份不可重复追问；缺政策、缺输入和工具故障分开；自由模型reason不成为权威政策。
- 同表恢复不得跨文档/版本/类别；不伪造规则；预算不足须可观测。
- 简化正文仍接受来源、枚举、来源版本和实际计算校验；不宣称语义完整。
- 真实SSE/SQLite落库一致；旧默认与成功指标/预警行为保持。

## Task 1 — 请求边界

Files: core/task.py、core/scope.py、runtime/grounded_answer.py、runtime/graph.py、runtime/state.py。
Consumes: question/history/resolution；Produces: interpret_task(...)→TaskBoundary，身份和引用角色分离，模式与绑定来源一处解释。
- [x] 用跨年级原例、一般查询、明确纠正、无身份比较与真实冲突写失败回归。
- [x] 实现解释并接入resolve、question_brief和证据用途，旧scope接口兼容。
- [x] 检查模型建议不能覆盖边界，范围不一致引用不能变个人依据。

## Task 2 — 应用服务与统一答案

Files: core/answer.py、runtime/answer_service.py、runtime/graph.py、routers/chat.py。
Consumes: TaskBoundary、证据包、工具状态、已校验正文；Produces: FinalAnswer及明确下一步决定。
- [x] 复现跨年级草稿解释丢失、缺输入/缺政策与故障混淆。
- [x] 实现程序确认缺口和模型建议分开、统一最终状态与来源、最终文本构造。
- [x] 图输出与HTTP仅消费对象；验证SSE、持久化、上下文与审计一致。

## Task 3 — 目标表格记录与证据交接

Files: tools/search_regulations/scripts/table_selection.py、retrieval.py、tool.py及core/calculations.py适配；tests针对真实失败行形状。
Consumes: query、既有表格候选与同表读取、恢复预算；Produces: 原始匹配记录、具体记录缺口、阶段审计。
- [x] 复用已保存轨迹定位CET4最后可确认阶段；若真实候选数据未保存，明确未定位，不猜原因。
- [x] 对现有支持的明确CET类别/分数任务修选择和预算，必要时同表读取并共享一次恢复额度。
- [x] 检验同源版本、边界值、缺行、预算/冲突与真实calculate，不扩大通用政策语义覆盖。

## Task 4 — 精简成文与薄编排

Files: runtime/answer_service.py、runtime/graph.py、llm_client.py、runtime/paragraph_answer.py或独立候选。
Consumes: 程序决策与证据包；Produces: paragraphs-only候选和FinalAnswer；旧默认不变。
- [x] 实现仅正文与最小引用的显式新候选，不生成status/missing/覆盖映射，不靠旧协议伪装实现。
- [x] 复用低层来源/枚举/计算检查；应用服务决定状态，故障与检索缺口不靠反复成文恢复。
- [x] 已明确且参数完整的支持任务减少模型规划；复杂输入回现有有界Agent，共用Skill/Registry/预算。
- [x] 模型规划与成文用途接口分离，保持现有供应商配置与禁止未经授权真实调用。

## Task 5 — 本地贯通与停点

- [x] 按变更运行必要定向与跨模块回归，记录历史失败，不能为绿灯改旧断言。
- [x] 分开报告机制、契约、真实HTTP/数据库与尚未验证语义；记录实际改动和限制。
- [x] 本地充分完成后提出新有限服务器范围与预算，未授权前不调用；不将文档或测试数量当完成。

无Git仓库，不制造提交；原始记录保持。通过检查后仅因新增改动或未解失败扩大/重复测试。

## 本地完成与剩余边界（2026-10-24）

上述勾选表示本地实现与相应检查完成，不表示真实模型验收完成。实施依据、改动和限制见[本地实施结果](../../../_work/phase8/evidence-centered-runtime-20261024/本地实施结果.md)。355项较大范围回归通过，最后99项定向回归通过，两者有重叠；5项历史排除保留。默认grounded-draft/5.1未切换，新候选为grounded-body/1。

- [x] 取得新授权，在真实索引保留完整轨迹，确认CET4具体丢失阶段及目标行进入最终输入。旧记录只支持原文有行/最终缺行。
- [x] 在同事实输入下有限比较真实9B，审阅关键前提、例外、数量归属与已有成功行为，再决定默认是否切换。

候选包仅在本地准备；本轮未连接服务器或调用真实模型/API。范围与预算见工作目录的新一轮有限服务器验收.md。

## 侧会话审核补充修正（已授权本地实施）

范围仅限已批准职责约束的两处遗漏，不开启新架构设计。

- [x] 先复现：双目标请求被个人适用缺口整题截断、一般规则被无关身份歧义截断、正文结构修复落入旧claims分流。
- [x] TaskBoundary保留个人结论依赖与显式引用说明目标；应用服务仅阻断受影响结论，已确认局部缺口随部分答案与来源一起交付，不根据工具partial_evidence直接定整题状态。
- [x] grounded-body/1独立判断草稿可用性及结构/内容/重生成；结构修复保留正文顺序，内容修复仍重新通过来源、枚举、计算、版本检查，第二次失败不发布无效草稿。
- [x] 验证原跨年级个人判断保护、一般规则、部分状态/限制及SSE/存储；更新实际结果和候选hash，真实索引/9B验收继续待新授权。

审核补充已完成本地实现：原反例未删除，修正前7项失败保留；最终376项通过、5项原历史排除，46项贯通检查与其重叠。资料说明与个人结论的局部缺口、独立正文修复类别及部分状态到SSE/SQLite均有验证，详见本地实施结果.md。真实索引/9B仍未验证，默认保持，新候选待新授权。

最新服务器状态：用户条件授权已满足，独立本地验收通过后已完成autodl只读预检；候选代码与冻结输入包的SCP上传被自动审批拒绝，需明确该包上传至该目标的许可。未改用其他传输方式，未启动真实检索/推理，消耗0次执行/0次生成；本任务临时目录已清理。原预算与默认保持。详情见_work/phase8/evidence-centered-runtime-20261024/server-validation/预检与上传阻断.md。

## 有限验收最终状态

用户明确授权上传及回收后，完成2次检索取证、16次执行/25次生成请求。上述真实验收项勾选表示验收动作已完成，不代表语义质量通过。当前复现定位CET4在候选过滤后选择阶段丢失，修复后进入最终输入并实际calculate450→79；跨年级具体缺口与旧指标/预警行为得到验证。新正文候选漏范围/均分，指标编号未交付；默认充分证据仍漏毕业前提。默认不切换，不继续追加本轮9B提示词或推理；下一步同证据有限API成文比较须另行明确模型、数据和预算。报告：[验收结论](../../../_work/phase8/evidence-centered-runtime-20261024/server-validation/验收结论.md)。本任务服务/目录已清理，生产受保护文件未变。

## 经审核认可的后续收敛顺序

用户已在侧会话批准最小契约收尾并转交主会话，随后在主会话明确要求调用服务器.bashrc已有DeepSeek API查看对比效果。

- [x] 先复现并修正body提示的编号含义：业务record_id须在正文与对应unit_id一起交付，内部追踪ID不得进入正文；不修改schema、不放松完整枚举或写入具体答案。必要本地回归后冻结提示和源文件hash。
- [x] 使用原4份冻结事实输入及原grounded-draft/5.1契约做DeepSeek最终成文对照。保留原9B结果，不重启检索；服务器仅使用已有密钥，默认deepseek-flash，最多8次API生成请求，失败与必要修复计入，不补做任意额外题。
- [x] 记录原始请求、草稿、交付、usage、接口错误及输入/契约hash；分别判断语义遗漏和渲染/校验失败。不将修改后的body契约与旧body结果混作模型对比；如要比较修订body，另行申请对应9B回放，不沿用已结束额度。
- [x] 据证据选择：API同契约保留必要语义且9B遗漏时优先替换成文；两者同处失败则查对应输入/契约；草稿对而交付错则修交付；四题均通过也只进入少量未参与修改题验收，不直接发布或认定全局可靠。

不增加专用语义审查平台、全库结构化、逐题关键词或固定模板。通用SYSTEM_PROMPT及重复证据仅保留为待验证因素，本轮不先优化。API接入不是全Agent替换，生产默认不自动切换。

本轮收口：最小body提示收尾已冻结；原默认5.1的4题DeepSeek成文对照完成，共5次API请求（含1次schema拒绝），修复后4次成功、无正文重试。4题关键语义检查满足，API仍有冗余与长段；优先选为后续成文候选，不修改生产默认、不重启9B。传输兼容修复及相关本地101项通过，原始草稿/交付/usage/hash已回收核对，服务器隔离目录已清理。详见[API对照结论](../../../_work/phase8/deepseek-fixed-20261024/对照结论.md)。

- [ ] 后续推广门槛：对少量未参与修改题与实际链路作有限验收，再决定部署；需另行明确数据外发和调用范围，本轮不追加。可靠成文全局目标仍未验收通过。
