# ops-batch MCP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个 stdio MCP 服务器，对外只暴露 `ops_batch` 一个工具，把多次工具往返压成一次，并配套一个能证伪它的转录统计仪器。

**Architecture:** 核心逻辑与 MCP 传输层分离。`schema` 解析并严格校验子操作，`policy` 在 shell 子操作执行前当刹车，`ops` 执行单个子操作并返回结构化结果，`batch` 负责编排、预算与溢出，`render` 把结果渲染成模型读到的那一个 text block，`server` 只是一层把 `batch` 接到 MCP 上的薄膜。除 `server` 外所有模块都是纯函数，可脱离 MCP 直接测。

**Tech Stack:** Python 3.13、`mcp==2.3.0`（**精确钉版**）、pytest、独立 venv。

**Spec:** `docs/superpowers/specs/2026-10-15-ops-batch-mcp-design.md`

## Global Constraints

- Python 3.13；仓库根目录 `D:\guet-agent-微调\ops-mcp\`，独立 venv 在 `.venv`。
- 依赖精确钉版：`mcp==2.3.0`。**mcp 2.x 已删除 `FastMCP`**，正确入口是 `from mcp.server import MCPServer`；`server/tool` 的签名见 Task 7。
- 传输 stdio；对外**只有一个**工具，名字固定为 `ops_batch`。
- 子操作**严格扁平**：彼此不共享数据，参数全部预先写定，不支持引用上一步的结果。
- **跨调用无状态**。唯一跨调用存活的东西是溢出文件。
- 溢出文件一律落在**当前工作目录内**的 `<cwd>/.ops-spill/`。绝不写系统临时目录（工作目录之外内置 Read 会弹权限提示）。
- **失败以值返回，绝不抛异常**：子操作失败表现为该节 `status: "failed"` / `"rejected"`，其余各节照常执行并渲染。
- 策略层是**刹车，不是安全边界**。任何把它描述成安全边界的措辞都要删掉（含 README、docstring、工具描述）。
- 服务器不得打印凭据、不得回显自身 env。
- 参数严格校验：未知键必须被拒绝，且错误信息要同时点出未知键与该子操作可接受的键。
- **命名约定**：跨到模型那一边的字段名用 camelCase（`maxChars`、`outputMode`、`stopOnError`、`timeoutMs`……），与 wire JSON 逐字一致，**不要改成 snake_case**；纯内部字段用 snake_case（`spill_path`、`elapsed_ms`、`total_chars`）。`SubOp` 属于前者，`SectionResult` 属于后者。

## Review Focus

以下五类输入是 spec 隐含但没有任何任务的测试会自然覆盖、且最可能立刻咬人的。每一类都已在下面指定的任务里配了测试。

1. **非 UTF-8 / GBK 输出**（最可能立刻咬人）：本机是 Windows + cp936，中文文件名与中文输出遍地（现有转录里已经出现乱码）。`bash` 子操作与 `read` 子操作遇到 GBK 字节时必须**不崩**，且不能被静默丢成空串。
2. **超时**：一个 `bash` 子操作超时后，该节必须留下可见的超时痕迹，且不许把整个 batch 拖死。
3. **路径不存在 / 把目录当文件读**：`read`/`grep` 指向不存在路径或目录时的行为。
4. **输出只在 stderr**：命令失败、stdout 为空时，stderr 必须进入该节，不能被丢掉——丢了模型会以为命令没跑。
5. **ops 列表为空或超量**：0 个子操作、或远超预期的数量。

---

### Task 1: 转录统计仪器

spec 的验收仪器必须先落地——它是后面唯一能证伪本设计的东西，且独立于服务器本身就有用。

**Files:**
- Create: `ops-mcp/tools/__init__.py`（空文件）
- Create: `ops-mcp/tools/audit_transcripts.py`
- Create: `ops-mcp/tests/fixtures/sample_session.jsonl`
- Test: `ops-mcp/tests/test_audit_transcripts.py`

**Interfaces:**
- Produces:
  - `@dataclass ToolStat: calls: int; result_chars: int; input_chars: int; repeat_calls: int`
  - `@dataclass AuditReport: sessions: int; tools: dict[str, ToolStat]; total_result_chars: int; total_input_chars: int; null_count: int`
  - `audit(paths: list[Path]) -> AuditReport`
  - `format_report(report: AuditReport) -> str`
  - CLI: `python -m tools.audit_transcripts <glob-or-dir>`

- [ ] **Step 1: 造测试夹具**

`tests/fixtures/sample_session.jsonl`：手写 6 行，构造一个已知构成的极小会话——2 次 `Bash`（其中第 2 次与第 1 次逐字相同，用于验证重复计数）、1 次 `Read`、1 次 `Grep`，外加 1 行 `isSidechain: true` 的记录（必须被忽略）。每行形如 `{"isSidechain": false, "message": {"role": "...", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {...}}]}}`，`tool_result` 块用 `{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "..."}]}`。

- [ ] **Step 2: 写失败的测试**

```python
def test_audit_counts_and_ignores_sidechain():
    report = audit([FIXTURE])
    assert report.sessions == 1
    assert report.tools["Bash"].calls == 2
    assert report.tools["Bash"].repeat_calls == 1
    assert report.tools["Read"].calls == 1
    assert "Grep" in report.tools
    assert report.null_count == 0  # sidechain 行被忽略

