# 后端目录归类实施计划

**Goal:** 将后端 29 个根模块迁入职责包，保留 main:app 与现有接口行为。

**Architecture:** application 装配应用，routers 提供 HTTP 接口，其余模块按 core/database/security/llm/entities/conversations/documents 归类；包初始化为空，业务间直接导入实际模块，不留旧名转发。

**Tech Stack:** Python/FastAPI/SQLAlchemy/LangGraph；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-25-backend-package-layout-design.md`

## 约束与检查重点

- main:app 启动方式及 HTTP/SSE 契约保留；不运行 lifespan 中的真实数据库初始化和运行环境检查。
- .env、data、prompts、deploy、final-md、model 等资源仍位于原位置。
- 不改实体资产与正文协议；区分迁移引起的失败和上轮已知失败。
- 维护脚本、训练编译器及现存测试使用同一组新模块导入；不通过 sys.modules 别名或根目录包装器兼容旧名。
- 只本地迁移和检查；不连接服务器、不调用付费 API、不重建语料。

## Task 1：包结构、入口与资源定位

- [x] 创建各职责包及 core/paths.py，迁移已确认的根模块。
- [x] 提取 application/app.py、lifespan.py，将 main.py 收敛为公开入口。
- [x] 修正 config、成文提示、API 配置、实体目录和文档定位中受 __file__ 变化影响的路径。

## Task 2：消费者迁移

- [x] 更新 backend、scripts、训练集、测试集及当前 _work Python 中的静态导入。
- [x] 更新测试动态 patch/模块导入目标及源码文件定位，更新当前配置文档和命令说明。
- [x] 添加必要的路径和入口回归检查；确认根目录只剩 main.py。

## Task 3：验证与交付

- [x] 检查相关源码语法和导入，以及 main:app 的路由与 SPA 行为。
- [x] 执行迁移相关本地测试，处理迁移涉及的测试源码路径及精确依赖白名单；未通过项保留并记录如下。
- [x] 在本计划记录结果，交付新的入口与模块说明。

执行：按用户“执行”授权，在当前会话顺序实施；不自动提交未跟踪的整个工程。

## 实施结果

- 29 个根模块已迁入职责包；backend 根目录的 Python 文件只有 8 行的 main.py，继续暴露同一 app，保持 main:app。未保留旧模块转发文件或 sys.modules 别名。
- 提取 application/app.py、lifespan.py；core/paths.py 统一 BACKEND_ROOT 与 PROJECT_ROOT。配置读取、提示词、实体目录、文档原文及静态前端位置检查通过。
- 后端、维护脚本、训练编译器、历史脚本的现存调用以及当前 PPT 工作脚本已更新静态导入；动态 patch 目标和源码读取路径同步更新。解析了相关 496 个 Python 文件，未发现未迁移的旧根模块导入。
- 新增 test_backend_paths.py 的 3 项检查通过：资源定位、相对 API 配置路径、main:app 同一对象及健康接口。SPA、鉴权、会话存储、文档更新/解析、主图、PPT、实体纯依赖边界等本轮相关检查共 259 项通过，8 项失败，1 个收集错误。
- 训练集/compiler/tests/test_multiturn_compile.py 的现存兼容检查单独执行，1 项通过，验证 runtime 与 compiler 共用新包下的上下文类型与消息窗口。
- 未通过项：test_multiturn_context 在收集时因生产实体目录“上游语料树指纹不一致”停止；PPT 真实子图事件检查及两项真实主图 API 测试未通过。test_unified_api 的标题/评分/PPT 配置检查仍写死旧 PPT 2600 输出上限，而当前配置为 8192；4 项旧 vLLM 身份检查在当前默认 API 模式下先报缺少 DEEPSEEK_API_KEY。这些测试配置、语料资产及校验规则未在本次改动中调整，不宣称全部测试通过。
- 当前 API 配置文档、交接中的代码入口和 deploy 中配置路径注释已同步更新。清理清单等历史快照保持原样。
- 当前 PPT 的 upload.tar.gz 仍是迁移前归档；已在其验收范围说明中标记：后续获得真实验收授权后需按现目录重新打包。本次未重新打包、上传或执行。
- 前端源码与依赖不变，未重复构建前端；未调用付费 API、启动服务器、执行部署或修改真实数据。

定向复查使用现有运行器，例如：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
& 'D:\venv-guet-ppt\Scripts\python.exe' '_work/phase8/answer-stability-implementation-20261023/run_local_tests.py' backend/tests/test_backend_paths.py backend/tests/test_spa_routes.py backend/tests/test_security.py 训练集/compiler/tests/test_multiturn_compile.py -q -p no:cacheprovider
```

完整本轮选择还包括会话/文档/标题/请求控制、架构、统一 API、运行身份、主图/PPT 及实体纯依赖边界检查；因 test_multiturn_context 有收集错误，使用 --continue-on-collection-errors 执行其余检查，保留错误结果。
