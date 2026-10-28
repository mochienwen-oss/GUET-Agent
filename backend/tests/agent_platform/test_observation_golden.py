# tests/agent_platform/test_observation_golden.py —— 唯一渲染器的金标准与状态分支
#
# 背景（阶段2）：`as_observation` 加了可选 `status`。它的验收有两条硬要求：
#   ① `status=None`（默认）时，**既有成功路径的输出逐字不变** —— 老调用方一字不改；
#   ② `tool_failed` 与 `no_evidence` 的渲染**非空且不同**。
#
# ② 是本项目的红线相关项：把**技术故障**说成「学校没有这条规定」，与编造同样有害。
# 金标准由 `_work/s2_capture_golden.py` 在**改动之前**生成（9 条，覆盖四工具成功路径与边界）。
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import ToolError, ToolResult  # noqa: E402
from agent.runtime import registry  # noqa: E402

_GOLDEN = json.loads(
    Path(__file__).with_name("golden_as_observation.json").read_text(encoding="utf-8"))


def _cases():
    return [(c["name"], c["case"], c["result"], c["observation"]) for c in _GOLDEN["cases"]]


def _registered_cases():
    return [case for case in _cases() if case[0] in registry.REGISTRY]


def _result(name, data, status="success"):
    return ToolResult(tool=name, ok=True, status=status, data=data)


def test_golden_file_is_not_empty():
    """门必须先证明自己看得见输入 —— 读到 0 条要报错，不是报绿。"""
    assert len(_cases()) == 9, "金标准条数变了，先确认是不是抓取脚本出了问题"
    assert len(_registered_cases()) == 8


def test_success_rendering_matches_golden_verbatim():
    for name, case, result, want in _registered_cases():
        got = registry.render(_result(name, result))
        assert got == want, f"{name}/{case} 输出漂移：\n  期望 {want!r}\n  实得 {got!r}"


def test_partial_evidence_uses_tool_renderer_verbatim():
    for name, case, result, want in _registered_cases():
        assert registry.render(_result(name, result, "partial_evidence")) == want


def test_unknown_status_raises_instead_of_falling_back():
    with pytest.raises(ValueError):
        ToolResult(tool="search_regulations", ok=True, status="某个没定义过的状态")


def test_misspelled_tool_failed_does_not_become_no_evidence():
    """★ 最要命的一条：`tool_failed` **拼错**，绝不能静默渲染成「未检索到相关资料」。

    若未知 status 回退成常规渲染，空结果就会渲染成「未检索到相关资料」——
    技术故障被静默说成「查不到」。
    """
    good = registry.render(ToolResult(
        tool="search_regulations", ok=False, status="tool_failed",
        error=ToolError("tool_failed", "timeout")))
    assert "未检索到" not in good
    for typo in ("tool_faild", "TOOL_FAILED", "tool_failed ", "", None):
        with pytest.raises(ValueError):
            ToolResult(tool="search_regulations", ok=True, status=typo)


def test_tool_failed_and_no_evidence_are_nonempty_and_different():
    """★ 核心验收：两者都非空，且**互不相同**。"""
    tf = registry.render(ToolResult(
        tool="search_regulations", ok=False, status="tool_failed",
        error=ToolError("tool_failed", "connect timeout")))
    ne = registry.render(_result("search_regulations", {"blocks": []}, "no_evidence"))
    assert tf.strip(), "tool_failed 渲染为空"
    assert ne.strip(), "no_evidence 渲染为空"
    assert tf != ne


def test_tool_failed_wording_makes_no_fault_attribution():
    """★ 措辞**不得**无证据地声称「查询服务故障」。

    `tool_failed` 也用于「参数不合法」「工具名未注册」这类**查询服务本身没问题**的场合，
    所以只能说「调用没成功」，不能替某一方认错。安全边界（不代表学校没有规定）必须留着。
    """
    tf = registry.render(ToolResult(
        tool="query_course", ok=False, status="tool_failed",
        error=ToolError("invalid_args", "course: 参数不合法")))
    assert "未检索到" not in tf
    assert "不代表学校没有相关规定" in tf
    assert "不能据此判断" in tf
    assert "服务" not in tf, "不得把调用失败说成查询服务故障"


def test_irrelevant_result_is_its_own_wording():
    """无关结果既不等于 tool_failed，也不等于 no_evidence。"""
    ir = registry.render(_result("search_regulations", {}, "irrelevant_result"))
    ne = registry.render(_result("search_regulations", {"blocks": []}, "no_evidence"))
    tf = registry.render(ToolResult(
        tool="search_regulations", ok=False, status="tool_failed",
        error=ToolError("tool_failed", "未知原因")))
    assert ir.strip() and ir != ne and ir != tf
    assert "无关" in ir
