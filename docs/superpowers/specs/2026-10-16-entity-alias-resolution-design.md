# 课程名 / 专业名 / 校内术语的缩写与口语简称解析 —— 设计（Task 0 冻结稿）

> **状态：设计冻结稿，未实施。** 日期：2026-10-16
> **本轮不写代码、不改索引、不改语料、不改场景状态、不冻结资格考试包。**
> 本文件是 Task 1–8 的唯一设计依据；实施顺序见 §16。

---

## 1. 背景、目标与非目标

### 1.1 背景

校内提问大量使用缩写与口语简称：`计科`、`软工`、`高数`、`大英`、`形势与政策1`、`转专业的那个规定`。
当前系统的实体解析全部发生在**工具内部**，且**在多候选时静默取第一项**（`ms[0]` / `hits[0]`，见 §2）。
这带来两类风险：

- **静默选错**：`计科` 可能同时命中「计算机科学与技术」与「计算机类」相关专业；
  取第一项后返回的是一个**看似成功、实则错误对象**的结果，机械门不会报警。
- **无处澄清**：解析发生在工具内，图层不知道「这次调用其实有歧义」，
  于是既不追问、也不降级，直接把错误对象写进轨迹。

同时必须认清一条既有限制：`search_regulations` 的检索词扩展
（`query_expansions.json`）**不是实体别名表**，它扩展的是**路由主题词**而非**实体**（见 §2.5）。

### 1.2 目标

1. 解析**课程名、专业名、规章文档名及校内术语**的缩写/口语简称；
2. **不依赖穷举全部缩写** —— 未收录的简称要能被识别为 `unknown`，而不是被乱猜；
3. **不允许 LLM 绕过规范实体目录** —— LLM 只能提出候选，不能创造实体、不能单独决定唯一实体；
4. **一对多（one_to_many）不静默选错** —— 必须走澄清，不得取第一项；
5. **保留原问句与规范问句两份**，便于审计、复现与人审；
6. 支持**人工审核**与**隐私安全的学习闭环**（§11）。

### 1.3 非目标（本设计**不**做，后续 Task 也不得顺手做）

- 本任务**不实施代码**；
- **不重建** `school_docs_phase5`；
- **不修改** `_md/` 语料；
- **不复制**「简称课程」记录进培养方案数据；
- **不训练或微调**任何模型；
- **不冻结**资格考试包；
- **不解决** `tq-009` 的语料缺口（§13 末）；
- **不借本任务重构**无关模块。

---

## 2. 已有实现审计（如实记录现状）

> 以下均为**只读核验**结果（2026-10-16），发现不一致不自行修正，直接报告。

### 2.1 结构化工具的实体解析路径

四个结构化工具都经由同一份 `repository.py`：

| 工具 | 入口 | 解析链 |
|---|---|---|
| `query_course` | `query_course_result(major, course)` | `find_major` → `match_course` |
| `compare_course` | `compare_course_result(course, majors)` | 逐个 major 调 `query_course_result` |
| `list_courses` | 同族 | `find_major` |
| `query_major_requirements` | 同族 | `find_major` |

实现位置：`backend/agent/tools/query_course/scripts/repository.py`（**已核实**）。

### 2.2 `find_major()` / `match_course()` 行为

```text
find_major(name):
  1) join_key 归一后精确相等        → 命中
  2) join_key 子串包含              → 命中
  3) 都没命中 → difflib.get_close_matches(cutoff=0.3, n=3) 只作为**候选提示**返回

match_course(major, course):
  exact → prefix（后缀最短优先）→ substring → 空
  命中多个时按 len(name) 升序（后缀最短优先），其余进 alternatives
```

★ 关键点：**第 3 步的 difflib 结果目前只被当作"未命中时的候选提示"**，
但它在**第 1、2 步命中时完全不参与**；而第 1、2 步一旦命中多于一，就进入 §2.3 的静默取首项。

### 2.3 静默第一项（核心风险）

`repository.py` 中已核实存在 4 处：

```text
line  98:  m = ms[0]                 # find_major 命中多个 → 取第一个专业
line 103:  return _card(m, hits[0], how, hits)   # match_course 命中多个 → 取第一门课
line 116:  rows.append(_row(ms[0], course))      # compare_course 每个 major 同样取首项
line 135:  c = hits[0]               # _row 内同样取首项
```

**没有任何一处把「命中多个」这件事上报给上层。** 这是本设计要消除的第一号问题。

### 2.4 `search_regulations` 的分流

```text
tool.py: search_regulations_result(query, keywords)
  → retrieval.retrieve_frozen(query, trace, keywords)
  → build_query_plan(query, keywords, carry)
      ├ plan.semantic_query  → dense（embedding）路
      └ plan.sparse_query + plan.keywords → BM25（sparse）路
```

即：**自然语言语义路**与**关键词稀疏路**是分开构造的，两者可以喂不同的词。
这为 §7 的「BM25 同时保留原简称与合法规范候选词」留好了接入位，但**当前没有实体解析介入**。

### 2.5 `query_expansions.json` 与 `route_templates.json` 的当前职责

- `backend/data/route_templates.json`：路由模板（主题/意图词表）。
- `backend/agent/tools/search_regulations/data/query_expansions.json`：
  `{version, source, entries}`，`source = "backend/data/route_templates.json reviewed terms"`，**7 条**。

单条形状：

```json
{"canonical": "转专业", "aliases": ["换专业", "换个专业", "专业分流"],
 "domain": "student_status", "reviewed": true}
```

★ **必须点破的两件事**：

1. 它的 `canonical` 是**路由主题词**（`转专业`、`学业预警`…），**不是实体**
   （不是课程、不是专业、不是规章）—— 没有 entity id、没有 scope、没有年级。
2. 它的 `reviewed: true` 指的是**路由模板已复核**，**不是「实体别名已复核」**。

→ 所以 **`query_expansions.json` 不是正式实体别名表**（与用户核验项 5 一致），
**不得自动 grandfather 成正式别名表**（§5 硬门）。

### 2.6 `query_quality.py` 目前是离线审计，不是生产安全门

`audit_query_args()` 定义在
`backend/agent/tools/search_regulations/scripts/query_quality.py`。
**已核实：全仓库除自身定义外，只有 `tests/test_query_quality.py` 调用它** ——
它**没有接入生产 Registry / graph 路径**（与用户核验项 6 一致）。
因此它现在提供不了任何运行时保护。

### 2.7 `AgentState` 缺少统一 resolution envelope

`backend/agent/runtime/state.py` 的 `AgentState` 现有字段：`messages` / `tool_calls` /
`tool_results` / `evidence` / `steps_used` / `tool_calls_used` / `pending_clarification` /
`task_id` / `conversation_id` / `final_answer` / `question` / `history` / `owner_id` /
`resume_stage` / `calls` / `contexts` / `decision` / `executed` / `retries_used` / `rounds_done`。

**没有任何字段承载「这次问句解析成了什么实体、是否歧义、依据是什么」** ——
解析结论无处存放，自然也就无法在调用前被校验。

### 2.8 图注释与实际节点不一致

`backend/agent/runtime/graph.py:9` 的注释画了：

```text
└─ tools   → validate_calls → execute_tools → append_evidence → agent_decide
```

但**已核实实际注册的节点只有 6 个**：

```text
decide / tools / evidence / wrap_up / ppt / clarify
```

**不存在名为 `validate_calls` 的节点或函数**（全仓库搜索无命中）。
注释描述的是一个**尚未实现**的意图（与用户核验项 7 一致）。

### 2.9 ★ 不得夸大的地方

**切勿把 §2.5 的检索词扩展描述成「系统已具备实体解析能力」。** 它只做主题词扩展：
既不产出 entity id，也不参与结构化工具，更不解决多候选歧义。

---

## 3. 方案比较与裁决

| 方案 | 做法 | 裁决 |
|---|---|---|
| **A** | 每个工具内部各自解析 | **拒绝** |
| **B** | 新增公开 `resolve_entity` 工具 | **拒绝** |
| **C** | 共享解析器 + 图级前置解析 + 工具调用前强校验 + 结构化工具严格匹配 | **✅ 采用** |

**拒绝 A 的理由**：

1. 四个结构化工具会各复制一份简称逻辑 —— 不变量（§10 的唯一性判据）无法一处保证；
2. 解析发生在图看不到的地方 → 歧义**无法触发澄清**，只能静默选错（继承 §2.3 的病）；
3. 歧义判据、版本哈希、审计留痕要写在 N 个地方，必然漂移；
4. 与本项目既有教训一致：**「复制出来的迟早漂移」**（见检索参数单一来源的同类裁决）。

**拒绝 B 的理由**：

1. 把解析做成**模型可调用的公开工具**，等于把「实体真相」交给模型的调用决策 ——
   模型可以**不调用**它直接回答，也可以调用后忽略结果；
2. 工具的 schema 一旦公开就进入训练分布与冻结契约（本项目七工具 schema 是受保护资产），
   增删成本高且会污染既有分布；
3. 解析是**每次问句都要发生的前置步骤**，不是「模型按需选择的动作」；
4. 与目标 3「不允许 LLM 绕过规范实体目录」直接冲突 —— B 恰恰把绕过变成了默认路径。

**采用 C 的理由**：唯一真相源只有一份解析决策实现（§8.1），
图级前置节点保证**每次问句都经过它**，调用前强校验保证**歧义到不了工具**，
工具内部只保留 canonical 严格守卫。绕过的路径被数据结构堵死，而不是靠提示词叮嘱。

---

## 4. 唯一真相源与实体身份

### 4.1 真相源层级

```text
上游事实源（不可被本设计改写）
  ├ backend/data/plan_db.json            ← 专业 / 课程的结构化事实
  └ Phase 5 冻结规章文档清单              ← 规章文档的身份事实
        ↓ 确定性生成（带 provenance + 上游哈希）
运行时规范目录
  └ entity_catalog.json                  ← 只读、带哈希、可拒绝加载
```

### 4.2 五类实体身份

| 身份 | 定义 | 当前建议 |
|---|---|---|
| `major_entity_id` | 一个专业 | `grade + major_code`（例：`2025:081802`） |
| `course_entity_id` | **一个专业里的一门课**（课程实例） | `grade + major_code + course_code + src.row_hash` |
| `course_family_id` | 跨专业的同一门课 | **只包含真实的 `course_entity_id`**，本身不复制课程记录 |
| `regulation_entity_id` | 一份规章文档 | 由**稳定规范标题/文号**派生 |
| `institutional_term_id` | 校内术语（非课程非专业非规章） | 必须有**独立、已审核**的规范术语或术语族记录 |

**为什么课程实例要带 `src.row_hash`**：`course_code` 在全局并不唯一（§15 风险项），
同专业内也可能出现同名/同码重复；带上来源行哈希才能锚定到**具体那一条记录**。

**为什么规章不用 Qdrant `document_id`**：它是**可漂移**的索引内部编号
（重建索引即变），不能作为人工别名的长期目标。规章身份必须从**标题/文号**派生。

### 4.3 `entity_catalog.json` 的地位（★ 用户点名的细节二）

- 它是**运行时规范目录**，供 resolver 查询；
- 但它**必须**由 `plan_db.json` 与 Phase 5 冻结文档清单**确定性生成**；
- 其 manifest **必须记录上游哈希**（`plan_db` 的 `source_fingerprint` 等）；
- **上游变化后旧 catalog 必须拒绝加载**（`resolution_status=catalog_invalid`，
  不是「尽力而为地用」）；
