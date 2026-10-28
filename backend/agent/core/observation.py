"""The only ToolResult -> observation entry used by runtime and data builders."""
from collections.abc import Callable

from .contracts import ToolResult


Renderer = Callable[[ToolResult], str]


def _render_failure(result: ToolResult) -> str:
    detail = result.error.detail if result.error else "未知原因"
    if result.error and result.error.code == "source_unavailable":
        return ("【资料暂不可用】%s。当前不能据此作事实回答，也不代表学校没有相关规定；"
                "不要重复调用同一数据源，请说明处理状态并待管理员完成更新。" % detail)
    return (
        "【调用未成功】本次工具调用**没有成功执行**（%s）—— "
        "这**不代表学校没有相关规定**，也**不能据此判断「查不到」**。"
        "稍后可以再试一次。" % detail
    )


def render_observation(result: ToolResult, renderer: Renderer) -> str:
    """Render once; failures use the common boundary, other states stay tool-specific."""
    if result.status == "tool_failed":
        return _render_failure(result)
    text = renderer(result)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("observation renderer returned empty text for %s" % result.tool)
    return text
