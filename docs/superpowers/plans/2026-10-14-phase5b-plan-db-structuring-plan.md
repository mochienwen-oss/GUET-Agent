# 培养方案结构化通路（阶段5B）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `_md/培养方案/` → `plan_db` 这条通路从"先抽不核"升级为**可溯源、可对账、可回滚**的结构化通路，
**并且按"先修数据、再扩工具"的顺序分两小阶段交付**：
**5B-1 只修数据**（抽取器 / 字段校验 / 来源记录 / 质量报告 / `plan_db.json` 构建结果），
**5B-2 才扩工具**（`list_courses` / `query_major_requirements`）。

**Architecture:** 5B-1 的输出改成 **v2 schema**（新增行级 `src` / `extractor` / `confidence` / `corrections`
与文档级 `quality` / `provenance`），**旧字段全部原位保留**，因此 `query_course` / `compare_course`
在 5B-1 期间**一行都不用改**。质量分三类门（硬失败 / 警告 / 人工复核），
"修不了但也不该丢"的进**复核队列**而不是被静默丢弃 ——
阶段5B 审计已证明：**当前最大的缺陷（21 行被丢）恰恰是唯一不产生任何 warning 的那一类**。
5B-2 在 5B-1 的验收门全过之后才动 `registry`，且**只加两个各司其职的精确查询工具**。

**Tech Stack:** Python 3.13、PyMuPDF（`pymupdf==1.28.2`，已在 `backend/requirements.txt`）、pytest。

**Spec:** `docs/阶段5B-培养方案结构化审计.md`（已审核）；`docs/superpowers/specs/2026-10-13-agent-platform-redesign-design.md` §7.2、§7.5。

## 阶段边界（本轮新增的硬约束）

```
5B-1 结构化数据修复        只改：抽取器 / 字段校验 / 来源记录 / 质量报告 / plan_db.json 构建结果
                          不加新工具。防止「底层数据还不可信，上层接口先扩张」。
   ↓ 通过 5B-1 验收门（§Gate-1，6 条）
5B-2 扩展精确查询工具      数据通过后，才加 list_courses / query_major_requirements
```

**两条阶段之间的门是硬的**：5B-1 未全过，**5B-2 一个字符都不许写**。

## Global Constraints

**禁止（五条，硬性）：**
1. **不得用 LLM 生成数值事实** —— 学分/学时/学期/指标点只能由确定性代码从原文算出。
2. **不得用现有 `plan_db` 反向生成测试答案** —— 金标准必须回到原始 `_md` / PDF 逐条人读。
3. **不得把 OCR 堆叠文字推断成先修关系** —— 拿不到矢量结构就**明确标不可用**（设计 §7.2）。
4. **不得把「全部课程学分之和 == 毕业学分」设成硬门** —— 选修菜单本就多于应修学分，
   审计实测该口径 62/63 假报警。只对 **模块学分和 vs 九节声明总学分** 设门，且为**警告级**。
5. **数据质量通过前不得修改训练集** —— `训练集/**` 在 5B-1 全程只读。

**阶段专属边界：**
- **5B-1**：只动 `query_course/scripts/build_plan_db.py`（及其 tests）；**不碰** `registry.py` /
  `schemas.py` / `repository.py` / 任何工具契约；不碰 `_md/`（只读）。
- **5B-2**：才允许动 `registry.py` / 新增工具包；**原有四个工具的职责与冻结 `PARAMETERS` 不变**。
- 两阶段都不碰：向量库（`school_docs` / `school_docs_phase5`）、服务器、训练/部署。
- 特判必须**内容键**（课号 / 课程名 / 文号），**不许行号键** —— 审计 F2 就是这么坏的。

## Review Focus

1. **行号键必然烂** —— `CREDIT_FIXES` 用 `(文件名, 行号)`，`_md` 重洗后修到了别的课
   （`军事技能` 被 0.0→2.0）。任何以位置为键的东西都过不了语料重洗。