- **它不得成为脱离上游来源的第二份事实** —— 不得手工往 catalog 里塞实体。

**Task 1 落地（2026-10-16）**：生成器与加载器同为 `backend/entity_catalog.py`
（`python -m entity_catalog --build`，需 `backend` 在 `PYTHONPATH`）；
只读产物落在 `backend/data/`（与 `plan_db.json` / `route_templates.json` / `ref_allowlist.json`
同类，该目录的 README 已声明为「运行时数据（人核产物，不是代码）」）：

```text
backend/data/entity_catalog.json             规范实体目录
backend/data/entity_catalog.manifest.json    manifest（含上游哈希，无环境字段）
backend/data/entity_catalog.collisions.json  join_key 碰撞审计 sidecar（**仅审计**）
```

**Task 1.1 落实的不变量（2026-10-16）**：

1. **上游不可绕过** —— `load()` 不传 `plan_db_path` / `corpus_root` 时**确定性定位仓库内正式上游并照常校验**；
   不保留「调用者不传就不校验」的语义，也**不提供** `skip_upstream` / `unsafe` / `force` 之类逃生参数。
2. **manifest 形状固定** —— 缺字段拒、**多字段拒**、`artifact_kind` / `schema_version` /
   `generator_version` 不符拒、`catalog.exists` / `audit.exists` 非 `true` 拒、
   `logical_path` 非约定值（含绝对路径、换文件名）拒。
   ★ 本轮**不增加版本兼容层**。
3. **三方自洽** —— catalog 文件 SHA == `manifest.catalog.sha256`；collisions sidecar 文件 SHA ==
   `manifest.audit.sha256`；`manifest.catalog.rows` == 实际行数；`manifest.counts` 各项 == 实际计数；
   `manifest` 的五项审计指标 == `catalog.audit_metrics`；sidecar 组数 == `join_key_collision_groups`；
   sidecar 每组 ≥2 个**不同**正式名、名称确实存在于 course 实体、**不得造成任何合并**。
   sidecar 路径**只**按 manifest 的受限逻辑路径在 catalog 同目录确定性定位，**不接受任意路径跳转**。
4. **ID 派生自洽** —— 不只查「是否重复」，还查 ID 是否由行内身份字段**正确派生**；
   身份字段不得为 `null`/空串；`regulation.relpath` 不得逃出语料目录；其文件 SHA 必须与当前上游一致。
5. **build 先验证、后落盘** —— 静态验证（身份完整性、ID 唯一与自洽、计数、跨文件一致性、序列化可行性）
   **全部在写盘之前**完成；失败时**不建目录、不写正式文件、不留 `.tmp`**，
   不得「先写无效 catalog 再靠 load 拒绝」。落盘走 staged 临时文件 + 固定顺序 replace，**manifest 最后**。
**Task 1.1.1 落实的不变量（2026-10-16）** —— 写盘失败边界与嵌套契约：

6. **stage 临时路径先入可清理集合、再写** —— 部分写入后抛错也必须能清理该 `.tmp`，
   不得留下无人认领的半成品。
7. **stage 与 replace 失败必须区分报告** —— stage 失败明确写「尚未 replace 任何正式文件」并列出
   已清理的 `.tmp`；replace 失败按**固定顺序**（catalog → audit → manifest）列出已替换 /
   未替换正式文件与已清理 / 未清理临时文件。**未发生的清理不得谎报**。
8. **不做伪事务回滚** —— 已 replace 的正式文件保持新版，只清理未替换的 `.tmp`；
   失败一律以保留原始异常于 `__cause__` 的方式抛出。
9. **嵌套形状固定** —— `manifest.counts`（五键）、`manifest.catalog` / `manifest.audit` 块、
   `manifest.upstream.plan_db` / `upstream.corpus` 块、`catalog` 顶层、`catalog.audit_metrics`
   与 collisions sidecar 全部**缺字段拒、多字段拒、类型错拒**；SHA 必须是 64 位**小写**十六进制。
10. **sidecar 的 `join_key` 必须语义正确** —— 组内名称逐一执行**当前正式**
    `normalize.join_key` 必须等于 `group.join_key`（**不复制第二份规则**）；
    `join_key` 不得重复、`names` 内部不得重复、同一正式名称不得被两个组重复收录、
    组内名称不得归一到不同键。
11. **一律 `CatalogError`** —— 缺文件、畸形 JSON、嵌套类型错、上游路径不存在都不得裸露
    `KeyError` / `TypeError` / `FileNotFoundError` / `JSONDecodeError`。

★ 本轮**不增加**版本兼容层，**不增加** `force` / `unsafe` / `skip` / `trusted` 逃生参数。

### 4.4 上游事实源

`plan_db.json` 的 `meta.source_fingerprint` 已存在
（实测：`3b7db64888b8cc17ca70f77cf43c1f493437afd24ef2b96a86c25536f074cff5`），
这就是 catalog manifest 要钉的上游哈希。

### 4.5 计数口径（Task 0 人工裁决，2026-10-16）

五条口径**不得混用**：

1. **不同正式课程名 = 2,116**，口径是 `backend/data/plan_db.json` 中课程名称的**逐字精确去重**；
2. **7,212 是课程记录 / 课程实例数**，与 2,116 **不是一回事**，不得互换；
3. `join_key` 归一化后的 **2,103 只作名称碰撞审计指标** —— 不得替代正式课程名计数，
   **不得据此合并或改写规范名称**；
4. **`course_family` 数量不预设为 2,116**，**不得只因课程同名就自动合并**；
   课程族契约与审核归属**留到 Task 2**，Task 1 **不生成** `course_family`、**不推断**课程族；
5. `course_entity_id` 必须**先过唯一性门再报数** —— **不得为了凑 7,212 放宽唯一性门**。

★ 2,103 与 2,116 的差额来自 13 组归一化碰撞（如 `Android 软件开发技术` / `Android软件开发技术`）；
碰撞**只用于审计**，不构成任何合并依据。

---

## 5. `AliasRecord` 契约

```text
alias_id           稳定身份（规范字段派生，改文案不漂移）
alias              用户可能写的缩写/口语简称
entity_type        major | course | regulation | institutional_term
canonical          规范实体 id —— **仅 one_to_one 可填**
canonical_family   规范实体族 id —— **仅 one_to_many 可填**
mapping_type       one_to_one | one_to_many
scope              粗粒度适用范围（grade / major_code / 学期 等）
review_status      pending | reviewed | rejected
reviewed_by        人类审核者标识
review_note        审核备注
```

### 5.1 硬门（不满足即拒，不得"从宽处理"）

1. `one_to_one` **只能**填 `canonical`（填了 `canonical_family` 即非法）；
2. `one_to_many` **只能**填 `canonical_family`（填了 `canonical` 即非法）；
3. `canonical` / `canonical_family` 指向的实体或族**必须存在**于 catalog；
4. `pending` / `rejected` **一律不进入生产解析**；
5. **已 `reviewed` 的映射不得被后写覆盖** —— 冲突必须走人工裁决，不得静默改；
6. `alias` 与**任何正式实体名称**冲突时必须拒绝（简称不许遮蔽正式名）；
7. **现有 `query_expansions.json` 不得自动 grandfather 为正式别名表** ——
   必须**单独迁移 + 单独审核**（见 §2.5：它的 canonical 是路由主题词，不是实体）。

### 5.2 Task 2 落地：别名 / 实体族契约（2026-10-16）

模块 `backend/entity_aliases.py`；正式 sidecar 与规范目录**同址**落在 `backend/data/`：

```text
backend/data/entity_aliases.jsonl              别名行（**本轮 0 行、零字节**）
backend/data/entity_families.jsonl             实体族行（**本轮 0 行、零字节**）
backend/data/entity_aliases.manifest.json      两者共用 manifest（含 catalog 绑定）
```

**本轮没有任何简称被人工批准** —— 两个 JSONL 都是 0 字节
（`sha256 = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`）；
`高数` / `毛概` / `大物` / `计科` / `软工` 只是**需求示例**，不得自动晋升，
也不得从 `query_expansions.json` 迁移。

**身份派生**（改文案不漂移；成员 / 目标一变就是新身份）：

```text
family_id = "family:" + sha256({entity_type, sorted(members)} 的规范 JSON)[:32]
alias_id  = "alias:"  + sha256({join_key(alias), entity_type, mapping_type,
                               目标（canonical 或 canonical_family）, scope} 的规范 JSON)[:32]
```

`review_note` / `reviewed_by` / `canonical_label` / 时间戳 / 行号**都不参与**身份；
「同一成员集合以不同 id 重复登记」由**派生自洽门 + 重复 id 门**共同拒绝。

**scope 契约**：必须是对象（可为 `{}`），只允许 `grade` / `major_code`（非空字符串）
与 `semester`（正整数，**bool 不得当整数**）；未知键拒；序列化走规范 JSON（键序固定）。
scope **只限制适用范围，不创建实体**，也**不做上下文收敛**（那是 Task 3）。

**正式名称冲突判据（★ 本轮定死）**：**不另写一份归一规则** —— 四类实体统一复用当前正式
`normalize.join_key`（`repository.find_major` / `match_course` 用的就是它，两者同源），
并**按全实体目录检查，不只看同类型**。只要 `join_key(alias)` 等于任一正式实体名，一律拒绝，
包括：

- 别名与正式名**逐字相同**；
- 别名**只因空格差异**与正式课程名冲突；
- 别名与正式名冲突**但指向同一实体**（用户本来就能写正式名，不需要别名）；
- 别名撞上**另一个 entity_type** 的正式名（例如 course 别名撞上 major 名 / 规章标题）。

**生产视图与审计视图**：`load_alias_bundle()` → `reviewed_aliases()` / `reviewed_families()`
**只**返回 `reviewed`；`audit_aliases()` / `audit_families()` 是**名称明确的独立审计接口**，
不与生产接口混用。**不存在** `include_pending` / `unsafe` / `force` / `skip_review` 之类绕过参数。
加载失败一律抛专用 `AliasCatalogError`，**不得**伪装成 `no_evidence` / `irrelevant_result` /
`tool_failed`。

**对 §5.1 硬门 3 的一处解释（本轮按字面最严执行）**：`one_to_many` 指向的族**必须**是
`reviewed` —— 该门对**任何状态**的别名行都生效，pending / rejected 的别名也不得指向
未经审核的族（族必须先被审过）。若后续要允许「待审别名指向待审族」，须先经人工裁决。

**实体族不得自动生成**：同名、`join_key` 相同、字符相似**都不建族**；
本轮没有任何按名称自动建族的代码路径，由测试（`test_families_are_never_derived_from_names`）锁定。

---

## 6. `ResolutionResult` 契约

> ★ **Task 3.0 收口**：本节的**字段表**已被 **§17.2**（`QueryResolutionEnvelope` +
> `MentionResolution` + `EntityCandidate` + `SelectedEntity`）**取代**；§6.1 的 7 个状态
> 在 **§17.2.7** 扩展为 8 个。§6.2「必须写死的口径」**继续有效**。

```text
original_query        用户原话（原样保留，不覆盖）
normalized_query      仅把**已确认实体**替换为规范名后的问句
detected_aliases      实际识别到的简称列表（含来源通道）
entity_candidates     候选实体列表（含各通道得分与来源，仅用于排序）
selected_entity       最终选中的规范实体（无唯一结论时为空）
resolution_status     见下
requires_clarification 布尔
catalog_sha256        catalog 版本留痕
aliases_sha256        别名表版本留痕
resolver_version      解析器版本
```

