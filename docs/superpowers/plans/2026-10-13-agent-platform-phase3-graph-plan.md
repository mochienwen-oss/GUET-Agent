# Agent Platform Phase 3 Unified Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed two-round QA path and the rule-based PPT router with one LangGraph main graph that loops on the model's own tool calls, enforces budgets and stop rules, maintains a single Evidence ledger, and hands off to the PPT subgraph when the model chooses `create_ppt`.

**Architecture:** `agent/runtime/graph.py` compiles START → `agent_decide` → (`validate_calls` → `execute_tools` → `append_evidence` → `agent_decide`) with two terminal branches: END for a final answer, and a PPT handoff that delegates to the existing `create_ppt` subgraph and never loops back. Budgets and stop rules live in `agent/runtime/budgets.py` as pure functions of state. Observation text, tool schemas, message shape and sampling parameters stay byte-frozen, because the LoRA was trained on them.

**Tech Stack:** Python 3.13, LangGraph `StateGraph`, LangChain Core `@tool` (already in place), Pydantic v2, pytest, existing `llm_client` / `agent.runtime.registry`.

**Spec:** `docs/superpowers/specs/2026-10-13-agent-platform-redesign-design.md` (§5 统一 Agent 主图, §11 阶段3)

## Global Constraints

- 所有请求经过**唯一** LangGraph 主图；不保留第二条运行入口（设计 §5、§12.2）。
- 预算默认值：普通任务最多 **4** 个 Agent 步骤；最多执行 **6** 次工具调用（设计 §5.3）。
- **相同工具与相同参数不得重复执行**；参数校验失败不得用原参数重试（设计 §5.3）。
- **连续两次工具调用没有新增 Evidence 时停止研究**（设计 §5.3）。
- 网络、超时等可恢复故障最多**重试一次**（设计 §5.3）。
- 达到预算时，**只返回已有证据支持的内容和明确缺口**，不得空答（设计 §5.3）。
- `create_ppt` 启动后由 PPT 子图接管，**主图不与子图交叉循环**（设计 §5.3）。
- 只保存可观察轨迹 `user / assistant(tool_calls) / tool(observation) / assistant(...)`；**不保存可见 `Thought:`**（设计 §5.2、§2.2）。
- `Evidence` 只能从 `ToolResult.sources` 构造，**禁止从 observation 文本反解析**（设计 §4.2）。
- 冻结层**逐字不动**：`llm_client.SYSTEM_PROMPT`、`registry.schemas()`、`registry.render()`、`agent/core/observation.py`、`_r2_messages` 的消息形状、采样参数 `temperature/top_p=0.8/top_k=20/repetition_penalty=1.05`。
- `settings.vllm_model` **不得写死成 `guet-agent`** —— 该 LoRA 要到阶段8 才训练出来；本阶段只把主图做好，模型名保持可配。
- `routers/chat.py` 的六个既有 SSE 键（`content` / `error` / `calls` / `replace` / `title` / `[DONE]`）与 `ppt_*` 加性键**语义不变**。
- 本目录**不是 git 仓库**：用 `docs/WORK_PROGRESS.md` 与 SHA-256 manifest 代替 commit 步骤。
- 服务器默认只读；本阶段**不改服务器、不启停服务**。

## Review Focus

1. **模型喊了同一工具同一参数两次** —— 必须拦住、**不执行第二次**，且那次重复不得算作「新增证据」（否则「连续两次无新增即停」的规则永远不触发）。
2. **第 2 轮模型又喊工具**（训练里罕见但确实存在）—— 必须走 `validate_calls`/`execute_tools`，不能像今天 `qa.py` 那样用 `tool_choice="none"` 重问一次把它**丢掉**：「none」会把 `tools` 整个从 payload 里拿掉，`# Tools` 段随之消失，形状就不是训练那条了。
3. **预算耗尽**（步数到 4 或工具调用到 6）—— 用已有 Evidence 收尾并**明说缺口**，不得返回空串，也不得继续调工具。
4. **工具抛异常 / 超时** —— 渲染成 `tool_failed` 的边界文本（「调用未成功」），**不得说成「查不到」**；可恢复类最多重试一次，且重试不得用同一组参数去撞参数校验。
5. **`create_ppt` 参数不全** —— 追问，**不启动子图**；一旦启动子图，主图不得再回到 `agent_decide`。

