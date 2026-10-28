import ast
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))


def _legacy_imports() -> list[str]:
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.relative_to(BACKEND).parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "agentkit" or name.startswith("agentkit.") for name in names):
                offenders.append(str(path.relative_to(BACKEND)))
    return sorted(set(offenders))


def test_agent_is_a_package_not_a_root_module():
    assert (BACKEND / "agent" / "__init__.py").is_file()
    assert not (BACKEND / "agent.py").exists()


def test_agentkit_legacy_package_is_removed():
    assert not (BACKEND / "agentkit").exists()


def test_production_sources_do_not_import_legacy_agentkit():
    assert _legacy_imports() == []


def test_expected_public_imports_resolve():
    from agent.core import EvidenceLedger, Source, ToolResult
    from agent.runtime.graph import run_stream
    from agent.runtime.registry import call

    assert callable(run_stream)
    assert callable(call)
    assert EvidenceLedger and Source and ToolResult


def test_runtime_consumers_import_together():
    import agent
    import agent.runtime.graph
    import routers.chat

    assert Path(agent.__file__).name == "__init__.py"
