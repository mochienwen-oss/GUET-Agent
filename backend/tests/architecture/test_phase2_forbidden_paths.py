"""阶段2 收尾门：旧路径必须消失，且工具只能从 tool.py / Registry 这一条路进来。

Task 7 Step 1。扫描是 **AST 级**的 —— 注释里出现旧路径名不算违规
（本仓的出处台账大量写在注释里，比如 `_work/v4/gate_survey.py` 那种引用），
只有**真 import** 才算。

反过来说：这几条一旦红，说明有人又开了一条旁路 —— 那正是本阶段要杜绝的。
"""
import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PROJECT = BACKEND.parent

#: 阶段2 搬走的根模块 → 它们的新位置（记录在这里，红了能直接告诉人搬去哪儿了）
MOVED_ROOT_MODULES = {
    "rag.py": "agent/tools/search_regulations/scripts/recall/retrieval.py",
    "vector.py": "agent/tools/search_regulations/scripts/index_ops/indexing.py",
    "bm25.py": "agent/tools/search_regulations/scripts/index_ops/bm25.py",
    "coverage.py": "agent/tools/search_regulations/scripts/evidence_ops/coverage.py",
    "flows.py": "agent/tools/search_regulations/scripts/recall/flow_expand.py",
    "refs.py": "agent/tools/search_regulations/scripts/recall/reference_follow.py",
    "intent.py": "agent/tools/search_regulations/scripts/querying/intent.py",
    "citations.py": "agent/core/citations.py",
}

#: 搬走之后**任何模块名**都不该再被 import 的顶层包
DEAD_TOP_LEVEL = frozenset(MOVED_ROOT_MODULES) | {
    name[:-3] for name in MOVED_ROOT_MODULES
} | {"tools", "agentkit"}


def _imported_modules(path: Path) -> list[str]:
    """只取真 import（AST），注释与字符串里的同名文字不算。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def _referenced_names(path: Path) -> set[str]:
    """代码里**真的引用到**的名字（AST 级）。

    ★ 为什么不用纯文本搜：本仓的注释与 docstring 里会大量**提到**这些名字
      （比如解释「为什么不用 convert_to_openai_tool」）。纯文本搜会把说明文字
      当成违规，那种门只会被人删掉。AST 里注释根本不存在，字符串是 Constant ——
      所以只统计 Name / Attribute / import 别名，正好只覆盖「真的用了」。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
            names.update(alias.name for alias in node.names)
    return names


def _has_openai_function_literal(path: Path) -> bool:
    """文件里有没有**真的**构造 `{"type": "function", ...}` 这种 schema 字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = dict(zip(node.keys, node.values))
        key = next((k for k in pairs if isinstance(k, ast.Constant) and k.value == "type"), None)
        if key is None:
            continue
        value = pairs[key]
        if isinstance(value, ast.Constant) and value.value == "function":
            return True
    return False


def _production_files():
    for path in BACKEND.rglob("*.py"):
        parts = path.relative_to(BACKEND).parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        yield path


def test_moved_root_modules_are_gone():
    leftovers = sorted(name for name in MOVED_ROOT_MODULES if (BACKEND / name).exists())
    assert leftovers == [], "这些根模块应已搬走（见本文件顶部映射表）: %s" % leftovers


def test_legacy_packages_are_gone():
    assert not (BACKEND / "tools").exists(), "旧工具包 backend/tools/ 应已删除"
    assert not (BACKEND / "ppt").exists(), "空壳 backend/ppt/ 应已删除"
    assert not (BACKEND / "graph" / "ppt").exists(), "空壳 backend/graph/ppt/ 应已删除"
    assert not (BACKEND / "agent" / "core" / "adapters.py").exists(), \
        "阶段1 的临时适配器应已删除（工具自己产出 ToolResult，不再需要它）"


def test_no_production_import_of_a_moved_or_dead_module():
    offenders = []
    for path in _production_files():
        for name in _imported_modules(path):
            if name.split(".")[0] in DEAD_TOP_LEVEL:
                offenders.append("%s -> %s" % (path.relative_to(BACKEND), name))
    assert offenders == [], offenders


def test_a_tool_never_imports_a_sibling_tools_scripts():
    """工具组合只能走对方的公开入口（`tool.py`），不得伸进兄弟工具的 `scripts/`。"""
    tools_root = BACKEND / "agent" / "tools"
    offenders = []
    for tool_dir in sorted(p for p in tools_root.iterdir() if p.is_dir()):
        for path in sorted(tool_dir.rglob("*.py")):
            rel = path.relative_to(tools_root)
            if "tests" in rel.parts or "__pycache__" in rel.parts:
                continue
            for name in _imported_modules(path):
                parts = name.split(".")
                if parts[:2] != ["agent", "tools"] or len(parts) < 4:
                    continue
                if parts[2] != tool_dir.name and "scripts" in parts:
                    offenders.append("%s -> %s" % (rel, name))
    assert offenders == [], offenders


def test_openai_tool_schemas_have_exactly_one_producer():
    """`convert_to_openai_tool` 在 `backend/` 里**一处都不该被真引用**。

    它的输出与 LoRA 训练列不同源（键序重排 + 多出 minLength/minItems），
    所以 Registry 不再用它，参数块改为各工具冻结的 `PARAMETERS`。
    谁再引入它，就是在造第二份 schema 来源。
    """
    offenders = []
    for path in _production_files():
        if "convert_to_openai_tool" in _referenced_names(path):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == [], offenders


def test_no_hand_authored_schema_literal_outside_the_frozen_blocks():
    """训练/脚本侧不许再内联整条 OpenAI schema —— 只能从 Registry 取。

    扫 `scripts/` 与 `训练集/` 里**真的构造** `{"type": "function", ...}` 的文件。
    豁免不靠白名单：`agent/tools/*/schemas.py` 的冻结 `PARAMETERS` 只写 parameters
    块，不含 `"type": "function"` 外壳，所以天然不会被这条扫到。
    """
    roots = [PROJECT / "scripts", PROJECT / "训练集"]
    offenders = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.relative_to(root).parts:
                continue
            if _has_openai_function_literal(path):
                offenders.append(str(path.relative_to(PROJECT)))
    assert offenders == [], offenders


def test_phase3_left_only_one_runtime_entry():
    """阶段3：唯一主图（设计 §5、§12.2）。

    旧的 `agent.runtime.qa`（固定两轮）与 `backend/graph/`（关键词规则路由）
    必须消失 —— 留着就是第二条运行入口，而「不建立两套运行入口」是设计原话。
    """
    assert not (BACKEND / "agent" / "runtime" / "qa.py").exists(), \
        "旧的固定两轮运行器应已删除"
    assert not (BACKEND / "graph").exists(), \
        "backend/graph/（旧主图 + 关键词路由）应已删除"


def test_production_does_not_reference_the_retired_router_or_runner():
    dead = ("agent.runtime.qa", "graph", "graph.main_graph")
    offenders = []
    for path in _production_files():
        for name in _imported_modules(path):
            if name in dead:
                offenders.append("%s -> %s" % (path.relative_to(BACKEND), name))
        names = _referenced_names(path)
        for gone in ("detect_ppt_intent", "route_decision", "build_main_graph"):
            if gone in names:
                offenders.append("%s -> %s" % (path.relative_to(BACKEND), gone))
    assert offenders == [], offenders


def test_phase2_public_imports_resolve():
    import agent
    from agent.runtime import registry

    assert Path(agent.__file__).name == "__init__.py"
    assert {row['function']['name'] for row in registry.schemas()} == {'search_regulations'}
    assert sys.modules["agent"].__name__ == "agent"
