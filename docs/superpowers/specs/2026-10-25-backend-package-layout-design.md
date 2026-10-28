# 后端根目录模块归类

用户已确认目录结构并要求执行。目标是根目录只保留 `main.py` 服务入口，HTTP 接口集中在 `routers/`，将其余 29 个根 Python 模块归入职责包。

## 模块归属

- application：从 main 提取 app 装配、异常处理/静态挂载以及 lifespan；迁入 runtime_identity。
- core：config、errors；paths 提供 BACKEND_ROOT 与 PROJECT_ROOT，维持资源原有位置。
- database：db，保留现有 ORM、连接及初始化实现。
- security：auth 与 passwords（原 security.py）。
- llm：llm_client、llm_api_client、llm_api_config、deepseek_client、final_answer_profile、ctx_budget。
- entities：entity_catalog、entity_aliases、entity_resolver、entity_calls、entity_prior、entity_alias_queue。
- conversations：conversation_context、conversation_context_store、titles。
- documents：document_metadata、document_availability、document_chunks、mdify、pdfextract、ocr_engine、tablebuild。
- routers：保留现有四组路由，迁入 schemas。

## 兼容与范围

保留 `main:app` 和现有 HTTP/SSE/数据契约，不在根目录保留旧模块转发文件。同步更新后端、维护脚本、现存测试/训练编译器/PPT 工作脚本的导入、动态替身目标和源码路径。配置、语料、模型、提示词与运行数据保留原路径，显式修改随模块位置变化的路径计算。包初始化不加载模型或建立新运行入口。

只迁移文件、拆分 main、修正引用与资源定位；不改算法、协议、语料校验或测试判据，不调用付费 API、不启动服务器、不部署。

## 验收

根目录仅 main.py；相关源码可解析且导入新包；main.app 与 application.app.app 为同一对象，健康检查/异常与 SPA 行为保留；配置及默认资源仍定位至原路径。运行迁移相关现有测试和上轮模块化的主图定向检查，已知实体目录不一致和旧替身协议失败如实保留。无需前端构建，前端源码不变。
