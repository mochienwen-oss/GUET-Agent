"""Input schema for comparing one course across multiple majors."""
from pydantic import BaseModel, field_validator


class CompareCourseArgs(BaseModel):
    course: str
    majors: list[str]

    @field_validator("course")
    @classmethod
    def clean_course(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("majors")
    @classmethod
    def clean_majors(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("专业名称不能为空")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("专业名称不能重复")
        if len(cleaned) < 2:
            raise ValueError("至少要两个专业才能对比")
        return cleaned


# ★ 冻结：见 `search_regulations/schemas.py` 的同名注释。
#   注意冻结块里**没有** minItems —— 「至少两个专业」由上面的校验器管，不进这份 JSON。
PARAMETERS = {
    "type": "object",
    "properties": {
        "course": {"type": "string"},
        "majors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要对比的专业列表，问句里提到的专业**一个都不能少**",
        },
    },
    "required": ["course", "majors"],
}