2. **静默丢弃是最坏的失败** —— F1 的 21 行不产生任何 warning。新通路里**丢弃必须是显式动作**，
   要么进 `rejected.json` 带原文与理由，要么进复核队列，**不允许 `return False` 了事**。
3. **对账口径要选对** —— 只对模块学分和 vs 声明总学分设门；不要用"全部课程学分和"。
4. **v2 不能破运行时** —— `repository.py` 的 `COURSE_FIELDS` / `sources.line` / `majors` 形状原样可用。
5. **加工具不能改训练分布** —— `training_schemas()` 由 `TRAINING_TOOL_NAMES`（4 个）过滤，
   加工具**不影响**它；但 `test_five_tools_are_registered` 断言了确切的 5 个名字，**必须同步更新**。

---

## 1. 最终结构化字段与字段类型

**顶层（`plan_db.json`）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `meta.schema_version` | `str` | `"plan_db/v2"` |
| `meta.extractor_version` | `str` | 见 §2 |
| `meta.source_fingerprint` | `str` | `_md/培养方案` 整树指纹（复用阶段5A `corpus_fingerprint`） |
| `meta.built_at` | `str` | 构建时间（不参与确定性） |
| `majors` | `list[Major]` | 63 条，按文件名排序 |

**Major**

| 字段 | 类型 | 备注 |
|---|---|---|
| `file` `major` `grade` `college` `code` `degree` `duration` `level` `degree_level` `major_field` | `str \| null` | 保留 |
| `total_credits` | `float \| null` | 保留（= `credits.total_declared`） |
| `module_credits` | `dict[str, float]` | 保留 |
| `indicator_text` | `dict[str, str]` | 保留 |
| `core_courses` `practice_courses` | `list[str]` | 保留 |
| `courses` | `list[Course]` | 见下 |
| **`credits`** | `{"total_declared": float\|null, "modules_sum": float\|null, "consistent": bool\|null}` | **新增** |
| **`quality`** | `{"status": "ok"\|"warn"\|"needs_review", "hard_failures": [...], "warnings": [...], "review_items": [...]}` | **新增**，取代 `low_confidence` 的语义 |
| **`provenance`** | `{"file": str, "sha256": str, "estate8": "extracted"\|"unavailable_by_decision"\|"unavailable_raster", "strategy": str}` | **新增** |
| `low_confidence` | `bool` | **保留**（= `quality.status != "ok"`），仅作过渡 |

**Course（`courses[i]`）** —— 前 17 个键**原样保留**（`repository.COURSE_FIELDS` 依赖）：

| 字段 | 类型 | 新增? |
|---|---|---|
| `name` `code` `nature` `dept` `module` `semester` | `str \| null` | 保留 |
| `module_path` `indicators` | `list[str]` | 保留 |
| `credits` `hours` `theory` `lab` `practice` | `float \| null` | 保留 |
| `required` `is_core` `is_practice` | `bool` | 保留 |
| `src_line` | `int` | 保留（`repository` 暴露为 `sources.line`） |
| **`src`** | `{"file": str, "line": int, "row_hash": str, "fragment": int}` | **新增** |
| **`extractor`** | `{"name": "build_plan_db", "version": str, "strategy": "header"\|"code_anchor"\|"manual"}` | **新增** |
| **`confidence`** | `{"level": "high"\|"medium"\|"low", "reasons": list[str]}` | **新增** |
| **`corrections`** | `list[{"field","from","to","reason","authority":"human_review"\|"rule","by","date"}]` | **新增**（空列表 = 未改过原文） |

> 原则：**新增字段一律加性**，旧键原位保留 → `repository.py` 一行都不用改（§9）。

## 2. 行级来源、原始行哈希、抽取器版本、置信状态