### 6.1 状态枚举

```text
exact_canonical        问句里本来就是规范名
resolved_reviewed_alias 由**已审核**别名唯一解析
resolved_by_context    由年级/专业/学期等**上下文**唯一收敛
ambiguous              存在多个合理候选，无法唯一
unknown                没有候选
scope_conflict         候选存在但 scope 与上下文冲突
catalog_invalid        catalog 缺失/哈希不匹配/上游漂移
```

### 6.2 必须写死的口径

- **相似度只排序候选，不赋予真相** —— 字符相似、embedding 相似、LLM 打分都只是候选生成器；
- **LLM-only 单候选仍不得自动选中** —— 「只有一个候选」不等于「它是对的」；
- **top-1 不能绕过 runner-up 歧义门** —— 必须做亚军检查（§10）；
- **`normalized_query` 只替换已确认实体**，未确认部分原样保留；
- **`selected_entity` 为空时，不得调用要求唯一实体的结构化工具**（§9）。

---

## 7. 数据流

```text
用户问句
   │
   ▼
[original_query] ────────────────────────────────┐（原样保留，进审计与留痕）
   │                                              │
   ▼                                              │
┌──────────────────────────────────────────┐      │
│ 图级节点 resolve_entities                │      │
│  · 单一 resolver 决策实现                │      │
│  · 多通道候选生成（§10）                 │      │
│  · scope 硬过滤 + 上下文一致性            │      │
│  · runner-up 歧义检查                     │      │
│  · 版本/哈希留痕                          │      │
└──────────────────────────────────────────┘      │
   │                                              │
   ▼                                              │
[ResolutionResult] ───────────────────────────────┤
   │                                              │
   ▼                                              │
┌──────────────────────────────────────────┐      │
│ 独立节点 validate_calls                  │      │
│  · 结构化工具：selected 必须唯一，否则拦下 │      │
│  · ambiguous/unknown → clarification      │      │
│  · 不伪装成 no_evidence / irrelevant /    │      │
│    tool_failed                            │      │
└──────────────────────────────────────────┘      │
   │                                              │
   ├──► 结构化工具（query_course / compare_course / │
   │     list_courses / query_major_requirements）  │
   │     · 只接收 canonical，内部只做严格守卫       │
   │                                              │
   └──► search_regulations ◄──────────────────────┘
         · embedding/reranker 用**自然化后的规范问句**
         · BM25 同时保留**原简称**与**合法规范候选词**
         · one_to_many 在此**允许扩展召回**，但不得声称已唯一确定实体
```

**留痕要求**：工具执行记录**同时保留 `raw_args` 与 `canonical_args`**；
最终回答应显示「**按正式名称 X 查询**」，让用户能发现解析错误。

---

## 8. 图与工具边界

### 8.1 「唯一解析入口」的准确含义（★ 用户点名的细节一）

「唯一解析入口」**不是**「只允许一个调用点」，而是：

1. **只有一份 resolver 决策实现**（一个模块，一份逻辑）；
2. 图级查询解析（`resolve_entities`）与调用前验证（`validate_calls`）**共同复用它**；
3. **工具内部只能做 canonical 严格守卫**（输入必须是已确认的规范实体 id / 规范名），
   **不得复制解析逻辑**。

这样既保证了「决策唯一」，又不强迫所有调用都走同一个物理函数。

### 8.2 四项结构要求

1. 图级新增 `resolve_entities` 节点；
2. 新增**独立** `validate_calls` 节点（把 §2.8 那条只在注释里的意图真正落地）；
3. Registry 只做**最终 canonical 守卫 + 执行**（不做解析、不做消歧）；
4. `repository` **取消多候选取第一项**（§2.3 的 4 处）；
5. 四个结构化工具**不得各复制简称逻辑**。

### 8.3 `ambiguous` / `unknown` 的处理口径

- 在**调用工具之前**进入 `clarification`；
- **不得伪装成 `no_evidence`**（那等于说"学校没规定"）；
- **不得伪装成 `irrelevant_result`**；
- **不得伪装成 `tool_failed`**；
- **本轮不新增 `ToolStatus`** —— 五状态不变，歧义走澄清而非新状态。

### 8.4 为什么「只在工具内部加解析」不够（绕过风险）

- 模型**可以绕过工具直接回答**（尤其 no-tool 路径）：工具内的解析再严，也管不到「不调用工具」这条路；
- 工具内解析**没有图级视野**：拿不到年级/专业/学期上下文，消歧质量天然更低；
- `compare_course` 会**多次调用** `query_course`：工具内解析会让同一次问句在不同子调用里
  解析出**不同实体**，且无人发现；
- 歧义必须在**产生工具调用之前**被拦下，否则 `tool_calls` 已经写进轨迹，
  `evidence`/`budget` 已经计费。

---

## 9. `one_to_one` / `one_to_many` / `unknown` 决策表

> ★ **Task 3.0 收口**：本表已被 **§17.8 的可执行决策表**取代（15 行 + 3 行实测补充，
> 每行给出候选通道 / 确定性支持 / 是否允许 selected / 状态 / 是否澄清 / 是否替换 /
> 是否允许结构化调用 / 是否允许规章扩展召回）。本节保留作历史记录。

| 场景 | 目录存在 | scope 一致 | 上下文过滤 | 确定性支持（非 LLM） | 候选数 | 允许 selected | 调用结构化工具 | 扩展规章召回 | 最终动作 |
|---|---|---|---|---|---|---|---|---|---|
| **reviewed one_to_one** | ✓ | ✓ | — | ✓（已审核别名） | 1 | **是** | **是** | 可 | 直接规范化并查询 |
| **reviewed one_to_many** | ✓ | ✓ | 收敛到 1 | ✓ | 1（族收敛） | 是 | 是 | 可 | 规范化并查询；**保留族留痕** |
| **reviewed one_to_many** | ✓ | ✓ | 未收敛 | ✓ | >1 | **否** | **否** | 可 | **澄清**（列出族成员） |
| **unknown + 上下文唯一** | ✓ | ✓ | 收敛到 1 | ✓（年级/专业/学期） | 1 | 是 | 是 | 可 | `resolved_by_context`，规范化查询 |
| **unknown + 多候选** | ✓ | 部分 | 未收敛 | ✗ | >1 | **否** | **否** | 可 | **澄清**（`ambiguous`） |
| **unknown + 无候选** | ✗ | — | — | ✗ | 0 | **否** | **否** | 仅原词 | **要求完整名称**（`unknown`） |
| **LLM-only 单候选** | ✓ | 未验 | — | **✗（无确定性支持）** | 1 | **否** | **否** | 可 | **澄清**，不得自动选中 |
| **catalog 孤儿** | ✗ | — | — | — | — | **否** | **否** | 仅原词 | **`catalog_invalid` → 停止并报告** |
| **scope 冲突** | ✓ | ✗ | — | — | — | **否** | **否** | 仅原词 | **`scope_conflict` → 澄清** |

★ 表中「允许 selected = 否」的每一行，都**必须**在 `validate_calls` 处被拦下，
而不是依赖工具自己发现。

---

## 10. 动态候选与唯一性判据

### 10.1 候选可以来自（多通道合并）

- 规范实体 **exact match**；
- **已审核 alias**（`reviewed`）；
- 字符 / 关键词相似（`difflib`、归一化子串）；
- **embedding** 相似；
- **专业 / 年级 / 学期上下文**；
- 培养方案中**实际存在的实体**；
- **LLM candidate proposal**（只能**提议**）。

### 10.2 最终唯一性**不是**（必须写死）

- ❌ `top1`；
- ❌ 最高分；
- ❌ 模型说「我认为是」；
- ❌ 只有一个候选被召回。

### 10.3 至少要经过的步骤

> ★ **Task 3.0 收口**：第 5 步「runner-up 歧义检查（亚军与冠军差距不足 → `ambiguous`）」
> 已被 **§17.6 的离散证据等级规则**取代 —— **首版 resolver 不含任何数值相似度阈值**，
> 「冠军 / 亚军差距」这种判据不得实现。第 1、2、3、4、6、7 步仍然有效。

1. **规范目录存在性**检查（不在 catalog 里的不能选）；
2. **多通道候选合并**（记录每个候选来自哪些通道）；
3. **scope 硬过滤**（年级/专业对不上的直接出局）；
4. **上下文一致性**（与已知的年级/专业/学期相容）；
5. **runner-up 歧义检查**（亚军与冠军差距不足 → `ambiguous`）；
6. **LLM-only 禁止自动选中**（无任何确定性支持的候选，即使唯一也不自动选）；
7. **版本与哈希留痕**（`catalog_sha256` / `aliases_sha256` / `resolver_version`）。

---

## 11. 学习闭环与隐私

- `unknown` / `ambiguous` 的问句可以进入**待审核队列**；
- **不根据一次 LLM 猜测晋升**任何别名；
- **只统计高频稳定表达**（低频、易变的不进队列）；
- 正式晋升**必须**满足 `review_status=reviewed` **且**有 `reviewed_by`；
- **默认不保存完整原始问句**；
- **删除或哈希**姓名、学号、班级、联系方式等个人信息；
- 只记录：`alias`、`entity_type`、**粗粒度** `scope`、候选集合、出现次数；
- **待审核队列不是生产别名表** —— 队列中的任何条目在 reviewed 之前不参与解析。

---

## 12. 对现有系统的影响

| 对象 | 影响 | 裁决 |
|---|---|---|
| 四个结构化工具 | 内部只保留 canonical 严格守卫；不再多候选取首项 | 改（Task 4） |
| `search_regulations` | 接入规范化问句 + BM25 保留原简称与合法候选词 | 改（Task 5），**不动阈值/排序/召回池/精排池/gate** |
| `AgentState` / 运行图 | 新增 resolution envelope 字段 + `resolve_entities` / `validate_calls` 两节点 | 改（Task 4） |
| evidence binding | 解析结果**可能**改变**新**场景的 binding | **不得静默改旧 binding** |
| duplicate / budget key | 改用 **canonicalized args**，避免「同一实体不同写法」被当两次调用 | 改（Task 4） |
| tool schema | **七工具 schema 维持不变** | **不变** |
| Phase 5 索引 | **`school_docs_phase5` 不重建** | **不变** |
| BM25 词表 | `bm25_vocab_phase5.json` 不动 | **不变** |
| 现有正式训练资产 | **不重写** | **不变** |
| 后续新训练数据 | 需携带 raw + canonical 双份留痕 | Task 4 后定 |
| 资格考试包 | 见 §13 | 增量 |

**必须同时满足**：七工具 schema 不变；`school_docs_phase5` 不重建；`_md` 不修改；
现有正式训练资产不重写；canonicalized args 用于 duplicate/budget key；
**原参数与规范参数同时留痕**；解析结果可能改变新场景 binding，但**不得静默改旧 binding**。

---

## 13. 资格考试增量设计

> ★ **Task 3.0 收口**：四条机制题与「确定性支持」的对应、以及各自的前置条件，
> 见 **§17.10**。本节其余口径（总数上限、先 pending、不提前冻结、tq-009/tq-010 独立）
> **继续有效**。

现有 **26 条不动**。本设计只提出**最多 4 条机制题**：

