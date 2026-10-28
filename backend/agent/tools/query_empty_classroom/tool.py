"""Public pointer tool. It deliberately performs no CAS login or network request."""
from langchain_core.tools import tool

from agent.core import ToolResult, Source
import json

from .schemas import QueryEmptyClassroomArgs
from .scripts.pointer import ENTRY_URL, NEED, ROOM_FREE_PATH


# ★ 冻结：逐字等于 LoRA 训练列里的 description（与 schemas.py 的 PARAMETERS 同一份来源）。
DESCRIPTION = (
    "查某个时间段**哪些教室空着**（找自习室、找空教室用）。"
    "问「哪里有空的教室」「哪间教室现在没人」「空教室怎么查」时用它。"
    "⚠️ 它**只给查询入口**，给不了实时名单（那要登录教务系统）—— "
    "所以答的时候要如实说「查不到实时的」，别编教室名。"
    "问教室**借用/使用规定**时改用 [search_regulations]。"
)


def query_empty_classroom_result() -> ToolResult:
    data = {
        "kind": "pointer", "entry_url": ENTRY_URL, "room_free_path": ROOM_FREE_PATH,
        "need": NEED, "reason": "realtime_requires_login",
    }
    return ToolResult(tool="query_empty_classroom", ok=True, status="success", data=data,
                      sources=[Source(title="空教室查询入口", text=json.dumps(data, ensure_ascii=False))])


@tool("query_empty_classroom", args_schema=QueryEmptyClassroomArgs, description=DESCRIPTION)
def query_empty_classroom_tool() -> ToolResult:
    """Return the authenticated system entry point without querying live rooms."""
    return query_empty_classroom_result()
