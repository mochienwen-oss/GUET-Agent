"""Tool definitions share one input model with their execution validator."""
from dataclasses import asdict, dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError

from agent.core.request_control import check_cancelled
from agent.tools.search_regulations.schemas import SearchRegulationsArgs
from agent.tools.search_regulations.tool import DESCRIPTION, search_regulations_result


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: Callable

    def schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.args_schema.model_json_schema()}}


def _search(**arguments):
    from documents.document_availability import require_ready
    revision = require_ready()
    result = search_regulations_result(**arguments)
    require_ready(revision)
    return {"status": result.status, "data": result.data,
            "sources": [asdict(source) for source in result.sources],
            "source_revision": revision,
            **({"error": asdict(result.error)} if result.error else {})}


REGISTRY = {"search_regulations": ToolSpec(
    "search_regulations", DESCRIPTION, SearchRegulationsArgs, _search)}


def schemas():
    """Ordinary campus chat exposes retrieval only."""
    return [spec.schema() for spec in REGISTRY.values()]


def execute(spec: ToolSpec, arguments: dict):
    check_cancelled()
    try:
        clean = spec.args_schema.model_validate(arguments).model_dump()
    except ValidationError as error:
        detail = "; ".join("%s: %s" % (".".join(map(str, row["loc"])), row["msg"])
                           for row in error.errors()[:3])
        return {"status": "error", "error": {"code": "invalid_args", "detail": detail}}
    try:
        result = spec.handler(**clean)
        check_cancelled()
        return result
    except (InterruptedError, TimeoutError):
        raise
    except Exception as error:
        from documents.document_availability import SourceUnavailable
        code = "source_unavailable" if isinstance(error, SourceUnavailable) else "tool_failed"
        return {"status": "error", "error": {"code": code, "detail": str(error)[:1200]}}


def call(name, args=None):
    spec = REGISTRY.get(name)
    if spec is None:
        return {"status": "error", "error": {"code": "unknown_tool", "detail": "未开放此工具"}}
    return execute(spec, args or {})