| # | 机制 | 预期 |
|---|---|---|
| **A** | reviewed `one_to_one` → 直接规范化 | 结构化工具 1 次，参数为规范实体 |
| **B** | reviewed `one_to_many` → 必须澄清 | **工具调用 0 次** |
| **C** | unknown + 专业/年级/学期上下文唯一 → 允许规范化查询 | 结构化工具 1 次 |
| **D** | unknown 且不能唯一 → 要求完整名称 | **工具调用 0 次** |

**口径**：

- 文案目前只是**候选**，**不提前把「计科」等映射标成 `reviewed`**；
- 实施完成后总数**最多 30**；
- **A / C 需要真实环境双轮重放**；
- **B / D 预期工具调用 0 次**，但仍需**双轮运行图验证**（验证「确实没调用」）；
- 四条**全部先保持 `pending`**，有 rubric，**人工审核后**才能 `reviewed`；
- **不得提前冻结**；
- `tq-009` 与 `tq-010` 仍作为**独立未决问题**处理（不在本设计内解决）。

---

## 14. 不需要重构的内容

- `_md/` 语料；
- `school_docs_phase5`；
- Phase 5 的分块、embedding、reranker；
- `bm25_vocab_phase5.json`；
- `plan_db` 的既有抽取流程；
- 七工具公开 schema；
- `ToolStatus` 五状态；
- evidence binding 算法；
- qualification compiler / promotion 链；
- 现有正式训练资产；
- base / LoRA 结构。

---

## 15. 风险与停止条件

| # | 风险 | **可执行的停止条件** |
|---|---|---|
| 1 | catalog 上游指纹不一致 | `catalog.manifest.upstream_fingerprint != plan_db.meta.source_fingerprint` → **拒绝加载，`catalog_invalid`，停止并报告**，不得降级使用旧 catalog |
| 2 | alias 孤儿或冲突 | 任一条 alias 的 `canonical`/`canonical_family` 不在 catalog → **拒绝该条并停止**；alias 与正式实体名冲突 → **拒绝该条并停止** |
| 3 | 课程代码不能全局唯一 | 建立 course 身份时若 `grade+major_code+course_code` 在同专业内不唯一 → **必须加 `src.row_hash`**；仍不唯一 → **停止并报告** |
| 4 | 同专业重复课程代码/名称 | 解析到多条同码同名记录 → **`ambiguous`，澄清**，不得取首项 |
| 5 | 动态候选假唯一 | 若唯一性只由 LLM 或单一相似度通道支持 → **不得 selected**（§10.2） |
| 6 | embedding 版本漂移 | `embed_model` 或 catalog 版本变化 → 旧 `catalog_sha256` 失效 → **拒绝加载并报告** |
| 7 | LLM 直接回答绕过 | 图路径上若存在「未解析即调用工具」或「未解析即回答」的通道 → **该通道必须被 `validate_calls` 拦下**；拦不下就**停止并报告** |
| 8 | 多工具调用部分执行 | `compare_course` 多 major 解析若部分成功 → **整次调用拦下**，不得部分执行 |
| 9 | 年级扩展后一对一变一对多 | 新增年级导致某 `one_to_one` alias 实际命中多实体 → **该 alias 自动降级为需澄清，并进审核队列** |
| 10 | 待审核队列隐私 | 队列中出现未脱敏的姓名/学号/班级/联系方式 → **写入前拦截，停止并报告** |
| 11 | 旧 query expansion 误当正式 alias | 发现任何代码路径把 `query_expansions.json` 当实体别名表读取 → **停止并报告**（§5 硬门 7） |
| 12 | 工具 schema 或训练分布被无意改动 | 七工具 schema 或既有正式训练资产 SHA 变化且非本 Task 授权 → **停止并报告** |

★ 停止条件一律写成**可执行判据**（比较、拒绝、报告），不是提醒语。

---

## 16. 后续 Task 拆分（**本轮不实施**）

> 每个 Task 完成后**立即停止**，提交验收，**不得提前进入下一 Task**。

### Task 1：规范实体目录
- **允许修改**：新增 catalog 生成脚本 + `entity_catalog.json` + manifest；`docs/WORK_PROGRESS.md`
- **不做**：不改 `plan_db`、不改 `_md`、不接运行图、不改工具
- **红灯**：上游指纹不匹配仍能加载；缺 provenance；entity id 不稳定（两次生成不同）
- **绿灯**：同输入两次生成逐字节一致；上游改动后拒绝加载；实体数可核对
- **验收字段**：catalog SHA、manifest 上游哈希、五类实体计数
- **受保护**：`plan_db.json`、`_md`、Phase 5 索引、正式训练资产
- **停点**：完成后停止

### Task 2：别名和实体族契约
- **允许修改**：`AliasRecord` / family 模型与校验；sidecar；`docs/WORK_PROGRESS.md`
- **不做**：不接运行图、不改工具、不迁移 `query_expansions.json`
- **红灯**：one_to_one 填 family 被放过；pending 进生产；孤儿 canonical 被放过；冲突后写覆盖被放过
- **绿灯**：§5.1 七条硬门逐条有红灯与绿灯
- **验收字段**：别名条数、各硬门用例数、`aliases_sha256`
- **受保护**：同 Task 1 + `query_expansions.json` **不得被改**
- **停点**：完成后停止

### Task 3：纯解析器

> ★ **Task 3.0 收口**：本 Task 的**更细**边界见 **§17.9（工具映射）/§17.11（允许与禁止）**；
> 与本节重叠处，以 §17.11 为准。本节其余内容不变。
- **允许修改**：resolver 模块 + 测试；`docs/WORK_PROGRESS.md`
- **不做**：不接运行图、不改工具、不查 Qdrant、不调 LLM
- **红灯**：top-1 直接选中；LLM-only 自动选中；无 runner-up 检查；normalized 替换了未确认片段
- **绿灯**：§9 决策表逐行用例；§10.3 七步逐步可测
- **验收字段**：`ResolutionResult` 全字段、状态枚举逐项覆盖
- **受保护**：全部上游资产
- **停点**：完成后停止

### Task 4：运行图接入与结构化工具安全门
- **允许修改**：`state.py` 增字段、`graph.py` 增两节点、四工具 + `repository.py` 去首项取法、测试
- **不做**：不改七工具 schema、不新增 ToolStatus、不改检索参数
- **红灯**：ambiguous 仍到达工具；歧义被伪装成 no_evidence/irrelevant_result/tool_failed；
  duplicate/budget key 仍用 raw args
- **绿灯**：§9 表中「不允许 selected」的行全部被拦；raw + canonical 双留痕
- **验收字段**：拦截用例数、双留痕字段、schema SHA 未变
- **受保护**：七工具 schema、正式训练资产、`_md`、Phase 5 索引
- **停点**：完成后停止

### Task 5：embedding / BM25 查询接入
- **允许修改**：`search_regulations` 查询构造与测试
- **不做**：不改阈值/排序/召回池/精排池/gate/trace、不改词表、不重建索引
- **红灯**：规范问句替换掉原简称；one_to_many 被当成已唯一确定；BM25 丢掉原简称
- **绿灯**：embedding 用规范问句、BM25 同时含原简称与候选词、两轮 A/B 可复现
- **验收字段**：A/B 结果、binding 说明
- **受保护**：索引、词表、语料、检索参数
- **停点**：完成后停止

### Task 6：去标识化学习闭环
- **允许修改**：队列落盘与脱敏模块 + 测试
- **不做**：不自动晋升任何别名
- **红灯**：写入未脱敏个人信息；单次 LLM 猜测进入队列；队列被当生产别名表读取
- **绿灯**：脱敏用例全过；晋升必须 reviewed + reviewed_by
- **验收字段**：队列条数、脱敏字段清单
- **受保护**：全部上游资产
- **停点**：完成后停止

### Task 7：资格考试四条增量场景
- **允许修改**：`qualification.scenarios.draft.jsonl` / `rubric.draft.jsonl` / `review.md` / `WORK_PROGRESS.md`
- **不做**：不冻结、不改 `review_status`、不改既有 26 条
- **红灯**：B/D 意外调用了工具；A/C 没走规范化
- **绿灯**：A/C 真实双轮重放；B/D 工具调用 0 次且双轮验证；总数 ≤ 30
- **验收字段**：四条 binding、两轮漂移数
- **受保护**：正式五资产、七个正式种子、`review.md` 之外的资格输入
- **停点**：完成后停止

### Task 8：人工审核与冻结裁决
- **允许修改**：`review.md`、`WORK_PROGRESS.md`
- **不做**：不自动 reviewed、不自动冻结
- **红灯**：未经人工即改 `review_status`
- **绿灯**：逐条人工裁决留痕；`reviewed_by` 齐备
- **验收字段**：裁决条数、冻结前 SHA
- **受保护**：同上

### §17.17 运行图接入、检索双通道与审计修订（2026-10-16 实施记录）

1. 主图在首次 `decide` 前只构造一次 `QueryResolutionEnvelope`；解析器构造失败直接停止，
   不降级为普通回答或工具失败。
2. 结构化调用在 Registry 前统一经过 `entity_calls.validate_calls`；只有 `allow` 的
   `canonical_args` 能进入 duplicate/budget/Registry/evidence 流程，调用日志同时保留
   `raw_args` 与 `canonical_args`。
3. `search_regulations` 仅扩展已经 `selected` 的确定性实体：模型沿用原问句时，dense
   query 使用 `normalized_query`；BM25 保留既有关键词并追加原简称与规范名。T4–T7
   未决候选不扩展，不改检索阈值、池大小、reranker、gate、索引或词表。
4. 待审核队列只记录未决提及、粗粒度 scope、候选集合、次数与 resolver/catalog 哈希；
   不保存完整原问句，不含正式 AliasRecord 的审核/映射字段，手机号、邮箱、带标签的
   学号/姓名/班级在写盘前直接拒绝。
5. 已晋升 target 的场景参数变化走显式 revision：新 ledger 行以
   `supersedes_target_sha256` 指向上一版 target，活动 target 必须等于修订链末端。
   普通 promote 仍不得覆盖已有 target。
6. 最终 `done` 事件保留既有 `contexts` / `calls` / `n_refs` / `decision`，并追加
   `call_validations` 审计字段；被澄清或拒绝的调用不会伪装成已执行的 `calls`，但其
   raw/canonical 参数、绑定与决策仍可在回合结束时复核。

已知边界：对普通模型文本回答，不能仅凭 T4 弱候选全局强制澄清，否则常规问句会被
高召回 T4 误拦；本轮把强制安全门放在结构化调用边界。正式五资产的全量重编译依赖
`school_docs_phase5` 真环境，服务器关闭时不得用旧 output/manifest 冒充已收口。
- **停点**：完成后停止，等用户裁决

---

## 附：本设计明确**不**主张的事

- 不主张 `top-1` 可直接选中；
- 不主张 LLM 可以决定唯一实体；
- 不主张需要重建 `school_docs_phase5`；
- 不主张修改 `_md` 加别名；
- 不主张新增公开 `resolve_entity` 工具作为最终方案；
- 不主张用 `query_expansions.json` 充当实体别名表。

---

## 17. Task 3.0：纯解析器接口与唯一性判据收口（2026-10-16）