---

### Task 1: Agent state and budget rules

**Files:**
- Create: `backend/agent/runtime/state.py`
- Create: `backend/agent/runtime/budgets.py`
- Test: `backend/tests/agent_platform/test_runtime_budgets.py`

**Interfaces:**
- Produces: `AgentState` (TypedDict, 设计 §5.1 的九个字段 + 本仓 SSE 契约所需字段)。
- Produces: `budget_exhausted(state) -> str | None`，返回 `None` 或耗尽原因（`"steps"` / `"tool_calls"`）。
- Produces: `is_duplicate_call(state, name, args) -> bool`。
- Produces: `should_stop_research(state) -> bool`（连续两次无新增 Evidence）。
- Produces: `can_retry(state, name, args) -> bool`（可恢复故障的限次重试）。

- [x] **Step 1: Write failing budget tests**

```python
def test_budget_exhausted_reports_steps_then_tool_calls():
    assert budget_exhausted({"steps_used": 3, "tool_calls_used": 0}) is None
    assert budget_exhausted({"steps_used": 4, "tool_calls_used": 0}) == "steps"
    assert budget_exhausted({"steps_used": 1, "tool_calls_used": 6}) == "tool_calls"

def test_same_tool_same_args_is_a_duplicate_but_different_args_is_not():
    state = {"executed": [{"name": "query_course",
                           "args": {"major": "A", "course": "B"}}]}
    assert is_duplicate_call(state, "query_course", {"major": "A", "course": "B"}) is True
    assert is_duplicate_call(state, "query_course", {"major": "A", "course": "C"}) is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_budgets.py -q`
Expected: FAIL with `ModuleNotFoundError: agent.runtime.budgets`

- [x] **Step 3: Implement `state.py` and `budgets.py`**

`AgentState` 的字段：设计 §5.1 的 `messages` / `tool_calls` / `tool_results` / `evidence` / `steps_used` / `tool_calls_used` / `pending_clarification` / `task_id` / `conversation_id` / `final_answer`，**外加**本仓 SSE 契约与既有主图需要的 `question` / `history` / `owner_id` / `calls`（调用日志）/ `contexts` / `decision` / `executed`（已执行过的 `(name, args)` 列表）。

预算阈值从 `settings` 读：新增 `agent_max_steps: int = 4`、`agent_max_tool_calls: int = 6`、`agent_max_retries: int = 1`（写进 `config.py`，并保留既有的 `agent_max_calls: int = 3` 作为**每轮**截断上限，不合并）。

`is_duplicate_call` 用 `agent.core.evidence.args_hash` 比参数（与 Evidence 去重同口径）；`should_stop_research` 看 `state["tool_results"]` 末尾两条是否都 `sources == []`。

