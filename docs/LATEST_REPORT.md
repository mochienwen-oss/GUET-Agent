# 最新完成任务汇报（覆盖式）

> **本文件只保留「最近一次完成任务」的汇报，每次任务完成后整篇覆盖。**
> 历史轨迹看 `docs/WORK_PROGRESS.md`（追加式，不覆盖）。
>
> 任务：**Task 8C.2 / Task 4.2 —— 三个失败场景的替代候选调查**
> 日期：2026-10-15　状态：**已完成，停在人工选择点**

---

## 一、结论

| 缺口 | 候选数 | 合格 |
|---|---|---|
| **G1**（numeric_boundary，替代 tq-011） | 3 | **2** |
| **G2**（partial_evidence，替代 tq-013） | 3 | **0** |
| **G3**（irrelevant_result，替代 tq-015） | 3 | **0** |

**未替换任何场景、未改 selection / draft / rubric / review.md、未冻结、未进入 Step 6。**

### 一个结构性发现（代码 + 实测双向确认）

各工具能产出的状态集合是固定的，**`irrelevant_result` 在整个运行时没有任何生产者**：

```
search_regulations / query_course / list_courses / query_major_requirements → success | no_evidence
compare_course                                                              → success | no_evidence | partial_evidence  ← partial 唯一生产者
query_empty_classroom / create_ppt                                          → 恒 success
irrelevant_result                                                           → 无生产者
```

`irrelevant_result` 只出现在状态枚举、一个渲染分支、一个消费分支和一条测试里，**没有任何地方赋值它**。所以 G3 不是数据缺口，是**运行时能力缺口**。

## 二、每个候选的两轮状态与 binding

两轮 **漂移 0**（tools / statuses / binding / 预览四轴全同）。

| 缺口 | 候选 | 拟覆盖 | 实测（两轮同） | binding（两轮同） |
|---|---|---|---|---|
| G1 | C1 `专科起点本科的修读年限最长能到几年` | success | **`success`** ✅ | `cf6aeb81…f39045f` |
| G1 | C2 `转专业的学生在校总年限有限制吗` | success | **`success`** ✅ | `a55076ef…454fd174` |
| G1 | C3 `本科是不是最多读四年？` | success | `no_evidence` ❌ | `null` |
| G2 | C1 `《信息安全法》国际经贸规则/法学` | partial_evidence | `success` ❌ | `null` |
| G2 | C2 `《品牌服装设计》产品设计/动画` | partial_evidence | `success` ❌ | `null` |
| G2 | C3 `《锂离子电池电芯设计…》新能源材料与器件/材料科学与工程` | partial_evidence | `success` ❌ | `null` |
| G3 | C1 `学校能不能带宠物进宿舍？` | irrelevant_result | `no_evidence` ❌ | `null` |
| G3 | C2 `图书馆的咖啡吧几点关门？` | irrelevant_result | `no_evidence` ❌ | `null` |
| G3 | C3 `羽毛球馆怎么收费？` | irrelevant_result | `no_evidence` ❌ | `null` |

## 三、每个缺口的推荐项

**G1 → 推荐 `C1`（首选）/ `C2`（备选）**

`C1` 命中学籍管理规定**第六条修读年限**，证据原文同时给足**下界与上界**：

> (二)普通本科学生修读年限为 **3-6 年**，其中**专科起点本科学生修读年限为 2-4 年**。

它**自带培养类别对象**（专科起点本科），语料里有**专属性条款**，且必须与同句的普通本科 3-6 年**消歧** —— 正好治原场景「问句无对象」的病。**一处如实说明**：问法仍以「最长」为主，**问句本身没有显式要求下界**；要强制保留 2–4 完整区间，需人工在 rubric 里写明「不得只答 4 年」。请裁决是否接受。

`C2` 命中**第二十六条**（转专业学生累计在校年限不得超过规定修读年限），但问的是「有没有限制」而非「具体几年」，numeric_boundary 成色弱于 C1；且**主题上与 `tq-025` 邻近**（名额上限 vs 在校年限，文档不同、事实不同），标为中重复风险。