> **性质：纯设计。** 本轮**未写任何 resolver 代码**、未新增测试、未改任何资产。
> 本节是 Task 3 / Task 4 的**唯一接口依据**；它**取代** §6 的字段表、§6.1 的状态枚举、
> §9 的决策表、§10.3 的 runner-up 检查。§10.2「最终唯一性**不是**什么」的清单**继续有效**。
> 全节判据均以**磁盘代码与当前正式目录的实测**为准（测量命令见 §17.12）。

### 17.1 单问句多实体：A / B / C 比较与裁决

| 方案 | 做法 | 裁决 |
|---|---|---|
| **A** | 一个 `ResolutionResult` 承载整个问句，`selected_entity` 改成列表 | **拒绝** |
| **B** | `ResolutionResult` 表示**一个实体提及**；顶层加 `QueryResolutionEnvelope`（mentions / resolutions 列表） | **✅ 采用** |
| **C** | resolver 只按单个工具参数调用，不做问句级 envelope | **拒绝** |

**拒绝 A**：四个结构化工具的参数形状根本不同 —— `compare_course` 要
**一个 course + 一个 ≥2 元素的 majors 列表**（`schemas.py` 实测），`list_courses` 只要一个 major。
把 `selected_entity` 拉平成一个列表后，「哪一项对应哪个参数」「哪一项是课程哪一项是专业」
**没有任何地方记得住**；一处 ambiguous、一处 resolved 时也无从表达；`normalized_query`
的替换会失去位置锚点（只剩字符串替换，必然误伤）。它恰好丢掉了 Task 4 唯一需要的东西。

**拒绝 C**：解析发生在**选工具之前**（图级前置），resolver 不掌握「即将调用哪个参数」。
按参数调用等于：① 同一段问句被解析 N 次，§8.1「只有一份决策」退化成「一份实现、N 次决策」；
② `compare_course` 会**多次**调用 `query_course`，同一问句在不同子调用里可能解析出不同实体
（§8.4 已点名的风险）；③ `original_query` / `normalized_query` 没有落脚点，
审计留痕无处可写；④ `validate_calls` 没有可比对的解析记录。

**采用 B**：只有 B 同时满足「每个提及独立记录 span/类型/候选/状态」「顶层保留原句与规范句」
「`compare_course` 能同时表达课程与多个专业」「部分歧义不把整句压成单一实体」
「Task 4 能把每个工具参数绑定到对应提及」。A 的 `selected_entity` 并未丢失 ——
它降为**提及级**字段 `MentionResolution.selected`。

### 17.2 最终数据结构（取代 §6 字段表）

#### 17.2.1 `QueryResolutionEnvelope`（顶层，1 个/问句）

```text
original_query        str        用户原话，**逐字符原样**（值 == 输入，有自洽断言）
normalized_query      str        只替换**已唯一确认**的提及（规则见 §17.2.10）
query_sha256          str        sha256(original_query)，用于审计留痕（**不含时间戳**）
mentions              tuple[MentionResolution, ...]   按 start 升序
suppressed_spans      tuple[SuppressedSpan, ...]      被重叠裁决压掉的 span（**不静默丢弃**）
resolution_status     ResolutionStatus                 顶层聚合（见 §17.2.7）
requires_clarification bool                            聚合规则见 §17.2.8
context               ResolutionContext               本轮只由问句本身决定
catalog_sha256        str        实体目录版本留痕（envelope 级，**每个 mention 不重复**）
aliases_sha256        str        reviewed 别名表版本留痕
resolver_version      str        解析器版本
```

**嵌套关系**：`Envelope 1 → N MentionResolution`；`MentionResolution 1 → N EntityCandidate`；
`MentionResolution 1 → 0..1 SelectedEntity`。三个版本戳**只放顶层**：
一次加载只有一份，避免 N 个副本各自漂移。

#### 17.2.2 `MentionResolution`（一个实体提及）

```text
mention_id          str        "m:<start>:<end>" —— 同一 span 就是同一提及（确定性、免哈希）
raw_text            str        == original_query[start:end]（有自洽断言）
start / end         int        **字符**下标，半开区间 [start, end)（**不是字节**）
entity_type         str|None   唯一确定时才有值，否则 None（**不得凭名称猜类型**）
detection_sources   tuple[CandidateSource, ...]   产生本提及的通道，排序去重
candidates          tuple[EntityCandidate, ...]   按 (tier, candidate_id) 排序
support_tier        str|None   最高且唯一支撑的层级 T1..T7（无唯一层级时 None）
selected            SelectedEntity|None
resolution_status   ResolutionStatus
requires_clarification bool
context_from        tuple[str, ...]   供本提及收敛的、**更早**的 mention_id（依赖留痕）
derivation          str|None   确定性规则命中时的字符对位（供人复核），否则 None
```

#### 17.2.3 `EntityCandidate`

```text
candidate_id    str       规范身份：实体用 entity_id，实体族用 family_id
entity_type     str       major|course|regulation|institutional_term
canonical_name  str       正式名称（course 用 canonical_name，regulation 用 canonical_title）
tier            str       T1..T7（见 §17.5）
sources         tuple[CandidateSource, ...]   产生本候选的通道
scope           dict      该候选声明的适用范围（reviewed alias / family 带来；实体为空对象）
instance_ids    tuple[str, ...]   course 名级候选时，与上下文一致的**全部**实例 id
score           float|None 仅 T5/T6/T7 可携带；**不参与任何判定**（§17.6）
```

#### 17.2.4 `SelectedEntity`

```text
kind            str       "instance" | "name"
entity_id       str|None  kind=instance 时非空
canonical_name  str       正式名称（**这是四个结构化工具真正吃的东西**）
entity_type     str
instance_ids    tuple[str, ...]  kind=name 时非空（上下文一致的实例集合，可确定性重算）
via             str       "exact_canonical" | "reviewed_alias" | "code_exact"
                          | "deterministic_abbreviation" | "context"
alias_id        str|None  由 reviewed alias 命中时带上（留痕）
family_id       str|None  one_to_many 收敛时带上（留痕）
```

★ **为什么 `kind="name"` 必须是第一类结果**（本轮实测得出的结论，§17.12）：
2,116 个不同课程名对应 **7,212 条课程实例**，其中 **628 个名字有多个实例**
（最多的一个叫「跨文化交际」，**64 个实例**）。而四个结构化工具的 `course` / `majors`
参数是**字符串**（`schemas.py` 实测），工具自己按专业再匹配一次。
把课程解析强求成「唯一实例」会让绝大多数正常问句变成歧义。
因此：上下文一致的实例**恰为 1** 时 `kind="instance"`；**多于 1** 时 `kind="name"`
（名字已唯一确定，实例集合作为确定性证据一并留痕）。

#### 17.2.5 `ResolutionContext`

```text
grade       str|None   年级（如 "2025"）
major_code  str|None   专业代码（如 "080902"）
major_name  str|None   专业正式名
semester    int|None   开课学期
source      str        "query"（本轮唯一实现）；"conversation" 仅留位，本轮不实现
```

#### 17.2.6 `CandidateSource`（枚举）

```text
exact_canonical | reviewed_alias | code_exact | deterministic_abbreviation
| context | lexical | embedding | llm
```

#### 17.2.7 `ResolutionStatus`（8 个，**扩展并取代 §6.1 的 7 个**）

```text
exact_canonical                  提及文本本身就是某个正式名称
resolved_reviewed_alias          由 **reviewed** 别名唯一解析
resolved_by_context              收敛**依赖上下文过滤**（年级/专业/学期）
resolved_by_deterministic_rule   ← 新增：收敛**不依赖上下文**，靠确定性规则本身唯一
                                 （目前只有 code_exact 走这一支）
ambiguous                        存在并列候选，无法唯一
unknown                          没有任何候选
scope_conflict                   最高层级候选存在，但其声明 scope 与已知上下文**同维度冲突**
catalog_invalid                  目录 / 别名表缺失、哈希漂移、或指向目录外实体
```

#### 17.2.8 `requires_clarification` 的层级关系

```text
mention 级 = resolution_status ∈ { ambiguous, unknown, scope_conflict }
envelope 级 = any(mention 级) or resolution_status == catalog_invalid
```

★ 顶层**不是**「有没有 selected」的否定 —— `catalog_invalid` 也可能 0 个 mention。

#### 17.2.9 多实体示例（`compare_course`，取自真实目录）

问句：`对比一下 软件工程 和 计算机科学与技术 这两个专业里 数据结构 的学分`

```text
original_query = 上面整句（原样）
mentions（按 start 升序）：
  m:4:8   "软件工程"        T1 候选 = {major:软件工程, course:软件工程 × N 实例}
                            → **跨类型并列** → ambiguous；entity_type=None
  m:11:19 "计算机科学与技术" T1 候选 = {major:2025:…} 唯一 → exact_canonical，kind=instance
  m:26:30 "数据结构"        T1 候选 = 目录中所有名为「数据结构」的课程实例；
                            上下文（m:11:19 已确定的 major）过滤后按 §17.2.4 定 kind
resolution_status = ambiguous（取最差）；requires_clarification = true
normalized_query = 只替换 m:11:19（以及 m:26:30 若已确认）；**不替换** m:4:8
```

★ 这个例子同时展示了三件事：**部分歧义不拖垮整句**（m:11:19 仍然确认）、
**跨类型并列是真实存在的**（9 个专业名同时是课程名，见 §17.12）、
**替换按 span 精确进行**（不会碰其他相同片段）。

#### 17.2.10 `normalized_query` 的确定性生成规则

```text
1. 取 status ∈ { exact_canonical, resolved_reviewed_alias,
                 resolved_by_context, resolved_by_deterministic_rule } 的提及；
2. 按 start **从右向左**依次把 [start, end) 替换为 selected.canonical_name；
3. ambiguous / unknown / scope_conflict 的提及**原文保留**；
4. **禁止 `str.replace`**（会连坐其他相同片段）；
5. 可机械校验：把所有被替换片段**换回**原 raw_text 后，必须与 original_query 逐字相同。
```

★ 从右向左是为了让左侧 span 的下标在替换过程中**保持有效**；任何等价且确定的做法都行，
但**不得**从左向右边替换边漂移下标。

#### 17.2.11 数据结构中**不得**出现的字段

- **没有**模型思维链、自由文本推理、`reason` / `thought` / `rationale` 之类字段 ——
  `derivation` 是**字符对位**（如 `高@0→高等数学@0, 数@3`），是可机械复核的数据，不是解释文字；
- **没有**时间戳、主机名、用户名、绝对路径（与 Task 1/2 的 manifest 同一条规矩）；
- **没有**任何「不可复算」的浮点参与判定：`EntityCandidate.score` 只允许由 T5/T6/T7
  携带、**只作记录**，§17.6 的判定路径**一次都不读它**；
- **没有**随机性：同一输入（问句 + catalog_sha256 + aliases_sha256 + resolver_version）
  必得同一 envelope，含 `mention_id` / 排序 / 状态。

### 17.3 提及的 span、重叠、重复与替换

- **span 是字符下标**（Python `str` 语义，半开区间）。**不是字节** —— 问句里有全角字符，
  按字节算会与 `raw_text` 对不上。`raw_text == original_query[start:end]` 是**自洽硬门**。
- **重复提及**：同一串出现两次 → **两个不同提及**（span 不同），**不合并且不去重**；
  但二者 `raw_text` 与上下文相同时解析结果**必然相同**（解析是纯函数），
  有专门用例锁定这条。