def test_null_result_is_counted_not_dropped():
    # 夹具里有一条 tool_result 的 content 是空数组，必须计入 null_count
    ...

def test_format_report_mentions_every_tool():
    ...
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_audit_transcripts.py -v`
Expected: FAIL — `ImportError: cannot import name 'audit'`

- [ ] **Step 4: 实现 `tools/audit_transcripts.py`**

`audit` 逐行 `json.loads`，坏行跳过但计入 `null_count`；跳过 `isSidechain`；把 `tool_use` 的 `id→name` 与 input 长度记下，把 `tool_result` 按 `tool_use_id` 归属到工具；重复判定用 `(name, json.dumps(input, sort_keys=True))` 的哈希。`errors="replace"` 打开文件，**绝不因编码崩掉**。

- [ ] **Step 5: 在真语料上跑一遍，肉眼核对**

Run: `.venv/Scripts/python -m tools.audit_transcripts "C:/Users/Lenovo/.claude/projects/D--guet-agent---"`
Expected: 22 个会话；`Bash.calls` 约 5,300；`Read.calls` 约 814。数量级不符说明解析逻辑错了。

- [ ] **Step 6: Commit**

```bash
git add ops-mcp/tools ops-mcp/tests
git commit -m "feat(ops-mcp): land transcript audit instrument"
```

---

### Task 2: 包骨架与严格 schema

**Files:**
- Create: `ops-mcp/pyproject.toml`
- Create: `ops-mcp/ops_mcp/__init__.py`（空文件）
- Create: `ops-mcp/ops_mcp/schema.py`
- Test: `ops-mcp/tests/test_schema.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) SubOp`，字段：`op: str`；`command: str|None`；`cwd: str|None`；`timeoutMs: int|None`；`path: str|None`；`offset: int|None`；`limit: int|None`；`pattern: str|None`；`glob: list[str]|None`；`outputMode: str|None`；`caseInsensitive: bool|None`；`lineNumbers: bool|None`；`maxChars: int|None`
  - `class SchemaError(ValueError)`
  - `parse_subops(raw: object) -> list[SubOp]`
  - 常量：`MAX_OPS = 64`、`DEFAULT_MAX_CHARS = 20000`、`MAX_CHARS_CAP = 200000`、`DEFAULT_BASH_TIMEOUT_MS = 120000`、`MAX_BASH_TIMEOUT_MS = 600000`
  - 每个 op 的合法键集合：`bash={op,command,cwd,timeoutMs,maxChars}`、`read={op,path,offset,limit,maxChars}`、`grep={op,pattern,path,glob,outputMode,caseInsensitive,lineNumbers,maxChars}`、`glob={op,path,pattern,maxChars}`

- [ ] **Step 1: 建 venv 与 pyproject**

```bash
cd "D:/guet-agent-微调/ops-mcp"
python -m venv .venv
.venv/Scripts/python -m pip install "mcp==2.3.0" pytest
```

`pyproject.toml` 里写死 `requires-python = ">=3.13"` 与 `dependencies = ["mcp==2.3.0"]`。

- [ ] **Step 2: 写失败的测试**

```python
def test_unknown_key_is_rejected_and_lists_accepted_keys():
    with pytest.raises(SchemaError) as e:
        parse_subops([{"op": "bash", "command": "ls", "timeout": 5}])
    assert "timeout" in str(e.value)
    assert "timeoutMs" in str(e.value)      # 必须点出可接受的键