- **`src.row_hash`**：对**该行归一化后的原始 md 文本**取 `sha256`（口径与阶段5A
  `corpus_fingerprint` 一致：`open(encoding="utf-8").read()` 折行后取行、strip、NFKC+去空白）。
  用途：① 定位回原文；② 下次重洗时**判"这一行变了没有"**，而不是靠行号猜。
- **`src.fragment`**：该行所属的表片段序号（十节被分页切成多段，每段重建表头）。
- **`extractor.version`**：形如 `build_plan_db/2.0.0`；**任何解析规则变更必须 bump**，
  并在 `_work/plan_db/extractor_changelog.md` 记一行（改了什么 / 为什么 / 影响面）。
- **`extractor.strategy`**：`header` / `code_anchor` / `manual`。
- **`confidence`**：`high`（表头解析成功、学分合理、未修正）/ `medium`（落在 corrections 上，或表头由 ≥2 行竖排拼成）/ `low`（特判路径，或缺字段）。
  `reasons` 是**可读字符串列表**，不是数字。

## 3. 三类质量门

| 类别 | 判据（举例） | 处置 |
|---|---|---|
| **硬失败** | 课程名为空 / 学分非数值且无法解释 / 同一行解析出 ≥2 个 6 位课号 / 行无法定位回源行 | 该行**不进 `courses`**；写进 `quality.hard_failures` **并进 `_work/plan_db/rejected.json`**（**必须带原文行与理由**）。文件级有硬失败 → `status = "needs_review"` |
| **警告** | 学分缺失 / 模块学分和 ≠ 九节声明总学分 / 矩阵 token 未匹配 / 封面缺学分段 / 落 `corrections` / 表头竖排拼接 | 行**保留**，`confidence` 降级，进 `quality.warnings`；`status = "warn"` |
| **人工复核** | 判据拿不准；或能按规则修但会牵动多门课（如 F1 冒号判据） | 进 `_work/plan_db/review_queue.json`；**未复核前不许改**；结论写回 `corrections` 且 `authority="human_review"` |

**明确不设为门的**（禁止 4）：全部课程学分之和 == 毕业学分。**只做**
`credits.modules_sum` vs `credits.total_declared` 的**警告级**对账。

## 4. 各文档格式族与解析策略

阶段5B 审计实测出的族（**不是猜的**）：

| 族 | 判据 | 规模 | 策略 |
|---|---|---|---|
| **A 标准表** | 表头单行、列齐全 | 多数 | 现 `parse_plan_table`（片段级表头重建） |
| **B 分页 14/15 列混排** | 同文件出现两种列数 | **63/63** | 已是现状：**每片段重建表头**，禁写死列号 |
| **C 表头竖排爆开** | 表头与数据行交错、索引不稳 | **2 份**（测控、自动化） | 课程代码锚定 |
| **D 封面缺学分段** | `COVER_RE` 不中 | **4 份** | 九节兜底 + 警告 |
| **E 课程名含冒号** | 课程名列含 `:`/`：` | **11 份 / 21 行** | ★ 改判据：只有**整格形如 `^(要求学分\|要求子模块数)[:：]`** 才算汇总行 |
| **F 九节模块学分明细不全** | 模块学分和 ≠ 声明总学分 | **8 份** | ★ 重写九节抽取（Task 5） |
| **G 无第八节** | 节号 七→九 | **63/63** | **用户主动删除，正常**，不报警（§6） |

## 5. 逐文件特判清单及理由

| 文件 | 现键 | 理由 | 改法 |
|---|---|---|---|
| `2025级测控技术与仪器专业培养方案(主修).md` | 文件名 | 表头竖排爆开且与数据行交错，表头索引无法稳定重建 | 保留文件名键 |
| `2025级自动化专业培养方案(主修).md` | 文件名 | 同上 | 保留文件名键 |
| `2025级密码科学与技术专业培养方案(主修).md` | **`(文件名, 行号)`** | 源里两行学分漏小数点（写成 32） | ★ **改内容键**：`(文件名, 课号)` → `030246`=2.0、`030245`=2.0 |

