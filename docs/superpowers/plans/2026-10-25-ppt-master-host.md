# PPT Master Host Implementation Plan

**Goal:** 让PPT Master成为新任务的实际制作流程，接入现有网页确认、任务与交付。

**Architecture:** 能力内部新增工作区/上游命令适配与模型工具循环；workflow选择新宿主，旧任务保留兼容；router与前端补确认和分段继续。

**Tech Stack:** 现有Python/FastAPI、统一LLM API、固定PPT Master 6.7.3、Vue/TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-25-ppt-master-host-design.md`

## 约束与验证重点

- 不修改上游Skill包、不开放任意终端、不静默退回旧制作器。
- 只在当前用户任务目录读写；保留材料范围、版本、实际预览和下载归属约束。
- 两阶段确认必须来自网页实际提交，恢复不重复执行已完成工具。
- API请求有界、可取消；失败保留原版本，不扩大付费或部署范围。
- 检查分别覆盖：越界路径、确认跳步、工具失败/续接、旧下载版本、未选页被改动。

## Task 1：上游工作区与执行接口

Files: 新增`backend/agent/tools/create_ppt/scripts/master_workspace.py`；保留旧`ppt_master.py`为历史评估适配器。

- [x] `MasterWorkspace(root)`提供包内资源读取、任务文件读写、上游白名单命令、初始化和真实PPTX交付检查。
- [x] 现有材料/表格/分析写入sources；保留来源ID、适用范围及单位。
- [x] 定向检查路径限制、上游脚本失败、页数/来源与未选页保留；运行真实上游入口检查。

## Task 2：模型循环及默认任务入口

Files: 新增`master_host.py`；修改`workflow.py`、`llm_api_client.py`、`delivery.py`及PPT薄交接。

- [x] `advance(state, confirmation=None, complete=None)`持久化工具消息，运行有限步数，返回待确认、暂停、失败或真实draft。
- [x] 同步API接口支持tools；模型可读取Skill、写任务文件并串行执行允许的上游脚本。
- [x] 新建/聊天交接任务默认使用PPT Master；现有旧任务继续按原renderer处理。
- [x] 定向验证Stage 1/2真实确认、工具失败继续、默认路由、版本及指定页修订。

## Task 3：网页确认与继续

Files: 修改`backend/routers/ppt.py`、`frontend/src/api/presentation.ts`、`types/presentation.ts`、`store/chat.ts`、`components/PptReview.vue`、`PresentationComposer.vue`。

- [x] API增加策划答复和有限继续入口，状态只返回用户所需信息，不返回宿主绝对路径/模型上下文。
- [x] 页面显示策划建议、确认/修改反馈及分段继续；最终草稿仍由原确认下载接口交付。
- [x] 执行相关PPT/API检查与`npm run build`，更新当前交接说明，准确标注真实模型验收未执行。

结果：37项定向检查通过，追加实际未选页面篡改的交付拒绝检查通过，前端构建与桌面/窄屏确认交互通过。PPT扩展范围保留1项既有主图事件失败；另行尝试应用Skill检查在生产实体目录指纹校验处收集失败，未执行。详见`_work/phase8/ppt-master-host-20261025/实现与验证.md`。

执行方式：当前会话顺序实施；用户已明确“执行”，不重复发起计划审批，不自动提交整个未跟踪工程。