**G2 → 无推荐。** `partial_evidence` 要求「至少一个专业查不到该课程」，而本地逐文件核对 + 实测确认：**全部 18 条** compare_course 候选的课程在**两个**专业的培养方案里**都有**（`品牌服装设计` 1/1、`信息安全法` 1/1、`锂离子电池电芯设计…` 1/1）→ 恒 `success`。

**G3 → 无推荐。** 三条全部返回**空**（`未检索到相关资料。`），是 `no_evidence` 不是 `irrelevant_result`；用它顶替等于把空结果伪装成「召回了但不相关」。且与 `tq-014` 同格重复。

## 四、是否有缺口无法由现有 inventory 补齐

**有，两个。** G2 与 G3 **现有 inventory 无合格来源**。

按任务规定**不得自行新增 human-authored probe**，也不得为凑 26 条降低标准。请裁决方向：

1. G2 是否批准新增人工探针（需先核实一对**确实只在一个专业开设**该课程的专业组合，不得编造）；
2. G3 即使新增问句也改不了状态语义 —— 除非先改运行时（本轮**未授权改 `backend/**`**）；更现实的是**把 `irrelevant_result` 从覆盖门中移除**，或换其他状态承载该风险；
3. 或接受资格包暂时**少于 26 条**。

## 五、Qdrant / GPU 前中后

```
Qdrant   本轮由我启动 PID 7248（显式挂载现有 storage）；收尾**只按 7248 停止**（未用宽泛 pkill）
         停止后 6333 DOWN；storage/collection/snapshot 未删未改
身份     green / 789 points / 91 documents / index_payload_sha256=253a096b… / bm25=51aaef8d…
vLLM     全程停止；未改 .env；embedding=cpu / reranker=cuda
GPU      前 1 MiB → 两轮峰值各 4366 MiB → 后 1 MiB；无残留进程
```

## 六、生成文件 SHA

```
_work/phase6/teacher_qualification/qualification.repair-candidates.md
  77aae2eb14f911863c53313c2724863925a62f0c4dd8cc2e7a73462e8a386552
docs/WORK_PROGRESS.md
  6be224574d10a238124c807a74f19c081bc9ae82311f2372431bfc926c17e7e5
```

## 七、WORK_PROGRESS 修改摘要

追加 Task 4.2 一节：① 状态能力表结构性发现 ② 两轮定向重放表（9 候选 / 漂移 0）③ G2/G3 补不上的根因 ④ tq-022 人工裁决记录 ⑤ 产出与清理 ⑥ 资产未变与停点。

## 八、正式资产与原资格输入 SHA —— 全部未变

```
targets.jsonl 00a6ec0e…   promote.ledger.jsonl cf7e9fe2…   full_sft.jsonl e6769768…
quarantine.jsonl 2cb0e2d6…   manifest.json 0d6926f9…
inventory b970d609…   inventory.manifest c3730cae…   coverage-probes 5d141aba…
selection 20a695f7…   scenarios.draft 2c305f9c…   rubric.draft b757ffb1…
review.md 48bcd9be…（与 Step 5 收尾时逐位相同）
```

七个正式种子未变；26 条 draft 全 `pending`、`assistant_target` 全 `null`；未生成任何正式四件套；工作目录恰好 8 个文件（原 7 + 新报告 1）。服务器临时目录 `/root/autodl-tmp/tq-step42-tmp-20261015/` 整目录删除；本机 `%TEMP%\tq42\` 已删；仓库内无 `.tmp`。

## 九、明确声明

**已停止，等待人工选择。** 未替换场景、未修改 selection / draft / rubric / review.md / coverage-probes / inventory、未改 `review_status`、未冻结、未进入 Teacher 资格考试、未进入 Step 6、未训练。

---

**补充记录（本文件用途）**：文件名与位置由本轮确定，若需改名请直接说明。
详细逐条证据见 `_work/phase6/teacher_qualification/qualification.repair-candidates.md`。