**禁用**：任何以**行号**为键的特判。实证：第 337 行现在是 `军事技能`，
被这条特判从 0.0 改成 2.0 —— **修错了课，真目标仍在丢**。

**新增前必须满足**：每条特判 = `{内容键, 值, 理由, 依据(原文引用), 作者, 日期}`，
写进 `_work/plan_db/manual_overrides.json`，并在 `extractor_changelog.md` 留痕。

### 高风险文件（人工核对清单）—— **21 / 63**

判据与缺陷直接挂钩（**不含**噪声型 warning，如 `syllabus_name_unresolved`）：

| 文件 | 触发 |
|---|---|
| 产品设计 | 模块学分差 −19.5 |
| 动画 | 模块学分差 −24.0 |
| 建筑环境与能源应用工程 | 模块学分差 −34.0 |
| 环境设计 | 模块学分差 −34.0 |
| 电子封装技术 | 模块学分差 −43.0 |
| 视觉传达设计 | 模块学分差 +2.5 |
| 国际经贸规则 | 丢课号 3 / 模块学分差 +1.0 |
| 生物医学工程 | 丢课号 3 / 九节无总学分 |
| 密码科学与技术 | 丢课号 2 / 拒收行 2 |
| 法学 / 知识产权 | 丢课号 4（各） |
| 导航工程 / 微电子科学与工程 / 智能感知工程 / 环境工程 / 电子信息工程 / 电子科学与技术 / 通信工程 | 丢课号 1（各） |
| 光电信息科学与工程 / 数字媒体技术(中外合作办学) / 智能视觉工程 | 封面缺学分 |

## 6. 先修后修关系图的处置方式

**两件事分开**：**状态标记（5B-1 必做）** 与 **是否抽取（用户裁决后才做）**。

- **现状**：`_md` 里 63/63 无此节 —— **用户主动删除的**（2026-10-14 声明），不是缺陷。
- **源 PDF**：63/63 标题都在；**53/63 是矢量绘图**，10/63 该页无矢量对象。

**5B-1 内必做（不依赖任何裁决）**：每份文件必须在 `provenance.estate8` 里给出**明确状态**，
取值只能是 `extracted` / `unavailable_by_decision` / `unavailable_raster` ——
**"没有"必须写成显式状态，不许留空、不许默认**（对应 5B-1 验收第 5 条）。

**前置动作（阻断式，见 Task 0）**：把每份 PDF 的 §8 页**裁剪渲染成 PNG** 供用户判效果。
用户裁决写进 `docs/阶段5B-先修图裁决.md`：
- 裁决"做" → 走**坐标法**（文字 span 定位节点、`get_drawings()` 定位边），
  产物独立成 `backend/data/course_relations.json`，**不进** `plan_db.json`、**不进**向量库；
- 裁决"不做" → 63 份统一 `unavailable_by_decision`；
- **红线**（禁止 3）：位图那 10 份若只能得到堆叠文字 → `unavailable_raster`，**不得推断**。

## 7. 独立课程金标准的构建办法

**目标**：一把**不依赖 plan_db** 的尺子，能回答"这门课在这个专业是不是 3 学分"。

**来源与流程（禁止 2）**

1. **候选队列**（三处，都不含 plan_db）：
   - `测试集/agent_acceptance.json` 里 `kind ∈ {培养方案, 跨专业对比}` 且带 `must_include` 的条目；
   - 阶段5B 审计的**缺陷行**（F1 的 21 行、F2 的 2 行）；
   - 每专业分层抽样（必修/限选/实践/核心）。
2. **逐条回原文人核**：读 `_md` 那一行（必要时回源 PDF），把 `学分/学时/学期/性质` **从原文抄**。
3. **产出** `测试集/course_gold.jsonl`：

