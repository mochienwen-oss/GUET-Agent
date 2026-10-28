"""Input schema for the single-major course query."""
from pydantic import BaseModel, field_validator


class QueryCourseArgs(BaseModel):
    major: str
    course: str

    @field_validator("major", "course")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


# ★ 冻结：见 `search_regulations/schemas.py` 的同名注释。改这里 = 改训练分布。
PARAMETERS = {
    "type": "object",
    "properties": {
        "major": {"type": "string", "description": "专业名称，如 计算机科学与技术"},
        "course": {"type": "string", "description": "课程名称，如 数据库系统原理"},
    },
    "required": ["major", "course"],
}