def test_single_string_glob_pattern_is_rejected():
    with pytest.raises(SchemaError):
        parse_subops([{"op": "glob", "path": ".", "pattern": "**/*.md"}])  # 必须是数组

def test_empty_ops_is_rejected():            # Review Focus 5
    with pytest.raises(SchemaError):
        parse_subops([])

def test_too_many_ops_is_rejected():         # Review Focus 5
    with pytest.raises(SchemaError):
        parse_subops([{"op": "read", "path": "a"}] * (MAX_OPS + 1))

def test_missing_required_field_is_named():
    with pytest.raises(SchemaError) as e:
        parse_subops([{"op": "grep"}])
    assert "pattern" in str(e.value)

def test_unknown_op_is_rejected():
    with pytest.raises(SchemaError):
        parse_subops([{"op": "exec", "command": "ls"}])

def test_maxchars_above_cap_is_rejected():
    with pytest.raises(SchemaError):
        parse_subops([{"op": "read", "path": "a", "maxChars": MAX_CHARS_CAP + 1}])

def test_defaults_are_filled():
    op = parse_subops([{"op": "bash", "command": "ls"}])[0]
    assert op.timeoutMs == DEFAULT_BASH_TIMEOUT_MS
    assert op.maxChars == DEFAULT_MAX_CHARS
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops_mcp'`

- [ ] **Step 4: 实现 `schema.py`**

每个 op 一张 `{合法键} → {必填键}` 表驱动校验；未知键报错时按字母序列出该 op 的合法键；`glob` 的 `pattern` 必须是 `list[str]`（单个字符串明确拒绝，且错误信息说明"必须是数组"）。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_schema.py -v`
Expected: PASS（9 passed）

- [ ] **Step 6: Commit**

```bash
git add ops-mcp/pyproject.toml ops-mcp/ops_mcp ops-mcp/tests/test_schema.py
git commit -m "feat(ops-mcp): strict sub-op schema"
```

---

### Task 3: 策略层（刹车）

**Files:**
- Create: `ops-mcp/ops_mcp/policy.py`
- Test: `ops-mcp/tests/test_policy.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Verdict: allowed: bool; reason: str | None`
  - `check_command(command: str) -> Verdict`
  - 常量 `DENY_PATTERNS: list[tuple[str, str]]`（正则, 理由）

- [ ] **Step 1: 写失败的测试**

```python
@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr ./build",
    "sudo rm -rf /var",
    "mkfs.ext4 /dev/sdb1",
    "dd if=/dev/zero of=/dev/sda",
    "shutdown -h now",
    "diskpart",
    "Remove-Item -Recurse -Force C:\\Windows",
])
def test_destructive_commands_are_rejected(cmd):
    v = check_command(cmd)
    assert not v.allowed and v.reason

@pytest.mark.parametrize("cmd", [
    "rm -rf .ops-spill",              # 相对且在工作区内 —— 仍拒绝（保守）
    "git status",
    "python -m pytest tests -q",
    "grep -rn 'foo' backend/",
    "ls -la",
    'echo "rm -rf /" > notes.txt',    # 出现在字符串里 —— 见 Step 4 的说明
])
def test_safe_commands_are_allowed(cmd):
    assert check_command(cmd).allowed

def test_reason_tells_the_model_what_to_do_instead():
    v = check_command("rm -rf /")
    assert "Bash" in v.reason        # 必须指向内置 Bash，把决定权交回给人
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_command'`

- [ ] **Step 3: 实现 `policy.py`**

模块 docstring 第一行必须写明：**这是刹车，不是安全边界；可被内置 Bash 或变形命令绕过，设计目标是防手滑。**