- **重叠提及的裁决（最长匹配不是绝对优先）**：先按**证据层级**分层，再在层内做
  **span 贪心**：同层内 `start` 升序、同 `start` 时**长者优先**，已被接受 span 覆盖的
  span 一律**记入 `suppressed_spans`**（带 span 与压制理由），**绝不静默丢弃**。
  ★ 为什么不能「长的一定赢」：长 span 可能只是低层级的词法命中（如整句子串），
  却把里面一个**高层级的正式名**吞掉。层级优先、长度只做层内次序。
- **一个字符串同时是课程名与专业名**（实测有 9 个：软件工程、人工智能、会计学、
  市场营销、新能源材料与器件、电子商务、财务管理、软件工程、金融工程等）：
  **一个提及、多个候选**，候选各自带 `entity_type`。**resolver 不得按类型猜选**
  （解析发生时还没有目标工具，猜类型就是静默选错）→ 记 `ambiguous`。
  按参数期望类型收窄**只允许**发生在 Task 4 的 `validate_calls`，见 §17.7。
- **外部候选生成器提出的 span 必须验证**：`embedding` / `llm` 只提供 span，
  resolver 必须① `0 <= start < end <= len(query)`；② **从问句里重新取出** `raw_text`；
  之后才能建提及。**对不上的 span 一律丢弃并记入 `suppressed_spans`** ——
  外部通道**不得**引入问句里没有的文本。
- **LLM 提出的 span 能否单独触发自动选择：不能。** 由 LLM span 建立的提及，
  其候选最多只能到 T7；按 §17.5 的层级表，T7 **永不**构成确定性支持，
  因此该提及只能落到 `ambiguous`（≥1 候选）或 `unknown`（0 候选）。
  LLM span 的**唯一**合法作用：把那段字符**交给确定性通道重跑一遍**
  （此后若 T1/T2/T3/T4 命中，那是确定性通道的功劳，不是 LLM 的判定）。

### 17.4 「unknown + 上下文唯一」与「LLM-only」的分界（本轮最关键的裁决）

**四条写死的口径**：

1. **目录存在性只证明实体真实存在，不证明用户的简称指向它。**
   `计科` 在目录里能找到候选，不等于 `计科` 就是那个候选。
2. **年级 / 专业 / 学期上下文只能过滤候选，不能凭空证明简称语义。**
   上下文**不产生候选**；它只在已有候选集合上做交集（§17.5 步骤 2）。
3. **LLM 或 embedding 提出的唯一候选，即使目录中存在、scope 也一致，也不得因此自动选中。**
   「只有一个候选」≠「它是对的」（§10.2 继续有效）。
4. **`resolved_by_context` 必须先有至少一种非 LLM、非 embedding 的确定性候选支持**
   （T1/T2/T3/T4，见 §17.5）。上下文只能**收敛**，不能**替代**支持。

### 17.5 确定性支持清单与「缩写规则」的定位

| 层级 | 来源 | 判据 | 确定性支持 |
|---|---|---|---|
| **T1** | `exact_canonical` | 提及文本的正式归一形式 == 某个正式名称 | **✓** |
| **T2** | `reviewed_alias` | 命中 **reviewed** 的 `one_to_one` 别名（`one_to_many` 需 scope 收敛） | **✓** |
| **T3** | `code_exact` | 提及文本 == 某个 `course_code` / `major_code` | **✓**（唯一时） |
| **T4** | `deterministic_abbreviation` | **首字锚定 + 严格顺序子序列**（下方定义） | **弱 ✓（仅 §17.5.2 的情形）** |
| **T5** | `lexical` | 前缀 / 子串（`match_course` 既有口径）/ `difflib` 相似 | ✗ |
| **T6** | `embedding` | 注入式，向量相似 | ✗ |
| **T7** | `llm` | 注入式，模型提议 | ✗ |

#### 17.5.1 T4 的规则定义（本轮唯一定义的字符规则，确定性、可复现）

```text
abbrev(short, long) 成立 ⇔
  (a) 2 ≤ len(short) < len(long)
  (b) short[0] == long[0]                  # 首字必须相同
  (c) short[1:] 是 long[1:] 的**顺序子序列**（字符顺序一致，允许跳过）
```

不引入词表、不引入分词、不引入权重 —— 同一输入必得同一输出，可逐字符复核。
命中时必须记录 `derivation`（哪些字符匹配到 `long` 的哪些位置）供人复核。

#### 17.5.2 ★ T4 的**弱化裁决**（实测驱动的结论）

> ★ **Task 3 ruling 2 已把本节再收紧一档**：T4 **一律**不得自动选中 ——
> 连「上下文过滤后只剩一个」也不行。见 **§17.14.1**。下文括号内为原裁决。

**在真实目录上实测量得**（命令见 §17.12）：

```text
高数 → 9 个候选：高等代数1 / 高等代数2 / 高等代数选论 / 高等数学A1 / A2 / B1 / B2 / C1 …
大物 → 5 个候选：大学物理A1 / A2 / B / C / 大学物理实验
计科 → 2 个候选：course 计算机科学导论 + major 计算机科学与技术
软工 → 6 个候选：软件工程 / 软件工程(外文教材) / 软件工程与UML / 软件工程概论 / … + major 软件工程
毛概 → 1 个候选：毛泽东思想和中国特色社会主义理论体系概论
大英 → 10 个候选；形策 → 8 个候选（形势与政策1..8）
```

结论：这条规则**精度不足以为真** —— 它把 `高数` 拉到**高等代数**（另一门课）、
把 `计科` 拉到**计算机科学导论**。因此：

> **T4 只在「上下文把候选收敛到恰好 1 个」时才可 selected**（status = `resolved_by_context`）；
> **T4 单独唯一（没有上下文参与）不得 selected** —— 记 `ambiguous`，要求澄清。

理由：用「集合恰为 1」当自动选择的充分条件，会在**目录里只存在那个错误候选**时
（例如某专业只有「高等代数」而问 `高数`）静默给出**自信的错答案**，
正中本项目最忌讳的失败模式。而「上下文收敛到 1」不同：**收窄是用户自己给的**，
可复核、可复算，并且被过滤掉的候选全部留痕。

**T4 的合法用途**：① 生成候选（供澄清话术列举）；② 供 §11 的人审队列；
③ 在 §17.8 决策表第 6 行的条件下参与收敛。

#### 17.5.3 为什么这条规则不会退化成 top-1 猜测

因为它是**集合判据，不是排序判据**：T4 对每个名称只回答「是 / 不是」，
**没有分数、没有名次、没有第一名可以取**。当集合大小 > 1 时，
程序里**不存在**任何「拿一个最像的顶上」的代码路径 —— 这正是 §10.2 想要的形态。
反过来说，任何引入相似度分数的通道（T5/T6/T7）都必须先解决「谁是 top-1」的问题，
所以它们**一律不许参与选择**（只许生成候选）。

#### 17.5.4 两条防爆护栏（对应「两个字简称会不会产生大量假候选」）

- 长度下限 `len(short) ≥ 2`（默认拒 1 字简称 —— 1 字简称会命中几百个名称）；
- **首字必须相同**（`short[0] == long[0]`）：实测下 `大英` 只命中 10 个、`形策` 只命中 8 个，
  而不是「凡含英字的课」。即便如此仍不足以自动选（见 §17.5.2），
  所以真正的护栏是**「必须被上下文收敛」**，不是长度。

### 17.6 runner-up 歧义门（取代 §10.3 第 5 步；**首版不含任何数值阈值**）

**决议：Task 3 首版写死「不含任何相似度阈值」。** 数值校准属独立评估任务，
**不得混入首版 resolver**（不得出现 `top1-top2 > 0.1` 之类判据）。

**可执行规则**：

```text
1. 生成候选（多通道），每个候选带 tier（取该候选命中的**最高**层级）与声明 scope；
2. scope 硬过滤：丢弃「与已知上下文**同维度**且取值不同」的候选（上下文缺失的维度不参与）；
3. T* = 剩余候选中的最高 tier；S = 该 tier 的全部候选；
4. |S| == 1 且 T* ∈ {T1,T2,T3}          → 允许 selected
   |S| == 1 且 T* == T4                 → **仅在步骤 2 过滤掉过候选时**允许（resolved_by_context）
   |S| == 1 且 T* ∈ {T5,T6,T7}          → **不允许**，记 ambiguous
   |S| >  1                             → ambiguous（**低层级永远不得破并列**）
   |S| == 0                             → 步骤 2 曾把最高层级候选全部过滤掉 → scope_conflict；
                                          否则 unknown
```

**逐条回答**：

- **哪些层级有资格自动 selected**：T1 / T2 / T3，以及**被上下文收敛过的** T4。
- **哪些只能扩展候选**：T5 / T6 / T7（永远不参与选择）。
- **同一最高层级有两个候选**：**必须 ambiguous**，不得比较分数、不得比长度、不得比通道数。
- **低层级能否成为 runner-up 并阻止自动选择**：**不能阻止**。T5/T6/T7 的候选会被
  **记录在 `candidates` 里**（带 tier，供澄清话术与审计），但**不进入任何判定**。
  理由：若一个 embedding 命中就能否决一次「用户写了正式名」的 T1 命中，
  系统会退化成「谁向量近谁说话」，与本设计的目标 3 直接冲突。
- **reviewed `one_to_one` 与 scope 冲突为什么是 `scope_conflict` 而不是强行选择**：
  已审核别名是**带适用范围的注册事实**；在范围外使用它，等于把一条被限定的事实
  **偷偷外推到它没被审核过的情景**。宁可澄清，也不外推。
- **reviewed `one_to_many` 经 scope 仍多成员**：**必须 ambiguous**，
  且**不得**把族成员的「第一个」当成答案（§2.3 那四处静默取首项正是要消灭的东西）。
- **只有一个 embedding / LLM 候选时**：仍然 `ambiguous`，**不得 selected**。

### 17.7 Task 2 的 reviewed-only 生产边界

**决议**：

1. resolver 的构造函数**只接受已验证、已过滤的不可变记录**：
   `reviewed_aliases: tuple[Mapping, ...]`、`reviewed_families: tuple[Mapping, ...]`、
   `catalog`（已由 `entity_catalog.load` 严格校验过）。
2. **禁止 resolver 直接接收 `AliasBundle`** —— bundle 内部同时装着 pending / rejected，
   收下它就把「生产视图」变成了「调用者自觉」。**本轮不改 Task 2 代码**，只定接口。
3. 提供**唯一生产工厂**（命名待 Task 3 定，语义固定）：

```text
build_production_resolver(...)  :=  load_alias_bundle()          # Task 2 的严格门
                                 → reviewed_aliases(bundle)      # 只取 reviewed
                                 → reviewed_families(bundle)
                                 → Resolver(reviewed_aliases=…, reviewed_families=…, catalog=…)
```

4. resolver 模块**不得** import 或调用 `audit_aliases` / `audit_families`。
5. **静态测试**（Task 3 写）：用 AST 扫 resolver 模块，断言
   ① 不出现标识符 `audit_aliases` / `audit_families`；
   ② 不出现 `AliasBundle`；③ 不出现属性访问 `.aliases` / `.families`。
6. **行为测试**（Task 3 写）：构造一个含 pending / rejected 的 bundle 走工厂，
   断言 resolver 的别名视图里**没有**那些行；即「磁盘上有 pending」也到不了候选生成。

★ 结果：pending / rejected 即使存在于磁盘，也**不可能**到达候选生成 ——
它们根本不在构造函数的入参里。

