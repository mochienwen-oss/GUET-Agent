"""The pointer tool intentionally accepts no fake date or room parameters."""
from pydantic import BaseModel


class QueryEmptyClassroomArgs(BaseModel):
    pass


# ★ 冻结：见 `search_regulations/schemas.py` 的同名注释。零参数。
PARAMETERS = {"type": "object", "properties": {}, "required": []}