```json
{"id":"course-gold-001","major":"交通工程","course":"思想道德与法治",
 "fields":{"credits":3.0,"hours":48.0,"semester":"1","nature":"必修"},
 "source":{"file":"2025级交通工程专业培养方案(主修).md","line":215,"row_hash":"…","pdf":"…#page=…"},
 "review_status":"human_verified","verified_by":"…","date":"2026-10-14"}
```

4. **不与 plan_db 互校**：金标准只跟原文对齐；plan_db 的通过率是**被测量**。
5. 覆盖：**≥ 60 条**，**必须含全部 23 条审计缺陷行**。

## 8. plan_db 重建、回滚与版本标识

- **重建**：`python backend/agent/tools/query_course/scripts/build_plan_db.py`（一条命令，无手工接力）。
- **写盘前**：现有 `backend/data/plan_db.json` → `_work/plan_db/plan_db.pre-phase5b.json` + SHA-256，
  记入 `_work/plan_db/asset_inventory.md`。
- **原子写**：临时文件 + `os.replace`（沿用 `build_gold.py` 的模式）。
- **版本标识**：`schema_version` / `extractor_version` / `source_fingerprint` / `built_at`。
- **回滚**：把备份覆盖回去即可；运行时读到的形状不变（§9），**回滚不需要改代码**。
- **重建后自检**：`meta.docs == 63`、每份 `courses` 非空、打印 `quality.status` 分布。

## 9. `query_course` / `compare_course` 的兼容边界

**必须保持不变（契约）**：

| 契约 | 位置 |
|---|---|
| `_DB["majors"]` 为 list | `repository.py:25` |
| `COURSE_FIELDS` 全部键存在且语义不变 | `repository.py:20` |
| `major["file"\|"college"\|"total_credits"\|"module_credits"\|"indicator_text"]` | `repository.py:78-88` |
| `course["src_line"]` → 卡片 `sources.line` | `repository.py:89` |
| 模糊匹配三级 + 后缀最短优先 | `repository.py:66-73` |
| `compare_course` 未开设也出行 | `repository.py:130-139` |
| 四个工具的 `PARAMETERS` 冻结块 | 各 `schemas.py` |

**允许新增**：course 上的 `src`/`extractor`/`confidence`/`corrections`，major 上的
`credits`/`quality`/`provenance` —— `repository` 只取认识的键，多余键无害。

**5B-1 验收门**：对采样 (专业, 课程) 比对重建前后 `query_course` / `compare_course` 返回
**逐字段相同**（除新增键）。**不同 → 停，报告，不许改 `repository` 去迎合。**

## 10. 是否增加 `list_courses` / `query_major_requirements`

**结论：增加 —— 但只在 5B-2，且 5B-1 验收门全过之后。**

**先决条件**：`list_courses` 的过滤依赖 `semester` / `module` / `required` / `core_only` 四个字段的
**可信度**；审计已证明这四类字段当前**存在丢行与错位**（F1/F2/F4）。
**数据不可信时先把接口铺开，等于把不可信数据喂给模型。**

**两个新工具的签名（5B-2）**

```
list_courses(major, semester?, module?, required?, core_only?)   # 按条件列课程
query_major_requirements(major)                                   # 专业整体与模块学分要求
```

**加工具的技术前提（已核实）**：
- `training_schemas()` 由 `TRAINING_TOOL_NAMES`（4 个）过滤 → **加工具不影响它**，
  冻结训练列那条门（`test_shared_tool_schemas_are_byte_identical_to_the_frozen_training_column`）**仍然绿**。
- 但 `test_five_tools_are_registered` 断言了确切的 5 个名字 → **必须同步更新为 7 个**。
- 四个**原有**工具的职责与冻结 `PARAMETERS` **一律不动**。

## 11. 工具职责边界（四工具各司其职，禁 mode 大工具）