### 17.8 可执行决策表（取代 §9 决策表）

> ★ **Task 3 收口（见 §17.14.1）**：第 **6** 行的 `selected` 已由「允许（只因上下文）」
> 改为**不允许**、状态由 `resolved_by_context` 改为 `ambiguous`；
> 第 **12/13** 行的 `catalog_invalid` 改为在**构造期**失败（§17.14.4）；第 **18** 行的
> 类型收窄仍未接入（Task 4）。其余各行不变。

列含义：`确定性支持` = 是否有 T1..T4；`selected` = 是否允许自动选定该提及；
`替换` = `normalized_query` 是否替换该 span；`结构化` = Task 4 是否允许调用结构化工具；
`扩展` = `search_regulations` 是否允许扩展召回。

| # | 场景 | candidate sources | 确定性支持 | selected | resolution_status | 需澄清 | 替换 | 结构化工具 | 规章扩展 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | exact canonical（唯一） | T1 | ✓ | **允许** | `exact_canonical` | 否 | ✓ | 允许 | 允许 |
| 2 | reviewed `one_to_one` | T2 | ✓ | **允许** | `resolved_reviewed_alias` | 否 | ✓ | 允许 | 允许 |
| 3 | reviewed `one_to_one` + **scope 冲突** | T2 | ✓ 但范围不符 | **不允许** | `scope_conflict` | **是** | ✗ | **禁止** | 仅原词 |
| 4 | reviewed `one_to_many` + scope **收敛到 1** | T2 | ✓ | **允许** | `resolved_by_context` | 否 | ✓（收敛到该成员） | 允许 | 允许 |
| 5 | reviewed `one_to_many` + scope **仍多个** | T2 | ✓ 但未收敛 | **不允许** | `ambiguous` | **是** | ✗ | **禁止** | 允许（含合法候选词，但**不得声称已唯一确定**） |
| 6 | 确定性缩写 + **上下文收敛到 1** | T4 + context | 弱 ✓ | **允许（只因上下文）** | `resolved_by_context` | 否 | ✓ | 允许 | 允许 |
| 7 | 确定性缩写 + 多候选（无上下文或仍 >1） | T4 | 弱 ✓ | **不允许** | `ambiguous` | **是** | ✗ | **禁止** | 仅原词 |
| 8 | lexical-only 单候选 | T5 | ✗ | **不允许** | `ambiguous` | **是** | ✗ | **禁止** | 仅原词 |
| 9 | embedding-only 单候选 | T6 | ✗ | **不允许** | `ambiguous` | **是** | ✗ | **禁止** | 仅原词 |
| 10 | LLM-only 单候选 | T7 | ✗ | **不允许** | `ambiguous` | **是** | ✗ | **禁止** | 仅原词 |
| 11 | 多通道但**无确定性支持** | T5+T6+T7 | ✗ | **不允许** | `ambiguous` | **是** | ✗ | **禁止** | 仅原词 |
| 12 | catalog 孤儿（指向目录外的实体 / 族） | — | — | **不允许** | `catalog_invalid` | **是** | ✗ | **禁止** | **禁止（停止并报告）** |
| 13 | alias / family manifest 无效（含 SHA 漂移、catalog 绑定不符） | — | — | **不允许** | `catalog_invalid` | **是** | ✗ | **禁止** | **禁止（停止并报告）** |
| 14 | 多提及中一项 unresolved | 混合 | 部分 ✓ | 该项**不允许** | 未决者按各自状态；其余各自 | **是** | 只替换已确认者 | **整次调用阻断** | 允许 |
| 15 | 所有提及均 resolved | 混合 | ✓ | **允许** | 各自状态；顶层取最差 | 否 | 全部替换 | 允许（多参数齐备） | 允许 |

**补充两行（本轮实测新增，必须一并实现）**：

| # | 场景 | candidate sources | 确定性支持 | selected | resolution_status | 需澄清 | 替换 | 结构化工具 | 规章扩展 |
|---|---|---|---|---|---|---|---|---|---|
| 16 | exact canonical 但**同名多实例**，上下文收敛到 1 个实例 | T1 + context | ✓ | **允许**（`kind="instance"`） | `resolved_by_context` | 否 | ✓ | 允许 | 允许 |
| 17 | exact canonical 但**同名多实例且无上下文收敛** | T1 | ✓ | **允许**（`kind="name"`，名字唯一确定） | `exact_canonical` | 否 | ✓ | 允许（工具须报告实际命中对象） | 允许 |
| 18 | exact canonical **跨类型并列**（9 个专业名同时是课程名） | T1 | ✓ | **提及级不允许** | `ambiguous` | **是** | ✗ | 见 §17.7★ / §17.11 开放项 | 允许 |

★ 第 18 行的**唯一例外通道**（Task 4 实现，Task 3 不实现）：
`validate_calls` 在把提及绑定到**具体参数**时，可以按该参数的**期望 entity_type 收窄**，
条件是收窄后最高层级候选**恰为 1**，且必须把 `narrowed_by="arg_type"` 与原始并列类型
写进调用留痕。**不得**在 resolver 内实现该收窄（那时还没有工具信息）。
该收窄是否需要保留，**列为开放项**（§17.11 第 2 条）。

### 17.9 四个结构化工具的参数映射表（schema 一律不改）

| 工具 | 参数 | 需解析 | 期望 entity_type | 参数 ↔ 提及 | 任一项未决 |
|---|---|---|---|---|---|
| `query_course` | `major` | ✓ | `major` | **1 : 1** | 整次调用**阻断** |
| `query_course` | `course` | ✓ | `course` | **1 : 1** | 整次调用**阻断** |
| `list_courses` | `major` | ✓ | `major` | **1 : 1** | 阻断 |
| `list_courses` | `semester` / `module` / `required` / `core_only` | ✗ | — | —（筛选值，不是实体） | — |
| `compare_course` | `course` | ✓ | `course` | **1 : 1** | 整次调用**阻断** |
| `compare_course` | `majors` | ✓ | `major` | **有序 1 : 1**（第 i 项 ↔ 第 i 个 major 提及） | **任一项未决即整次阻断**（§8 风险 8） |
| `query_major_requirements` | `major` | ✓ | `major` | **1 : 1** | 阻断 |
| `search_regulations` | `query` / `keywords` | ✓（**只扩展召回**，不要求唯一） | 四类之一 | **1 : N** | **不阻断** |

**逐条回答**：

- **canonical_args 写什么**：写**规范名称字符串**（四个结构化工具的 schema 是 `string` /
  `array[string]`，实测）。**实体 id 不进工具参数**（那会改工具行为与训练分布），
  实体 id / instance_ids 进**审计留痕**。两者**同时保留**：`raw_args` + `canonical_args`。
- **schema 不变时规范实体怎么传给工具**：传规范名称；由 Task 4 在
  `repository` / `resolve_major` 侧**去掉静默取首项**（`ms[0]` / `hits[0]`），
  改成：规范名称在同专业内命中 **>1 实例**时**拒绝执行并澄清**
  （实测：**3 个专业内存在同名课程，共 13 个重名** —— 这是 `hits[0]` 的真实踩坑面）。
- **`compare_course` 多个 majors 如何保持顺序与一一对应**：`majors` 列表顺序 ==
  提及的 `start` 升序；解析后若**两条不同提及解析到同一个 major**，`canonical_args`
  会出现重复元素（schema 的校验器会拒），因此 `validate_calls` **必须先行**发现
  并转澄清，而不是丢给工具报 `invalid_args`。
- **重复参数 / budget key / duplicate key**：三者统一改用 **canonical identity**：
  `call_key(name, canonical_args)`（`budgets.call_key` → `args_hash(canonical_args)`）。
  `raw_args` 仍写进 `calls` 日志与 `executed` 留痕。这样「同一实体不同写法」
  不会再被当成两次调用。

### 17.10 资格考试四类机制题的映射（仅设计，不新增场景）

| # | 机制 | 需要的解析结果 | 现状与前置条件 |
|---|---|---|---|
| **A** | reviewed `one_to_one` → 直接规范化 | 决策表第 2 行；结构化工具 1 次，参数为规范实体 | **正式 aliases 目前是空文件** → A 在真正重放前**必须**先走人工审核 + 晋升（Task 6/8）；Task 3 单测只能用**临时 fixture** |
| **B** | reviewed `one_to_many` → 必须澄清 | 决策表第 5 行；**工具调用 0 次** | 同样需要先有人审过的族；按 Task 2 裁决，**族必须先 reviewed** 才能被任何别名指向 |
| **C** | 未知简称 + 确定性支持 + 上下文唯一 | 决策表第 6 行；结构化工具 1 次 | 必须选一个**上下文能收敛**的简称（如 `形策` + 学期 → 8 → 1）。**不得**用 LLM-only 候选伪装成「上下文唯一」 |
| **D** | 未知简称、无可靠唯一候选 → 要求完整名称 | 决策表第 7/8/9/10/11 行；**工具调用 0 次** | 与 A/B 不同，D **不依赖任何正式别名**，今天就能构造 |

★ 三个共同口径：**C 的真实性取决于 T4 的弱化裁决**（§17.5.2）—— 若人工裁定
「T4 不得自动选」，C 必须改由 reviewed alias 承载；**B/D 必须实测「确实 0 次调用」**；
四条**全部先保持 `pending`**，人工审核后才可 `reviewed`，**不得提前冻结**。

### 17.11 Task 3（下一轮）实现边界

**允许**：纯 resolver 模块（不接图）；§17.2 的数据模型；
exact canonical；reviewed alias；本地确定性字符/关键词候选（T4/T5）；
scope / context 过滤；候选合并；离散证据优先级（§17.6）；runner-up 歧义判定；
`normalized_query` 生成；**注入式** embedding / LLM proposal 的**验证**逻辑（不真调）；
单元测试。

**不允许**：接 `graph.py` / `state.py`；改工具或 `repository`；
真调 embedding；真调 LLM；查 Qdrant；改检索参数；实现学习队列；
修改正式 `aliases` / `families`；添加任何正式简称；修改资格考试包。

### 17.12 本轮实测（只读，命令可复算）

用当前正式 `backend/data/entity_catalog.json` 复算，未改任何数据：

```text
课程实例 7,212 / 不同课程名 2,116；**628 个名字有多个实例**，最多 64 个（跨文化交际）
**3 个专业内存在同名课程，共 13 个重名**   ← `match_course` + `hits[0]` 的真实踩坑面
专业名 63 个全部互不相同；**9 个专业名同时是课程名**
  （人力资源管理 / 人工智能 / 会计学 / 市场营销 / 新能源材料与器件 / 电子商务 / 财务管理 / 软件工程 / 金融工程）
规章标题 28 个互不相同；无任何 course/major 名与规章标题撞名
T4 缩写规则实测：高数→9、大物→5、计科→2（跨类型）、软工→6（跨类型）、毛概→1、大英→10、形策→8
```

★ **一处数字更正（只读核验，非本轮引入）**：§2.3 记「静默取首项 **4 处**」，
按当前磁盘代码实为 **5 处** —— `repository.py` 的 4 处（`query_course` 的 `ms[0]` / `hits[0]`、
`compare_course` 的 `ms[0]`、`_row` 的 `hits[0]`）**之外**，还有
`query_course/tool.py::resolve_major` 的 `matches[0]` ——
而 `list_courses` 与 `query_major_requirements` **都经由它**取专业（`resolve_major` 实测）。
即「取首项」实际影响 **5 条路径、4 个工具**。Task 4 收口时必须一并处理，别只改 `repository.py`。