- [x] **Step 4: Run tests to verify they pass**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_budgets.py -q`
Expected: PASS

---

### Task 2: Single-model access

**Files:**
- Create: `backend/agent/runtime/model.py`
- Test: `backend/tests/agent_platform/test_runtime_model.py`

**Interfaces:**
- Consumes: `llm_client.chat_with_tools(messages, tools) -> dict`（`{"type": "tool_calls", "calls": [...]}` 或 `{"type": "text", "content": ...}`）。
- Consumes: `llm_client.stream_chat(messages, tools=None, tool_choice=None)`，异步产出 `("delta", str)` / `("calls", [...])`。
- Produces: `async decide(messages) -> dict` —— 带工具问一轮，`tools` 固定取 `registry.schemas()`。
- Produces: `async answer(messages) -> AsyncIterator[tuple[str, object]]` —— 流式产出 `("delta", str)` / `("calls", list)`。

- [x] **Step 1: Write the failing request-shape test**

断言「门必须看得见输入」：`decide()` 发出的 `tools` 与 `registry.schemas()` **同一个对象内容**（`json.dumps(..., ensure_ascii=False)` 逐字相等），而不是某个局部副本 —— 阶段2 刚栽过一次 schema 漂移。

```python
def test_decide_sends_registry_schemas_verbatim(monkeypatch):
    seen = {}
    async def fake(messages, tools):
        seen["tools"] = tools
        return {"type": "text", "content": "ok"}
    monkeypatch.setattr(model.llm_client, "chat_with_tools", fake)
    asyncio.run(model.decide([{"role": "user", "content": "问"}]))
    assert json.dumps(seen["tools"], ensure_ascii=False) == \
           json.dumps(registry.schemas(), ensure_ascii=False)
```

- [x] **Step 2: Run test to verify it fails**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_model.py -q`
Expected: FAIL with `ModuleNotFoundError: agent.runtime.model`

- [x] **Step 3: Implement `model.py`**

两个函数都**只是转发**，不改 messages、不加字段、不重排参数 —— 形状保真的唯一办法是这里什么都不做。采样参数由 `llm_client` 负责，本模块**不得**再传一份。

- [x] **Step 4: Run test to verify it passes**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_model.py -q`
Expected: PASS

---

### Task 3: `agent_decide` node

**Files:**
- Create: `backend/agent/runtime/graph.py`
- Test: `backend/tests/agent_platform/test_runtime_graph_decide.py`

**Interfaces:**
- Consumes: Task 1 的 `AgentState`、Task 2 的 `decide`。
- Produces: `async agent_decide(state) -> dict` —— 返回 `{"tool_calls": [...], "steps_used": n, "final_answer": str | None}`。
- Produces: `route_after_decide(state) -> str`，取值 `"answer"` / `"tools"` / `"ppt"` / `"clarify"`。

- [x] **Step 1: Write failing routing tests**

```python
def test_text_reply_ends_the_turn():
    assert route_after_decide({"tool_calls": [], "final_answer": "你好"}) == "answer"

def test_create_ppt_call_routes_to_the_ppt_handoff_not_back_to_decide():
    state = {"tool_calls": [{"name": "create_ppt",
                             "arguments": {"goal": "讲转专业", "audience": "大一"}}]}
    assert route_after_decide(state) == "ppt"

def test_other_calls_go_through_validate_and_execute():
    state = {"tool_calls": [{"name": "search_regulations",
                             "arguments": {"query": "转专业"}}]}
    assert route_after_decide(state) == "tools"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_decide.py -q`
Expected: FAIL with `ModuleNotFoundError`（`graph.py` 尚不存在）

- [x] **Step 3: Implement `agent_decide`**

消息按**训练形状**拼：`[{"role":"system","content": llm_client.SYSTEM_PROMPT}] + history + [{"role":"user","content": question}]`，再追加本轮之前的 `assistant(tool_calls)` / `tool(observation)` 轮 —— 追加用的是 `qa.py::_r2_messages` 的同一套写法（`arguments` 必须是 `ensure_ascii=False` 的 JSON **字符串**，`role:"tool"`，`tool_call_id` 形如 `call_N`），**不要另写一套**。

`steps_used` 每次进这个节点 +1。`create_ppt` 只按**工具名**判定路由（参数合法性留给 Task 6）。

- [x] **Step 4: Run tests to verify they pass**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_decide.py -q`
Expected: PASS

---

### Task 4: `validate_calls` / `execute_tools` / `append_evidence`

**Files:**
- Modify: `backend/agent/runtime/graph.py`
- Test: `backend/tests/agent_platform/test_runtime_graph_tools.py`

