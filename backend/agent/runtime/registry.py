"""Tool definitions share one input model with their execution validator."""
from dataclasses import asdict, dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError

from agent.core.request_control import check_cancelled
from agent.tools.campus.schemas import CampusArgs, CampusContinueArgs
from agent.tools.campus.tool import DESCRIPTION as CAMPUS_DESCRIPTION, start_campus_service, continue_campus_service
from agent.tools.document.tool import DocumentArgs, create_document
from agent.tools.search_regulations.schemas import SearchRegulationsArgs
from agent.tools.search_regulations.tool import DESCRIPTION, search_regulations_result
from agent.tools.sheets.schemas import ComputeArgs, DescribeArgs
from agent.tools.sheets.tool import COMPUTE as SHEET_COMPUTE, DESCRIBE as SHEET_DESCRIBE
from agent.tools.sheets.tool import compute_table, describe_table
from agent.runtime.user_memory import list_memories, forget_memories


class ListMemoriesArgs(BaseModel):
    offset: int = 0


class ForgetMemoriesArgs(BaseModel):
    ids: list[str] | None = None
    all_memories: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: Callable
    needs_context: bool = False

    def schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.args_schema.model_json_schema()}}


def _search(**arguments):
    from documents.catalog.document_availability import require_ready
    revision = require_ready()
    result = search_regulations_result(**arguments)
    require_ready(revision)
    return {"status": result.status, "data": result.data,
            "sources": [asdict(source) for source in result.sources],
            "source_revision": revision,
            **({"error": asdict(result.error)} if result.error else {})}


REGISTRY = {
    'list_memories': ToolSpec('list_memories', '用户询问或要求修改、忘记长期记忆时查看自己的记忆；不要凭记忆 ID 猜测。', ListMemoriesArgs, list_memories, needs_context=True),
    'forget_memories': ToolSpec('forget_memories', '仅用户明确要求忘记时删除指定记忆；仅明确要求清除全部长期记忆时使用 all_memories。', ForgetMemoriesArgs, forget_memories, needs_context=True),
    'create_document': ToolSpec('create_document', '用户明确要求生成 Word 或 Markdown 文件时，将完整内容保存为会话文件。', DocumentArgs, create_document, needs_context=True),
    'continue_campus_service': ToolSpec('continue_campus_service', '用户回答校园任务的补问或要求继续当前校园任务时使用，保留原有页面和条件。', CampusContinueArgs, continue_campus_service, needs_context=True),
    "search_regulations": ToolSpec(
        "search_regulations", DESCRIPTION, SearchRegulationsArgs, _search),
    "describe_table": ToolSpec(
        "describe_table", SHEET_DESCRIBE, DescribeArgs, describe_table, needs_context=True),
    "compute_table": ToolSpec(
        "compute_table", SHEET_COMPUTE, ComputeArgs, compute_table, needs_context=True),
    "start_campus_service": ToolSpec(
        "start_campus_service", CAMPUS_DESCRIPTION, CampusArgs, start_campus_service,
        needs_context=True),
}


def schemas():
    """普通校园问答：检索 + 表格 + 校园服务。"""
    return [spec.schema() for spec in REGISTRY.values()]


def execute(spec: ToolSpec, arguments: dict, *, runtime_context=None):
    check_cancelled()
    try:
        clean = spec.args_schema.model_validate(arguments).model_dump()
    except ValidationError as error:
        detail = "; ".join("%s: %s" % (".".join(map(str, row["loc"])), row["msg"])
                           for row in error.errors()[:3])
        return {"status": "error", "error": {"code": "invalid_args", "detail": detail}}
    try:
        if spec.needs_context:
            if runtime_context is None:
                raise ValueError('工具缺少已验证的运行上下文')
            result = spec.handler(runtime_context=runtime_context, **clean)
        else:
            result = spec.handler(**clean)
        check_cancelled()
        return result
    except (InterruptedError, TimeoutError):
        raise
    except Exception as error:
        from documents.catalog.document_availability import SourceUnavailable
        code = "source_unavailable" if isinstance(error, SourceUnavailable) else "tool_failed"
        return {"status": "error", "error": {"code": code, "detail": str(error)[:1200]}}


def call(name, args=None, *, runtime_context=None):
    spec = REGISTRY.get(name)
    if spec is None:
        return {"status": "error", "error": {"code": "unknown_tool", "detail": "未开放此工具"}}
    return execute(spec, args or {}, runtime_context=runtime_context)