`DENY_PATTERNS` 只放**少而准**的条目：`\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+` 递归强制删除、`\bmkfs(\.\w+)?\b`、`\bdd\b.*\bof=/dev/`、`\b(shutdown|reboot|halt)\b`、`\bdiskpart\b`、`Remove-Item\b.*-Recurse`。命中即 `Verdict(False, <理由>)`。

- [ ] **Step 4: 处理字符串字面量里的假阳性**

`echo "rm -rf /" > notes.txt` 必须放行，否则正常输出会被拦。做法：先剥掉单/双引号包裹的片段再匹配，并在测试里保留这条用例与一条注释说明取舍——**宁可漏报，不可误伤**（误伤直接撞上反目标"报错导致重试"）。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_policy.py -v`
Expected: PASS（16 passed）

- [ ] **Step 6: Commit**

```bash
git add ops-mcp/ops_mcp/policy.py ops-mcp/tests/test_policy.py
git commit -m "feat(ops-mcp): policy brake for shell sub-ops"
```

---

### Task 4: 子操作执行器

**Files:**
- Create: `ops-mcp/ops_mcp/ops.py`
- Test: `ops-mcp/tests/test_ops.py`

**Interfaces:**
- Consumes: `SubOp`、`Verdict`、`check_command`（Task 2/3）
- Produces:
  - `@dataclass SectionResult: index: int; op: str; target: str; status: str; output: str; truncated: bool; spill_path: str | None; elapsed_ms: int; reason: str | None = None; total_chars: int = 0; shown_chars: int = 0`
    （`status ∈ {"completed","failed","rejected","skipped"}`；后三个字段只有 `rejected` 与截断的节用得上，其余留默认值）
  - `run_subop(subop: SubOp, cwd: Path) -> SectionResult`
  - `BASH_EXE: str | None`（`shutil.which("bash")` 的结果，模块加载时求值）

- [ ] **Step 1: 写失败的测试**

```python
def test_bash_captures_stdout(tmp_path):
    r = run_subop(bash_op("echo hello"), tmp_path)
    assert r.status == "completed" and "hello" in r.output

def test_failed_command_keeps_stderr():          # Review Focus 4
    r = run_subop(bash_op("ls /definitely/not/here"), tmp_path)
    assert r.status == "failed"
    assert r.output.strip()                       # stderr 必须进来，不能空

def test_nonzero_exit_is_failed_but_has_output():
    r = run_subop(bash_op("echo out; exit 3"), tmp_path)
    assert r.status == "failed" and "out" in r.output

def test_timeout_is_visible_and_bounded():       # Review Focus 2
    r = run_subop(bash_op("sleep 5", timeoutMs=500), tmp_path)
    assert r.status == "failed" and "timeout" in r.output.lower()

def test_gbk_output_does_not_crash_or_vanish():  # Review Focus 1
    # 让 python 以 cp936 打印中文，再读回
    r = run_subop(bash_op('python -c "print(\'培养方案\')"'), tmp_path)
    assert r.status == "completed"
    assert r.output.strip()