**Interfaces:**
- Consumes: `registry.call(name, args) -> ToolResult`、`registry.render(result) -> str`、`registry.schemas()`。
- Consumes: `agent.core.EvidenceLedger.add_many(tool, args, sources, retrieved_at)`。
- Produces: `async execute_tools(state) -> dict` —— 返回 `{"tool_results": [...], "tool_calls_used": n, "executed": [...], "calls": [...调用日志...], "contexts": [...]}`。

- [x] **Step 1: Write failing execution tests**

```python
def test_duplicate_call_is_blocked_and_not_counted_as_new_evidence(monkeypatch):
    # 同一 (name, args) 已执行过 → 第二次既不执行、也不产生新 Evidence
    ...

def test_failed_tool_renders_the_failure_boundary_not_no_evidence(monkeypatch):
    # registry.call 返回 tool_failed → observation 含「调用未成功」，且不含「未检索到」
    ...

def test_no_retry_with_the_same_args_after_invalid_args(monkeypatch):
    # invalid_args → 不重试；observation 回灌给模型，由模型自己决定下一步
    ...
```

- [x] **Step 2: Run tests to verify they fail**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_tools.py -q`
Expected: FAIL

- [x] **Step 3: Implement the two nodes**

每轮先按 `settings.agent_max_calls` **截断**（与今天同口径），截断必须在建消息**之前**；再按 `is_duplicate_call` 剔除重复；再按 `budget_exhausted` 剩余额度截断。**Evidence 从 `result.sources` 构造，绝不从 observation 文本解析。** 调用日志沿用今天的五个键 `{name, args, ms, hits, status}`，`hits` 用 `qa.py::_hits` 的判据（`compare_course` 取 `found_in`）。

- [x] **Step 4: Run tests to verify they pass**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_tools.py -q`
Expected: PASS

---

### Task 5: Stop rules and failure recovery

**Files:**
- Modify: `backend/agent/runtime/graph.py`
- Test: `backend/tests/agent_platform/test_runtime_graph_stop.py`

**Interfaces:**
- Produces: `async wrap_up(state) -> dict` —— 预算耗尽/停止研究时用已有 Evidence 收尾，`final_answer` 里**明说缺口**。

- [x] **Step 1: Write failing stop tests**

```python
def test_budget_exhaustion_wraps_up_with_evidence_instead_of_empty_answer():
    # steps_used 已达上限 → 走 wrap_up；答案非空且包含已检索到的来源
    ...

def test_two_consecutive_empty_tool_results_stop_research():
    # 末尾两条 tool_results 的 sources 都为空 → 不再回到 agent_decide 调工具
    ...

def test_recoverable_failure_retries_at_most_once():
    # TimeoutError → 重试一次；第二次仍失败 → 收尾，不无限重试
    ...
```

- [x] **Step 2: Run tests to verify they fail**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_stop.py -q`
Expected: FAIL

- [x] **Step 3: Implement the stop edges**

`wrap_up` **不是**新的一次模型调用兜底 —— 它把已有 observations 作为上下文，走 Task 2 的 `answer()` 让模型按「只答有据的部分 + 说缺哪一部分」收尾（`tool_choice="none"` 只能出现在这里，且必须在注释里写明：这是**收尾**，不是常规轮）。「连续两次无新增」与「预算耗尽」都进 `wrap_up`，同一条边。

- [x] **Step 4: Run tests to verify they pass**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_stop.py -q`
Expected: PASS

---

### Task 6: Model-driven `create_ppt` handoff

**Files:**
- Modify: `backend/agent/runtime/graph.py`
- Modify: `backend/graph/main_graph.py`（撤掉规则路由的调用点）
- Test: `backend/tests/agent_platform/test_runtime_graph_ppt.py`

**Interfaces:**
- Consumes: `agent.tools.create_ppt.tool` 的 `reserve_task` / `run_stream` / `owned_state` / `StateCorrupt`。
- Produces: `async ppt_handoff(state) -> dict`。

