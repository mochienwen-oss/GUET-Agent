"""Input schema for filtered course lists."""
from pydantic import BaseModel, field_validator


class ListCoursesArgs(BaseModel):
    major: str
    semester: str | None = None
    module: str | None = None
    required: bool | None = None
    core_only: bool = False

    @field_validator("major")
    @classmethod
    def clean_major(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("semester", "module")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("不能为空字符串")
        return value


PARAMETERS = {
    "type": "object",
    "properties": {
        "major": {"type": "string", "description": "专业名称"},
        "semester": {"type": "string", "description": "可选，开课学期，如 3"},
        "module": {"type": "string", "description": "可选，课程模块名称"},
        "required": {"type": "boolean", "description": "可选，true 必修、false 选修"},
        "core_only": {"type": "boolean", "description": "可选，true 时只列核心课程"},
    },
    "required": ["major"],
}
