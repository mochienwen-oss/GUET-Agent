# Presentation Agent Implementation Plan

> **For agentic workers:** 用户已经批准具体设计并要求直接执行。由本会话逐项实施，不额外开启子代理，不重复申请设计确认。

**Goal:** 多材料制作可编辑PPT、确定性数据图表、指定页面修订及实际预览。

**Architecture:** 继续使用create_presentation/create_ppt和现有任务存储/确认/下载；制作能力私有模块持有材料、分析及revision。先独立证明制作闭环，再接聊天和现有审阅界面；不重构普通问答主图。

**Tech Stack:** 现有FastAPI/Pydantic、python-pptx、python-docx/PyMuPDF、CSV/openpyxl、Vue；PowerPoint（Windows）或LibreOffice（Linux）渲染。

**Spec:** ../specs/2026-10-25-presentation-agent-design.md

## Global Constraints

- 检索召回和原问答成文冻结。
- 用户材料归任务，不进入公共知识库。
- 计算用确定性代码，原生图表/表格可编辑。
- 用户给定材料范围优先，取消固定2—5次研究要求。
- 旧39轮外发预算不能用于新付费调用。
- 现有任务归属和确认下载继续有效。

## Review Focus

- 同主题、不同适用年级/对象的材料不能混为同一规则：材料标记＋逐页来源保留。
- 空值/非数字/缺分母不能默认为0或猜：分析测试。
- 页号变化时仍保留稳定页面标识：拆分与追加测试。
- 修订中失败不得使旧文件冒充新草稿：revision/下载测试。
- 公开URL不能访问内网，跳转后也检查；仅给材料时禁止联网：资料适配测试。

### Task 1: 多材料与独立制作

**Files:** 新增scripts/materials.py、workflow.py；修改scripts/model.py、generate.py、subgraph.py；测试tests/test_materials_workflow.py。

**Interfaces:** 材料转换为任务内Material字典及evidence；generate.create_slides继续返回Slide，新增稳定ID/purpose/表达类型。workflow保存简报、材料、revision并调用现有制作器。

- [x] 写读取两份文档/CSV/XLSX、材料不入公共库、仅给材料不检索的检查，先复现缺能力。
- [x] 实现文本/DOCX/PDF/CSV/XLSX适配，记录定位及适用范围；补充任务输入API。
- [x] 解除固定研究任务数；材料充分走现有内容制作，无来源事实/建议分别表达。
- [x] 运行相关PPT检查；保留旧入口兼容。

### Task 2: 数据、修订与预览

**Files:** 新增scripts/analysis.py、preview.py；修改scripts/render.py、qa.py、delivery.py、workflow.py及routers/ppt.py；新增tests/test_analysis_revision.py。

**Interfaces:** analyse(material,spec)->带口径的计算结果；Slide通过analysis_id绑定图表。revise(task,expected_revision,target_ids,feedback)->新草稿revision。

- [x] 检查count/sum/mean/share、过滤及单位、非数字和缺分母。
- [x] 生成原生柱状/折线图与表格，完整来源放备注/来源清单。
- [x] 实现替换/拆分/追加及材料引用页更新；未选页面深度相等。
- [x] 下载token绑定revision，确认须核对当前版本；生成最终PPT页面PNG。
- [x] 用两份真实校园材料和公开数据制作初版及一次修订；渲染并人工检查（作者/计划为已标注替身，真实模型待验）。

### Task 3: 交互接入、定向补充和引擎评估

**Files:** 修改create_presentation.json、tool.py、runtime/graph.py（仅薄交接）、frontend PptReview/任务输入组件/store；新增scripts/public_materials.py、ppt_master.py（经实际能力核对后）。

- [x] 聊天交接保留模型已提取的完整制作简报；前端支持任务材料及数据分析、指定页反馈、实际页面预览。
- [x] 实现给定公开URL按缺口补充，遵循只用材料/校园规定来源，校验跳转和读取上限。
- [x] 固定并保留PPT Master所需包，独立验证导出/检查，再以同内容比较是否有实际收益（未证明收益，保持现有默认制作器）。
- [x] 运行PPT、相关主图回归和前端构建；记录实际产物、未完成项及真实API待验边界。

本地结果：148项相关检查通过，前端类型检查及构建通过。实际样例、网页检查的替身边界见`_work/phase8/presentation-agent-20261025/实现与本地验证.md`。真实API两题内容验收输入和执行脚本已准备，等待该新外发/付费范围授权；未连接服务器，未执行真实模型调用，未上线。

停止条件：上述新增行为有可检查产物和相关测试通过；不为未发生问题增加通用平台、全量跑批或额外门禁。无法在本地证明的模型/服务器效果如实列出，不能拿替身检查代替。