- [x] **Step 1: Write failing handoff tests**

```python
def test_missing_goal_or_audience_asks_instead_of_starting_the_subgraph(monkeypatch):
    # arguments 缺 audience → 追问，且 run_stream **一次都没被调用**
    ...

def test_valid_handoff_reserves_a_task_and_forwards_ppt_events(monkeypatch):
    # goal/audience 齐 → reserve_task 被调用一次；子图的 ("ppt", {...}) 原样转发
    ...

def test_main_graph_does_not_loop_back_after_the_subgraph_starts():
    # ppt_handoff 之后直接 END
    ...
```

- [x] **Step 2: Run tests to verify they fail**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_ppt.py -q`
Expected: FAIL

- [x] **Step 3: Implement `ppt_handoff`**

参数用 `CreatePptArgs` 校验（`registry.call("create_ppt", args)` 会做）；`invalid_args` → 追问并设 `pending_clarification`，**不建任务**。合法 → `reserve_task(owner_id, conversation_id)` 拿 `task_id`，再 `tool.run_stream(...)` 转发事件。**子图返回后直接 END。**

- [x] **Step 4: Run tests to verify they pass**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_ppt.py -q`
Expected: PASS

---

### Task 7: Compile the graph and make it the only entry

**Files:**
- Modify: `backend/agent/runtime/graph.py`
- Modify: `backend/graph/main_graph.py`（改为薄转发，或删除 —— 见 Task 8）
- Modify: `backend/routers/chat.py:12-14,106-107,150-152`
- Test: `backend/tests/agent_platform/test_runtime_graph_entry.py`

**Interfaces:**
- Produces: `build_agent_graph(*, decide=None, answer=None, ppt_stream=None)` —— 三个可注入，测试用假实现。
- Produces: `async run_stream(history, question, *, conversation_id=None, ppt_task_id=None, resume_stage=None, owner_id=None)` —— **与今天 `graph.main_graph.run_stream` 完全同形**。

- [x] **Step 1: Write the failing event-shape test**

断言事件形状与今天逐字一致：只有 `("delta", str)` / `("ppt", dict)` / `("done", {...})`，`done` 的四个键 `contexts` / `calls` / `n_refs` / `decision` 一个不少；且**没有** `done` 时必须补发一条（今天的「绝不静默返回空」）。

- [x] **Step 2: Run test to verify it fails**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_entry.py -q`
Expected: FAIL

- [x] **Step 3: Compile the graph and switch `chat.py`**

`routers/chat.py` 改为 `from agent.runtime.graph import run_stream`，并**删掉** `detect_ppt_intent(question)` 决定是否 `reserve_task` 的那一段（任务改由模型选到 `create_ppt` 时建）。`import graph` 与 `from graph.main_graph import detect_ppt_intent` 一并移除。

- [x] **Step 4: Run test to verify it passes**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests/agent_platform/test_runtime_graph_entry.py -q`
Expected: PASS

---

### Task 8: Retire the old paths and close the phase

**Files:**
- Delete: `backend/agent/runtime/qa.py`、`backend/graph/main_graph.py`、`backend/graph/__init__.py`、`backend/tests/graph/`
- Modify: `backend/tests/architecture/test_phase2_forbidden_paths.py`
- Modify: `backend/tests/agent_platform/test_runtime_registry.py`（若引用了 `qa`）
- Modify: `docs/WORK_PROGRESS.md`
- Create: `_work/agent-platform-phase3-manifest.txt`

**Interfaces:**
- Produces: 架构门新增两条 —— 生产不得 import `graph.main_graph` / `agent.runtime.qa`；`detect_ppt_intent` 不得再出现在生产代码里。

- [x] **Step 1: Add the forbidden-path assertions**

在 `test_phase2_forbidden_paths.py` 里加：`backend/graph/` 整个目录不存在；生产 AST 里不出现 `qa` / `detect_ppt_intent`。