| 工具 | 回答的问题 | 输入 | 输出形态 |
|---|---|---|---|
| `query_course` | **一个专业的一门课程**是什么样 | `major, course` | 课程定位卡（含指标点） |
| `compare_course` | **同一课程跨专业**怎么比 | `course, majors[]` | 一行一专业，未开设也出行 |
| `list_courses` | 某专业**按条件**有哪些课 | `major, semester?, module?, required?, core_only?` | 课程列表（不含指标点） |
| `query_major_requirements` | 某专业**整体与模块学分要求** | `major` | 模块学分 + 总学分 + 核心/实践清单 |

**禁止**：把这些能力塞进一个带 `mode` 参数的大工具。理由：
① `mode` 是隐式枚举，模型要先把问题映射到枚举值，**多一层出错面**；
② 四个工具的参数表各自冻结、各自可测；合并后一次改动牵动全部；
③ 模型侧只需要"选对工具"，不需要"选对 mode + 填对该 mode 的参数"。

---

## 5B-1：结构化数据修复（Task 0–8）

### Task 0（阻断）：先修图渲染预览，交由用户裁决

**Files:** Create `_work/plan_db/sec8_preview/*.png`、`…/index.json`、`docs/阶段5B-先修图裁决.md`

- [ ] 用 PyMuPDF 把 63 份 PDF 的 §8 页裁剪渲染为 PNG。
- [ ] 导出该页文字层长度 / 矢量对象数 / 图片数 → `index.json`。
- [ ] 在 `docs/阶段5B-先修图裁决.md` 给足素材（图 + 数字），**不代替用户裁决**。
- [ ] **裁决前，先修图抽取一律不启动**；但 `provenance.estate8` 的状态标记照做（§6）。

### Task 1：源指纹与逐行哈希基建

**Files:** Create `.../query_course/scripts/plan_source.py`; Test `.../tests/test_plan_source.py`

- [ ] **Step 1 写失败测试**：同一行两次 `row_hash` 相同；改一字符后不同；折行口径与
      `corpus_fingerprint.read_markdown` 一致。
- [ ] **Step 2 跑，确认红。**
- [ ] **Step 3 实现** `row_hash(line)` 与 `source_fingerprint(src_dir)`。
- [ ] **Step 4 跑，确认绿。**

### Task 2：行级 provenance + 抽取器版本

**Files:** Modify `.../build_plan_db.py`; Test `.../tests/test_plan_provenance.py`

- [ ] **Step 1 写失败测试**：每条 course 都有 `src.row_hash`/`extractor.version`/`confidence.level`；
      `src.line` 指向行的 hash == `src.row_hash`。
- [ ] **Step 2 跑，确认红。**
- [ ] **Step 3 实现**；新增 `EXTRACTOR_VERSION` 常量与 `_work/plan_db/extractor_changelog.md`。
- [ ] **Step 4 跑，确认绿。**

### Task 3：三类质量门与复核队列

**Files:** Modify `.../build_plan_db.py`; Test `.../tests/test_plan_quality_gates.py`

- [ ] **Step 1 写失败测试**：① 硬失败行不进 `courses` 且 `rejected.json` **带原文与理由**；
      ② 模块学分对不上 → `warn` 不阻断；③ `low_confidence` 与 `quality.status` 一致；
      ④ **反例**：把"全部课程学分和 == 毕业学分"当门的实现必须**测失败**（禁止 4 的钉子）。
- [ ] **Step 2 跑，确认红。**
- [ ] **Step 3 实现** `quality` 三分类 + `review_queue.json` / `rejected.json`。
- [ ] **Step 4 跑，确认绿。**

### Task 4：修 F1（课程名冒号判据）

**Files:** Modify `.../build_plan_db.py:149`; Test `.../tests/test_plan_colon_names.py`

- [ ] **Step 1 写失败测试**：`模拟法学实践:模拟仲裁` 必须**接受**；
      `要求学分:21` / `要求子模块数:1` 必须**拒绝**。
