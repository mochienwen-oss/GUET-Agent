"""Pure observation renderer for the authenticated empty-room entry point."""
from agent.core import ToolResult


def render(result: ToolResult) -> str:
    data = result.data
    return (
        "【空教室查询】实时空教室名单我这边**查不到** —— 它要登录教务系统才看得到。\n"
        "查询入口：%s\n"
        "登录后从菜单进「空闲教室」（页面地址 %s）。\n"
        "需要：%s\n"
        "（教室**借用、使用管理的规定**是另一回事，那个我能直接查。）"
        % (data["entry_url"], data["room_free_path"], data["need"])
    )