- [x] **Step 2: Delete the replaced implementations**

只在 Task 7 的测试全绿之后删。**不留下兼容壳**（本仓铁律：不保留第二份生产代码）。删除前把文件备份到 `_work/phase3-removed-<日期>/`（该目录不可被 import，仅供回滚）。

- [x] **Step 3: Run the full suite**

Run: `D:\venv-guet-ppt\Scripts\python.exe -m pytest backend/tests backend/agent/tools -q`
Expected: 无新增失败；`tests/test_citations.py::test_block_suffix_does_not_ask_for_citation_markers` 是阶段1 起就记录的陈旧断言，保持不变。

- [x] **Step 4: Replay the frozen contracts**

三条零字节差：observation 金标准（`golden_as_observation.json`）、tools schema 冻结列（`golden_tool_schemas.json`）、`_r2_messages` 的消息形状。用假流跑一遍四类问题（查规章 / 查课程 / 跨专业对比 / 空教室），断言 `[agent] calls=` 的日志形状与今天一致。

- [x] **Step 5: Write the manifest and update progress**

记：final 文件哈希、新的图节点与边、测试数、三条回放结果、forbidden-path 扫描、遗留的已知失败。`WORK_PROGRESS.md` 标记阶段3 完成、下一步是阶段4（检索诊断体系）。

---

## 需你拍板的事（都在动生产链路，我不自行开工）

1. ~~**规则路由本轮就撤吗？**~~ **已定（2026-10-13）：本轮就撤**，按设计 §1.9 执行。
   `detect_ppt_intent` 从生产路由里移除，PPT 入口改由模型自主选择。
   **被接受的中间态**：阶段8 训出会调 `create_ppt` 的模型之前，用户说「做个 PPT」
   会走普通问答、不生成文件。设计把上线卡在阶段9，所以仓库里这样是对的。
   Task 7 Step 3 与 Task 8 Step 1 按此执行。
2. **§3 里没被指派阶段的文件谁来收？** `agent/core/telemetry.py`、`agent/prompts/{agent_system,ppt_planner,ppt_author}.py`、`api/{chat,ppt}.py`、`app.py`（即 `routers/`→`api/`、`main.py`→`app.py` 的改名）。本计划**不碰**它们 —— 要么明确归给阶段3，要么单列一个清理阶段，否则会烂在缝里。
3. **§3 目录约束有一处现在就是破的**：`训练集/_work/toolcall/build_tool_samples.py` 直接 import 了 `query_course/scripts/{repository,normalize}` 与 `query_empty_classroom/scripts/pointer`。要么给工具加公开读口，要么在设计里给训练脚本开豁免。

## Self-Review Result

- **Spec coverage**：§5.1 状态 → Task 1；§5.2 单轮与 ReAct → Task 3/4；§5.3 预算与停止 → Task 1/5；§5.4 单模型 → Task 2（且不写死模型名）；§6.1 `create_ppt` 由主 Agent 选 + 缺参先追问 → Task 6；§4.2 Evidence 台账 → Task 4；§12.2 唯一主图 → Task 7/8。§10 可观测性（`request_id`/token 用量）**本阶段不做** —— 它没有阶段归属，已列入「需你拍板」第 2 条。
- **未覆盖的 §5.3 项**：`agent_max_retries` 的「网络/超时最多重试一次」在 Task 5 有测试；「参数校验失败不得用原参数重试」在 Task 4 有测试。
- **Type consistency**：`AgentState` 字段名在 Task 1 定义、Task 3/4/5/6 使用；`registry.call` / `registry.render` / `EvidenceLedger.add_many` 的签名沿用阶段2 已发布的接口，未改名。
- **Review Focus**：五条各已指到具体任务的测试（1→Task 4、2→Task 3/4、3→Task 5、4→Task 4/5、5→Task 6）。
- **Proportion**：本计划只固定签名、归属、迁移顺序与可观察验收，不写实现体。