- [ ] **Step 2 跑，确认红。**
- [ ] **Step 3 收窄判据**：只有**整格形如 `^(要求学分|要求子模块数)[:：]`** 才算汇总行。
- [ ] **Step 4 跑，确认绿。**
- [ ] **Step 5 重抽核对**：11 份文件课程数**只增不减且只增对应行**；审计 `dropped_by_name_colon` → 0。

### Task 5：修 F2（特判改内容键）与 F4（九节模块学分）

**Files:** Modify `.../build_plan_db.py`; Test `.../tests/test_plan_credit_fixes.py`、`test_plan_module_credits.py`

- [ ] **Step 1 写失败测试（F2）**：`(密码科学与技术, 030246)` 学分必须为 2.0；
      **且 `军事技能` 学分必须保持原文值**（钉住"修错行"）。
- [ ] **Step 2 实现**：`CREDIT_FIXES` 键 `(file, line)` → `(file, code)`。
- [ ] **Step 3 写失败测试（F4）**：8 份的「模块学分和 vs 声明总学分」差距被记录，
      **且不因此硬失败**（禁止 4）。
- [ ] **Step 4 重写九节抽取**并逐份复算；仍对不上的进**复核队列**（不猜）。
- [ ] **Step 5 跑，确认绿。**

### Task 6：格式族策略与特判台账

**Files:** Create `_work/plan_db/manual_overrides.json`、`extractor_changelog.md`; Test `.../tests/test_plan_overrides.py`

- [ ] **Step 1 写失败测试**：每条 override 有 `key/value/reason/evidence/author/date`；
      **且键里不含行号**（钉住 F2 的坏形态）。
- [ ] **Step 2 跑，确认红。**
- [ ] **Step 3 落盘现有 3 条特判**，把族 A–G 判据写进 changelog。
- [ ] **Step 4 跑，确认绿。**

### Task 7：独立课程金标准

**Files:** Create `测试集/course_gold.jsonl`; Test `.../tests/test_course_gold.py`

- [ ] **Step 1 建候选队列**（§7 三来源）→ `_work/plan_db/gold_queue.json`。
- [ ] **Step 2 **逐条回原文人核**（铁律 6）。**禁止从 plan_db 抄值。**
- [ ] **Step 3 写测试**：每条 `row_hash` 能在 `_md` 复算；`review_status == "human_verified"`；
      **且断言与 `训练集/**` 无逐字碰撞**（禁止 5 的前置）。
- [ ] **Step 4 跑，确认绿。**
- [ ] **Step 5 出基线**：当前 plan_db 在 gold 上的通过率（**被测量**）。

### Task 8：v2 输出、重建与回滚

**Files:** Modify `.../build_plan_db.py`; Test `.../tests/test_plan_db_schema_v2.py`

- [ ] **Step 1 备份** `plan_db.json` → `_work/plan_db/plan_db.pre-phase5b.json` + SHA-256。
- [ ] **Step 2 写失败测试（schema）**：`meta.schema_version == "plan_db/v2"`；每份有
      `quality`/`provenance`/`credits`（含 `estate8`）；每条 course 有 `src`/`extractor`/`confidence`。
- [ ] **Step 3 实现 v2 输出**（**旧字段原位保留**）。
- [ ] **Step 4 原子写 + 回滚说明。**
- [ ] **Step 5 跑，确认绿。**

### ★ Gate-1：5B-1 验收门（六条，全过才进 5B-2）

- [ ] **1. 63/63 文件处理完成** —— `meta.docs == 63`，每份 `courses` 非空。
- [ ] **2. 被拒收行全部有原始行和理由** —— `rejected.json` 每条含 `file`/`line`/`raw_line`/`reason`；
      **且行数 == `quality.hard_failures` 总数**。