def test_read_range_with_line_numbers(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("\n".join(f"L{i}" for i in range(1, 11)), encoding="utf-8")
    r = run_subop(read_op(str(f), offset=3, limit=2), tmp_path)
    assert "3\tL3" in r.output and "4\tL4" in r.output and "L5" not in r.output

def test_read_missing_path_is_failed_not_raised():   # Review Focus 3
    r = run_subop(read_op(str(tmp_path / "nope.txt")), tmp_path)
    assert r.status == "failed" and r.output

def test_read_directory_is_rejected_with_hint():     # Review Focus 3
    r = run_subop(read_op(str(tmp_path)), tmp_path)
    assert r.status == "failed" and "dir" in r.output.lower()

def test_read_binary_file_is_refused(tmp_path):
    f = tmp_path / "b.bin"; f.write_bytes(b"\x00\x01\x02\xff")
    r = run_subop(read_op(str(f)), tmp_path)
    assert r.status == "failed"

def test_grep_output_modes(tmp_path):
    ...
def test_grep_skips_git_and_hidden(tmp_path):
    ...
def test_glob_patterns_and_exclusions(tmp_path):
    ...
def test_relative_paths_resolve_against_cwd(tmp_path):
    ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_ops.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_subop'`

- [ ] **Step 3: 实现 bash 执行**

`subprocess.run([BASH_EXE, "-c", command], cwd=..., capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeoutMs/1000)`。**关键：必须捕获 `TimeoutExpired` 并转成 `status="failed"` 的节，绝不外抛**（Review Focus 2）。stdout 与 stderr 都要收；stderr 非空时附在 stdout 之后（Review Focus 4）。`BASH_EXE is None` 时该节 `failed`，理由说明本机没有 bash。

`FileNotFoundError`（`cwd` 不存在）同样转成失败节。

- [ ] **Step 4: 实现 read / grep / glob**

- `read`：以 `errors="replace"` 解码；含 NUL 字节判为二进制 → `failed`；目录 → `failed` 且理由里带 "dir"；带行号输出 `"{n}\t{line}"`。
- `grep`：Python `re`，`multiline` 用 `re.S`；`outputMode` 四种形态；遍历时跳过 `.git`、隐藏目录与符号链接（**这是我钉的默认值**，避免噪音）。
- `glob`：`pathlib` 匹配，`pattern` 数组里 `!` 前缀为排除，至少需要一个正向条目；同样跳过 `.git` 与隐藏项。
- 静态文件工具的做法**不例外地遵守"失败以值返回"**：任何 `OSError`/`re.error` 都转成失败节。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_ops.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ops-mcp/ops_mcp/ops.py ops-mcp/tests/test_ops.py
git commit -m "feat(ops-mcp): sub-op executors with value-returned failures"
```

---

### Task 5: 编排、预算与溢出

**Files:**
- Create: `ops-mcp/ops_mcp/batch.py`
- Test: `ops-mcp/tests/test_batch.py`

**Interfaces:**
- Consumes: `run_subop`、`SectionResult`、`check_command`、`GLOBAL_BUDGET_CHARS`
- Produces:
  - `run_batch(subops: list[SubOp], cwd: Path, stop_on_error: bool = False) -> list[SectionResult]`
  - `spill_dir(cwd: Path) -> Path`（即 `cwd / ".ops-spill"`，顺带 `mkdir(parents=True, exist_ok=True)`）
  - `GLOBAL_BUDGET_CHARS = 60000`、`SPILL_KEEP = 50`、`SPILL_MIN_PREVIEW = 200`

- [ ] **Step 1: 写失败的测试**

```python
def test_policy_runs_before_bash_and_marks_section_rejected(tmp_path):
    sections = run_batch([bash_op("rm -rf /")], tmp_path)
    assert sections[0].status == "rejected"
    assert not (tmp_path / "spilldummy").exists()

def test_stop_on_error_halts_remaining_ops(tmp_path):
    sections = run_batch(
        [bash_op("exit 1"), bash_op("echo second")], tmp_path, stop_on_error=True)
    assert sections[0].status == "failed"
    assert sections[1].status == "skipped"

def test_default_continues_after_failure(tmp_path):
    sections = run_batch(
        [bash_op("exit 1"), bash_op("echo second")], tmp_path, stop_on_error=False)
    assert sections[1].status == "completed"

def test_oversized_section_spills_and_is_marked_truncated(tmp_path):
    sections = run_batch([bash_op("seq 1 100000", maxChars=500)], tmp_path)
    s = sections[0]
    assert s.truncated and s.spill_path
    assert Path(s.spill_path).exists()
    assert Path(s.spill_path).read_text(encoding="utf-8", errors="replace").count("\n") >= 100000
    assert len(s.output) < 5000                      # 节内只留预览

def test_global_budget_shrinks_later_sections(tmp_path):
    # 三个各 30k 的节、全局预算 60k：第 1、2 节满额，第 3 节只剩残额
    ...

def test_spill_dir_is_inside_cwd(tmp_path):
    run_batch([bash_op("seq 1 100000", maxChars=100)], tmp_path)
    assert (tmp_path / ".ops-spill").is_dir()        # 绝不在系统临时目录

def test_spill_pruning_keeps_newest_n(tmp_path):
    ...

def test_all_sections_are_returned_in_input_order(tmp_path):
    # 即使中间某节被拒绝，索引仍与输入顺序一一对应
    ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_batch.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_batch'`

- [ ] **Step 3: 实现 `run_batch`**

按序执行；`bash` 子操作执行前先 `check_command`，`rejected` 时**不执行**；`stop_on_error` 命中后其余子操作标记 `skipped` 且不执行但**仍占据索引位**（渲染时按输入顺序一一对应）。

预算规则（我钉的取值）：每节允许量 = `min(subop.maxChars, 剩余全局预算)`；剩余 ≤ 0 时该节整段溢出、只留 `SPILL_MIN_PREVIEW` 字符预览。**任何情况下都执行全部子操作**，不因预算跳过——跳过会浪费已付出的工作。

- [ ] **Step 4: 实现溢出与清理**

溢出文件名 `f"{index:02d}-{op}-{sha1(output)[:8]}.txt"`，写 `encoding="utf-8"`。每次 `run_batch` 结束后把 `.ops-spill/` 里超出 `SPILL_KEEP` 的**最旧**文件删掉（按 mtime）。清理失败不得影响结果返回。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_batch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ops-mcp/ops_mcp/batch.py ops-mcp/tests/test_batch.py
git commit -m "feat(ops-mcp): batch orchestration, budget and spill"
```

---

### Task 6: 渲染

模型读到的就是这个字符串，它是这个工具真正的契约。

**Files:**
- Create: `ops-mcp/ops_mcp/render.py`
- Test: `ops-mcp/tests/test_render.py`

**Interfaces:**
- Consumes: `SectionResult`
- Produces: `render(sections: list[SectionResult]) -> str`

- [ ] **Step 1: 写失败的测试**

```python
def test_header_carries_index_type_target_status_and_ms():
    out = render([section(index=1, op="bash", target="ls", status="completed", elapsed_ms=12)])
    assert out.splitlines()[0] == "[1/1] bash · ls · completed · 12ms"

def test_truncated_section_names_the_spill_path_and_counts():
    out = render([section(truncated=True, spill_path="D:/x/.ops-spill/01-bash-ab.txt",
                          total_chars=143210, shown_chars=20000)])
    assert "truncated" in out
    assert "20000" in out and "143210" in out
    assert "D:/x/.ops-spill/01-bash-ab.txt" in out      # 模型必须拿到路径

def test_rejected_section_points_at_builtin_bash():
    out = render([section(status="rejected", reason="recursive force delete")])
    assert "Bash" in out

def test_empty_sections_render_a_single_explicit_line():
    assert render([]) == "(ops_batch ran no operations)"

def test_sections_are_separated_by_a_blank_line():
    ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'render'`

- [ ] **Step 3: 实现 `render`**

每节形如 header + 换行 + output；截断的节在 output 后附一行路径与计数；`rejected` 的节在 output 位置写理由并指向内置 Bash。分隔：节之间一个空行，结尾不留多余空行。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ops-mcp/ops_mcp/render.py ops-mcp/tests/test_render.py ops-mcp/ops_mcp/batch.py
git commit -m "feat(ops-mcp): single-block rendering contract"
```

---

### Task 7: MCP 服务器与冒烟测试

**Files:**
- Create: `ops-mcp/ops_mcp/server.py`
- Create: `ops-mcp/ops_mcp/__main__.py`
- Test: `ops-mcp/tests/test_server.py`

**Interfaces:**
- Consumes: `parse_subops`、`run_batch`、`render`
- Produces: `build_server() -> MCPServer`；`ops_batch` 工具函数；`main() -> None`

- [ ] **Step 1: 探针——先确认这一版 SDK 的实际入口**

`mcp 2.x` 已移除 `FastMCP`（`mcp/server/fastmcp.py` 只剩一个抛 `ModuleNotFoundError` 的壳）。正确写法是：

```python
from mcp.server import MCPServer          # 2.x；不是 mcp.server.fastmcp.FastMCP
server = MCPServer(name="ops-batch", instructions="...")
@server.tool(name="ops_batch", description=TOOL_DESCRIPTION, structured_output=False)
def ops_batch(ops: list[dict], stopOnError: bool = False) -> str: ...
server.run("stdio")                        # stdio 是默认传输
```

先跑一行探针确认：`.venv/Scripts/python -c "from mcp.server import MCPServer; print(MCPServer)"`。若导入失败，以 `mcp/server/mcpserver/server.py` 里的实际 `class` 名为准，**不要**去降级到 `mcp<2`。

- [ ] **Step 2: 写失败的测试（通过 SDK 客户端走真实 stdio）**

```python
@pytest.mark.anyio
async def test_server_lists_exactly_one_tool_and_calls_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    params = StdioServerParameters(command=sys.executable, args=["-m", "ops_mcp.server"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert [t.name for t in tools] == ["ops_batch"]
            assert "batch" in tools[0].description.lower()
            res = await session.call_tool("ops_batch", {
                "ops": [{"op": "bash", "command": "echo hi"}], "stopOnError": False})
            assert "hi" in res.content[0].text

@pytest.mark.anyio
async def test_unknown_key_comes_back_as_a_visible_error(tmp_path, monkeypatch):
    # 模型传错键时必须看到错误文本，而不是静默忽略
    ...

@pytest.mark.anyio
async def test_server_never_echoes_its_own_environment(tmp_path, monkeypatch):
    # spec 的硬约束：不回显 env
    monkeypatch.setenv("OPS_MCP_TEST_CANARY", "do-not-echo")
    text = (await _call("ops_batch", {"ops": [{"op": "bash", "command": "echo hi"}]})).content[0].text
    assert "do-not-echo" not in text
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops_mcp.server'`

- [ ] **Step 4: 实现 `server.py`**

`ops_batch` 函数体只有三步：`parse_subops` → `run_batch(..., cwd=Path.cwd())` → `render`。`SchemaError` 必须被捕获并**作为返回值**交给模型（渲染成一行说明 + 该 op 可接受的键），不允许变成 MCP 层异常——异常只会给模型一句笼统失败，正是 dsh-ops 特意避免的形态。

`cwd` 取 `Path.cwd()`（服务器进程工作目录），与 spec 的"跨调用无状态"一致。

**工具描述原文**（采纳率的全部指望，逐字使用）：

```
Batch several inspection operations into ONE call. Use this instead of
several separate Bash/Read/Grep/Glob calls: it runs shell commands, file
reads, content searches and path globbing in order, and returns one
bounded, sectioned result. Prefer it whenever your next step is two or
more lookups. Each section may set maxChars; oversized output spills to a
file under <cwd>/.ops-spill/ and the section shows a bounded preview plus
the path.
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_server.py -v`
Expected: PASS

- [ ] **Step 6: 全量测试**

Run: `.venv/Scripts/python -m pytest -q`
Expected: 全绿

- [ ] **Step 7: Commit**

```bash
git add ops-mcp/ops_mcp/server.py ops-mcp/ops_mcp/__main__.py ops-mcp/tests/test_server.py
git commit -m "feat(ops-mcp): stdio MCP server exposing ops_batch"
```

---

### Task 8: 注册、CLAUDE.md 规则与 README

**Files:**
- Create: `ops-mcp/README.md`
- Modify: `C:\Users\Lenovo\.claude\CLAUDE.md`（**用户级**，跨项目生效）

**Interfaces:**
- Consumes: Task 7 的 `python -m ops_mcp.server`
- Produces: 一个可回滚的注册 + 一条采纳规则

- [ ] **Step 1: 注册到用户级并放行**

```bash
claude mcp add --help          # 先确认这一版 --scope 的写法
claude mcp add --scope user ops-batch -- "D:/guet-agent-微调/ops-mcp/.venv/Scripts/python.exe" -m ops_mcp.server
claude mcp list
```

再把 `mcp__ops-batch__ops_batch` 加进用户级 `settings.json` 的 `permissions.allow`——**这是 spec 已定的授权姿态**：MCP 的批准粒度是工具级，不预先放行就变成每次弹窗，而这个工具的全部价值就在于取代那条已经被 999 条规则静默放行的 Bash。放行的前提是 Task 3 的策略层已经在位。

期望 `claude mcp list` 里出现 `ops-batch`，且新会话调用它不再弹确认。**这两步都会改用户级配置**，执行前向人确认。

- [ ] **Step 2: 写采纳规则**

往用户级 `CLAUDE.md` 追加一节（逐字）：

```markdown
## 探索类操作优先批量执行

当下一步需要两个以上的查看、搜索或命令时，用 MCP 工具 `ops_batch` 一次调用完成，
而不是连续发多次 Bash/Read/Grep/Glob。单次操作仍用内置工具。
```

- [ ] **Step 3: 写 README**

内容必须含：这个工具是什么、为什么存在（指向 spec）、如何回滚（`claude mcp remove --scope user ops-batch` 与删掉 CLAUDE.md 那一节）、以及**策略层是刹车不是安全边界**的明确声明。

- [ ] **Step 4: 端到端验证**

新开一个 Claude Code 会话，让它"看一下 ops_mcp 目录里有哪些文件、每个文件多少行、以及 grep 一下 TOOL_DESCRIPTION"，观察它是否自发用了 `ops_batch`，以及结果是否可读。

Expected: `ops_batch` 出现且返回一份分节结果；`.ops-spill/` 在需要时被创建。

- [ ] **Step 5: Commit**

```bash
git add ops-mcp/README.md
git commit -m "docs(ops-mcp): registration, adoption rule and rollback"
```

（用户级 `CLAUDE.md` 不在本仓库内，不进这次 commit。）

---

### Task 9: A/B 任务集选择与仪器覆盖检查

spec 的验收要跑受控 A/B。**先证明仪器看得见输入**——挑出来的任务必须真的会触发批量场景，否则整场 A/B 白跑。

**Files:**
- Create: `ops-mcp/tools/select_ab_tasks.py`
- Create: `ops-mcp/tests/fixtures/ab_tasks.jsonl`
- Test: `ops-mcp/tests/test_select_ab_tasks.py`
- Create: `ops-mcp/docs/AB.md`

**Interfaces:**
- Consumes: `tools.audit_transcripts` 的解析（Task 1）
- Produces:
  - `@dataclass Candidate: session_id: str; task_index: int; batchable_calls: int; prompt_excerpt: str`
  - `select_candidates(paths: list[Path], n: int, min_batchable: int) -> list[Candidate]`
  - `coverage_check(candidates: list[Candidate]) -> str`

- [ ] **Step 1: 写失败的测试**

```python
def test_candidate_requires_minimum_batchable_calls():
    # 夹具里有一个只含 1 次调用的任务，min_batchable=3 时必须被排除
    ...

def test_coverage_check_reports_zero_when_no_candidates():
    assert coverage_check([]).startswith("0")      # 覆盖为零必须显式说出来，不能静默

def test_three_arms_are_named_in_the_report():
    ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_select_ab_tasks.py -v`
Expected: FAIL — `ImportError: cannot import name 'select_candidates'`

- [ ] **Step 3: 实现选择器**

一个"任务"按转录里的用户消息切分；`batchable_calls` 是该任务内 `Bash/Read/Grep/Glob` 的调用数之和。默认 `min_batchable=3`。输出同时打印覆盖报告：候选数、被排除数、以及**被排除的原因分布**（宁可显式说出覆盖不足，也不要静默截断）。

- [ ] **Step 4: 在真语料上跑一遍**

Run: `.venv/Scripts/python -m tools.select_ab_tasks "C:/Users/Lenovo/.claude/projects/D--guet-agent---" --n 8`
Expected: 至少 8 个候选，否则放低 `min_batchable` 并**把这次放低记进 `docs/AB.md`**。

- [ ] **Step 5: 写 `docs/AB.md`**

三臂定义（不装 / 装但无规则 / 装有规则）、每个候选任务的原始 prompt、以及三条测量：往返次数、工具参数字符总量、`ops_batch` 调用占比（自发臂与规则臂分开算）。**验收执行本身是人工操作，不是这个脚本的一部分**——脚本只负责挑任务和证明仪器覆盖。

- [ ] **Step 6: Commit**

```bash
git add ops-mcp/tools/select_ab_tasks.py ops-mcp/tests ops-mcp/docs/AB.md
git commit -m "feat(ops-mcp): A/B task selection with instrument coverage check"
```

---

## 收尾

全部任务完成后，跑一次 `.venv/Scripts/python -m pytest -q` 应全绿，然后按 `docs/AB.md` 人工执行受控 A/B。**任何一项不达预期就是设计需要改**，不是"用起来就好了"。