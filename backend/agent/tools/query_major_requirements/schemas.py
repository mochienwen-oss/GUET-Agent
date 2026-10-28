"""Input schema for major-level requirements."""
from pydantic import BaseModel, field_validator


class QueryMajorRequirementsArgs(BaseModel):
    major: str

    @field_validator("major")
    @classmethod
    def clean_major(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


PARAMETERS = {
    "type": "object",
    "properties": {
        "major": {"type": "string", "description": "专业名称"},
    },
    "required": ["major"],
}