- [ ] **3. 所有结构化课程都有来源** —— 每条 course 的 `src.row_hash` 能在 `_md` 复算成功；
      **不允许 `src` 缺失或 `row_hash` 为空**。
- [ ] **4. 数值异常不被静默修复** —— 任何改过原值的地方都在 `corrections` 里，
      且 `extractor_changelog.md` 有对应条目；**未被记录的改动数 == 0**。
- [ ] **5. 关系图无法恢复时明确标记不可用** —— 63 份 `provenance.estate8` **全部为三值之一**，
      无空值、无缺失（Task 0 裁决"不做"则全为 `unavailable_by_decision`）。
- [ ] **6. 高风险文件完成人工核对** —— §5 列出的 **21 份**逐份完成，结论写进
      `_work/plan_db/high_risk_review.md`（每份：读了哪几行、结论是什么、是否改了数据）。
- [ ] **7.（附加）兼容性** —— 采样 (专业, 课程) 重建前后 `query_course`/`compare_course` 返回逐字段相同。
- [ ] **8.（附加）金标准过线数字已出**（不设门槛，只报告）。

---

## 5B-2：扩展精确查询工具（Task 9–11）

> **前置**：Gate-1 全过。在此之前**不写任何 5B-2 代码**。

### Task 9：`list_courses(major, semester?, module?, required?, core_only?)`

- [x] **Step 1 写失败测试**：无过滤 → 返回该专业全部课程；`core_only=True` → 只返回 `is_core`；
      `semester="3"` → 只返回该学期；`module="学科基础课程"` → 按 `module_path` 匹配；
      **多条件叠加是 AND**；**空结果不报错，返回空列表 + `unresolved` 说明**。
- [x] **Step 2–6** 按仓库既有工具形态实现：`tool.py`（唯一公开入口）/ `schemas.py`（冻结 `PARAMETERS`）/
      `renderer.py`（观察文本）/ `tests/`；注册进 `registry`。
- [x] **Step 7** 更新 `test_five_tools_are_registered` 期望清单（5 → 7）；
      **确认 `training_schemas()` 仍是 4 个**，冻结训练列那条门仍绿。

### Task 10：`query_major_requirements(major)`

- [x] **Step 1 写失败测试**：返回 `total_credits` / `module_credits` / `core_courses` /
      `practice_courses`；专业名对不上时返回候选而非空。
- [x] **Step 2–5** 同上（schemas / tool / renderer / tests / registry）。
- [x] **Step 6** 观察文本与 `query_course` 的卡片**不要长成同一个样子**（避免模型混淆两个工具）。

### Task 11：5B-2 验收门

- [x] 四个工具**职责边界**测试：`list_courses` 不返回指标点；`query_course` 不返回全量清单；
      `compare_course` 仍对未开设专业出行。
- [x] **无 mode 参数**：断言四个工具的 `PARAMETERS` 里都不存在 `mode` 字段。
- [x] `registry.schemas()` 为 7 个；`training_schemas()` 仍为 4 个且逐字未变。
- [x] 全量测试：允许且只允许既有 `test_citations.py::test_block_suffix_…` 陈旧失败 + 1 skipped。
- [x] 更新 `docs/阶段5B-培养方案结构化审计.md`（补"处置"一节）与 `_work/plan_db/asset_inventory.md`。

---

## 明确不做

- **5B-1 期间不新增任何工具**（这正是本计划分两段的原因）。
- 不生成先修关系（除非 Task 0 用户裁决"做"，且只走矢量坐标法）。
- 不改 `测试集/` 既有文件（除新增 `course_gold.jsonl`）；5B-1 期间 `训练集/**` 只读。
- 5B-1 不改 `repository.py` / `schemas.py` / `registry.py` 任何契约；5B-2 只**加性**扩展。
- 不碰向量库、不碰服务器、不训练、不部署。
- 不为通过验收而放宽本计划的任何质量门。
