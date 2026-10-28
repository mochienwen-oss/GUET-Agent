# 当前代码模块化设计

依据：用户已确认会话中的预期框架，并明确要求执行。

## 目标与范围

保留现有功能、API 路径、数据格式及页面样式，将主图和聊天页面中混合的职责移到独立模块。沿用现有依赖，不改变检索、问答策略、计算或 PPT 制作规则。

## 后端

`agent/runtime/graph.py` 保留图装配、模型决策、收尾及统一流入口；将实体解析和参数验证移到 `entity_validation.py`，消息构造移到 `message_protocol.py`，工具预算/去重/重试/执行移到 `tool_execution.py`，结果来源转换与证据追加移到 `tool_evidence.py`，PPT 交接移到 `presentation_handoff.py`。保留 graph 原有公开函数导入入口。新模块不反向依赖 graph。

## 前端

从聊天 Store 提取 `types/chat.ts` 和 `types/presentation.ts`；`api/chat.ts`、`api/presentation.ts` 负责业务请求，`api/stream.ts` 负责流式传输与 SSE 解析。`useChatStream.ts` 负责发送、中断、重生成和事件应用，`usePresentationTask.ts` 负责任务恢复、确认和下载，`usePptPreview.ts` 管理预览图片资源。Store 保留共享状态与统一任务映射，组件保留布局和输入交互。

## 验收

现有定向测试覆盖实体校验、消息形状、工具预算与重试、证据、PPT 交接和交付；前端通过类型检查与构建，并以本地替身验证拆出的流协议、任务操作及预览资源生命周期。保持权威全文替换、唯一完成信号、中断、revision 确认行为。测试不调用付费 API，不连接服务器。
