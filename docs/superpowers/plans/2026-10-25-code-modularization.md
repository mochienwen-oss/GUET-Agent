# 代码模块化实施计划

**Goal:** 按已确认框架拆分后端主图与前端聊天/PPT 交互，保持行为一致。

**Architecture:** 原实现按职责迁移；主入口继续装配节点，业务模块单向依赖现有契约。前端请求、类型、状态、操作和展示分开。

**Tech Stack:** Python/FastAPI/LangGraph、Vue 3/Pinia/TypeScript/Vite；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-25-code-modularization-design.md`

## 约束与重点

- 仅本地代码整理和关联检查；不运行付费 API、服务器或部署，不改问答策略及页面样式。
- 保留 graph 的原公开函数签名和入口；新模块不得反向导入 graph。
- 保留工具消息配对、预算、重复调用及重试行为，来源仍取真实 ToolResult。
- 保留 SSE 增量、replace、title、PPT 事件及 DONE 语义。
- 保留任务 revision、确认/下载顺序和预览资源释放。

## Task 1：后端运行职责

- [x] 新增 runtime 下 entity_validation、message_protocol、tool_execution、tool_evidence、presentation_handoff 五个模块，迁移原函数及所属常量。
- [x] graph 从新模块导入原公开函数，移除迁移后无用的导入；将受迁移影响的测试替身改到实际依赖位置。
- [x] 执行现有架构、主图、实体、漏斗、工具、PPT 及交付相关本地检查；通过范围与未通过项见下方结果。

## Task 2：前端类型与请求

- [x] 提取 types/chat.ts、types/presentation.ts，迁移各消费者类型导入。
- [x] 新增 api/chat.ts、api/presentation.ts，迁移 Store 和制作/审阅组件的相关请求，保留超时和参数。
- [x] 提取 api/stream.ts，使用本地分块流验证 SSE 解析、错误与 DONE 检查。

## Task 3：前端业务操作与交付

- [x] 新增 useChatStream、usePresentationTask、usePptPreview，迁移原操作和事件应用，Chat.vue 保留页面连接。
- [x] 用本地替身验证权威替换、中断、PPT 确认/下载及预览切换释放。
- [x] 执行 npm run build；检查新模块依赖方向和实际文件规模，交付变更说明。

执行方式：依用户“执行”指令，在当前会话顺序实施；既有源码未跟踪，不自动提交整个工程。

## 实施结果

- `graph.py`：955 → 454 行；`Chat.vue`：727 → 506 行；`store/chat.ts`：原类型和业务请求分别移入 types 与 api。
- 后端最终定向检查：53 项通过，包含现有架构检查、主图决策、工具预算/重试/证据、PPT 交接，以及 4 项新增主图集成检查。新增检查使用本地小型实体目录与模型/PPT 替身，不代表真实校园语料或真实 API 验收。
- 前端 10 项行为检查通过，覆盖分块 UTF-8/SSE、DONE 与 HTTP 错误、权威正文替换和忙碌状态、中断、PPT revision 确认、下载凭证时机、上传及超时、下载资源释放、恢复和预览版本切换。
- `npm run build` 通过。沙箱内 esbuild 子进程曾被拒绝，随后以授权的同一构建命令完成；未更改全局权限或安装依赖。
- 首轮扩展后端检查共 249 项：202 通过、47 未通过。直接复现的阻塞包括：未改动的生产实体解析器加载时报告“上游语料树指纹不一致，拒绝加载旧目录”；旧收尾测试返回 claims，而当前 API 成文入口要求 paragraphs，报“正文草稿仅允许paragraphs”。保留这些失败与校验，不把本次结果描述为全量验收通过。
- 本次没有修改语料、实体目录资产、成文协议或模型配置，没有调用付费 API、连接服务器或部署。

复查命令（工程根目录；Python 环境沿用交接文档）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
& 'D:\venv-guet-ppt\Scripts\python.exe' '_work/phase8/answer-stability-implementation-20261023/run_local_tests.py' backend/tests/agent_platform/test_runtime_modularization.py backend/tests/agent_platform/test_runtime_graph_decide.py backend/tests/agent_platform/test_runtime_graph_tools.py backend/tests/agent_platform/test_runtime_graph_ppt.py backend/tests/architecture -q -p no:cacheprovider --tb=short
```

前端目录执行：`node --test --test-isolation=none tests/modularization.test.mjs`（本次 Node 24.14.1 原生 TypeScript 支持，无新增测试依赖），以及 `npm run build`。