### 17.13 本节发现的开放项（需人工裁决，**不自行定夺**）

1. **T4 是否可自动选择**：本轮裁为「只在上下文收敛到 1 时可选」。
   更严的备选口径是「T4 一律只生成候选，永不 selected」（则 §13 的 C 场景必须改由
   reviewed alias 承载）。**需人工确认**。
2. **跨类型并列是否允许在 `validate_calls` 按参数期望类型收窄**（§17.8 第 18 行 ★）：
   本轮裁为「允许，但必须留痕」。**实现 Task 4 之前需人工确认**。
3. **`kind="name"`（课程名级结果）**：本轮据此裁决，因为 628/2,116 个课程名有多个实例；
   若人工要求「必须唯一实例才可调用工具」，`compare_course` 的多专业用法会直接不可用。
   **需人工确认**。

### 17.14 Task 3 实施后的口径修订（2026-10-16）

Task 3（纯函数解析器 `backend/entity_resolver.py`）落地后，以下**人工裁决**覆盖 §17 的对应条目；
**其余各节不变**，**未修改任何正式资产**。

#### 17.14.1 T4 一律不自动选中（覆盖 §17.5.2 与 §17.8 第 6 行）

Task 3 ruling 2：**T4/T5/T6/T7 只能提出候选，绝不自动选中；「上下文过滤后只剩一个」
也不得把弱证据升级为实体真相。** 故 §17.8 第 6 行的 `selected` 由「允许（只因上下文）」
改为**不允许**，状态改为 `ambiguous`；第 7 行不变。实现里弱候选只用于
① 澄清话术列举 ② 人审队列原料 ③ 留痕。

★ **连带后果（必须记账）**：§13 的**资格考试 C 场景**（「未知简称 + 确定性字符支持 +
上下文唯一」）**不能再由 T4 承载** —— 必须改由 **reviewed alias（T2）** 承载，
或改写为 D（要求完整名称）。**C 的最终形态需人工裁决。**

#### 17.14.2 不做「按提及顺序累积上下文」（覆盖 §17.2.2 的 `context_from`）

解析是**单趟、与提及顺序无关**的纯函数；专业/年级上下文**只由调用方**通过
`ResolutionContext` 传入（Task 4 负责从问句与图里取）。`context_from` **本轮未落地**。
理由：`compare_course` 会同时提到**多个**专业，按顺序累积会让第二个专业被第一个的
上下文过滤掉 —— 那是错的；改成两趟或定点解会引入顺序效应，得不偿失。

#### 17.14.3 学期上下文只记录、不过滤（覆盖 §17.2.5 / §17.5）

正式目录的 course 实体字段是 `entity_id / canonical_name / grade / major_code /
course_code / row_hash / source_file / source_line` —— **没有 `semester`**。
因此 `ResolutionContext.semester` 本轮**只留痕、不参与过滤**。要按学期收敛，
必须先授权把 `semester` 纳入 catalog，或允许 resolver 读 `plan_db`。**已记账。**

#### 17.14.4 `catalog_invalid` 在**构造期**失败（覆盖 §17.8 第 12/13 行）

目录哈希漂移、上游漂移、别名包非法、alias/family 指向目录外实体 —— 全部由 Task 2 的
严格加载与 `build_production_resolver` 在**构造期**拒绝，抛 `ResolverError`
（**不是**一个带 `catalog_invalid` 状态的 envelope）。该状态保留在枚举里，
供 Task 4 把工厂失败映射成用户可见的**停止并报告**，
**不得**映射成 `no_evidence` / `irrelevant_result` / `tool_failed`。

#### 17.14.5 新增字段 `discarded_proposals`（补充 §17.2.1）

注入式提案若 ① span 越界 / ② 从问句重新取出的文本对不上 / ③ 指向目录外的实体，
一律**丢弃并逐条留痕**（`source / start / end / text / reason`）。
外部通道**不得**引入问句里没有的文本。

#### 17.14.6 T4 再收紧：**压缩**，不是**截断**（细化 §17.5.1，只减不增）

实施时实测发现：只有「首字锚定 + 顺序子序列」时，`专业` 会命中 `专业外语`（**逐字前缀**），
于是正常问句「计算机科学与技术**专业**的课程」会多出一个 `专业` 提及，
按 §17.2.8 聚合后**强制澄清** —— 实测复现（该句唯一的真实实体本来 `exact_canonical`）。

故在 §17.5.1 的 (a)(b)(c) 之外补一条：

```text
(d) 匹配位置**至少要跳过 1 个字符**（positions[-1] > len(short) - 1）
```

即 T4 只认**压缩**（`高数`←`高等数学`：高@0, 数@2），不认**截断**（`专业`←`专业外语`：@0,@1）。
截断那一类由 **T5 真前缀**通道负责（长度 ≥ 3），两条通道分工清楚。

★ 这是**收紧**：候选只减不增，**没有放松任何唯一性门**。实测收紧后
`高数 / 大物 / 计科 / 软工 / 毛概 / 形策 / 大英` **仍全部命中** T4 候选，
`高数 → 高等代数1` 这条「规则会拉到别的课」的证据**依然存在**（§17.5.2 的结论不变）；
而 `专业` / `课程` / `学分` 这类普通词不再产生幻影提及。

#### 17.14.7 已实现的落点

```text
backend/entity_resolver.py              解析器（纯函数：无运行图、无工具、无网络、无 LLM）
backend/tests/test_entity_resolver.py   62 条用例
```

`catalog_sha256` / `aliases_sha256` / `resolver_version` 由构造方注入并写在 envelope **顶层**；
`Resolver` **拒绝**接收 `AliasBundle`、**拒绝** pending/rejected 行（构造期 `ResolverError`）；
`narrow_candidates(mention, expected_entity_type)` 是**纯过滤函数**（不接图、不做选择）。

### 17.15 Task 4.1 落地：调用验证与参数规范化（2026-10-16）

`backend/entity_calls.py`（**纯函数**：不接图、不执行工具、不碰 Registry / budget / duplicate）。

输入：原始 tool calls + `QueryResolutionEnvelope`。输出 `CallValidation`：

```text
tool / decision / reason
raw_args        原样保留（**与 canonical_args 双留痕**）
canonical_args  实体参数换成**规范名称**（schema 是 string）；实体 id **不进**参数
bindings[]      每个参数一条：arg / index / mention_id / entity_id / instance_ids /
                entity_type / canonical_name / kind / narrowed_by / narrowed_from
clarification   未决时给出：arg / index / mention_id / raw_text / resolution_status /
                support_tier / candidate_names / candidate_entity_types
validation_version
```

**三种决策，没有第四种**：`allow` / `clarify` / `reject`。

- `clarify` = 参数对应的实体**没被唯一确认**（歧义 / 未知 / scope 冲突 / 弱证据）→
  **明确澄清**，不得伪装成 `no_evidence` / `irrelevant_result` / `tool_failed`；
- `reject` = 调用**本身**不成立（未知工具 / 缺参 / 多参 / 类别错 /
  参数在问句里**找不到对应提及** / 专业重复）→ 整次不做；
- ★ **非 `allow` 的调用不产出 `canonical_args`（返回 `{}`）** —— 免得下游误当可执行。

**参数与提及的绑定按文本**（`join_key` 归一后逐字相等），**不按顺序猜**：
参数值必须在问句里真的有对应提及，否则 `arg_unmapped` —— 追溯不到的参数不是可验证的调用。
`compare_course.majors` 因此天然保持**原始顺序**（`bindings` 带 `index`）。

**收窄（ruling 5、6）**：跨类型并列时按参数期望的 `entity_type` 收窄，**仅当**
① 该提及处于**确定性层级**（T1/T2/T3）②收窄后最高层级**恰为 1** 个；
通过时记 `narrowed_by="arg_type"` 与 `narrowed_from=<收窄前的候选类型>`。
**T4–T7 一律不通过**（弱证据不得因参数类型升级为实体真相）。

**全有或全无（ruling 7）**：`compare_course` 任一参数未决 / 专业重复（**按 canonical 判**，
两种写法绑到同一专业也算） / 映射不完整 → **整次调用不做**，`bindings` 为空。

**旁路（ruling 10）**：`search_regulations` / `query_empty_classroom` / `create_ppt`
**原参数通过**（`reason="passthrough"`），本 Task 不改其行为。

**call key（ruling 9）**：`canonical_call_key(tool, canonical_args)` 是纯函数，
**复用 Registry / budgets 的同一个 `args_hash`**（不另写一套）；本 Task
**不接入** budget / duplicate 流程。

### 17.16 Task 4.2 落地：清除首项选择 + canonical 严格守卫（2026-10-16）

设计 §2.3 记的「静默取首项」在磁盘上实际是 **5 处**（Task 3.0 已更正：4 处
`repository.py` + 1 处 `query_course/tool.py::resolve_major`），本轮**全部清除**：

| 位置 | 原写法 | 现在 |
|---|---|---|
| `repository.query_course` | `m = ms[0]` | `m = pick_major(ms, major)` |
| `repository.query_course` | `_card(m, hits[0], …)` | `_card(m, pick_course(hits, course), …)` |
| `repository.compare_course` | `_row(ms[0], course)` | `_row(pick_major(ms, name), course)` |
| `repository._row` | `c = hits[0]` | `c = pick_course(hits, course)` |
| `query_course/tool.py::resolve_major` | `return matches[0]` | `return repository.pick_major(matches, major)` |

**结果规则**（`pick_major` / `pick_course` 同一口径）：

```text
0 个   → 调用方维持既有 not_found 行为（major_not_found / course_not_found）
恰 1 个 → 照旧返回，**输出逐字不变**
多个   → 抛 AmbiguousLookupError —— **绝不取第一项**
```

`AmbiguousLookupError`（`LookupError` 子类）带 `field` / `query` / `candidates`，
**不是** `ToolResult`、**不新增 `ToolStatus`、不改 Registry**。

★ **为什么用异常而不是返回 `{"error": ...}`**：`query_course_result` 把
`data["error"]` 映射成 `no_evidence`，而「歧义」**不是**「学校没有规定」。
异常经 Registry 的既有 `except Exception` 边界转成
`ok=False / status="tool_failed"`，**不新增第二套失败语义**。

★ **Task 4.3 的 `validate_calls` 如何让这条防御异常在正常路径不可达**：
图级 `validate_calls` 会在**调用工具之前**用 `entity_calls.validate_call` 检查每一个
结构化参数 —— 提及未唯一确认（`ambiguous` / `unknown` / `scope_conflict` / 弱证据）
时决策为 `clarify` / `reject`，**根本不会发出这次工具调用**；跨类型并列也已在那一层
按参数期望类型收窄过。因此 `AmbiguousLookupError` 只在他处**绕过图直接调工具**时才会出现，
它是**兜底守卫**，不是正常失败路径。

**`compare_course` 全有或全无**：任一专业或课程判歧义时异常直接穿出
（`compare_course_result` 逐个 major 调 `query_course_result`，第一个抛错就整体中止），
**不返回部分行**。

**同一专业内同名课程多实例**（实测 3 个专业共 13 个重名）走同一条路：
`match_course` 精确命中两条同名记录 → `pick_course` 抛歧义，
**不再静默挑其中一条**。
