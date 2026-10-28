"""Input schema for school-regulation retrieval."""
from pydantic import BaseModel, Field, field_validator


class SearchRegulationsArgs(BaseModel):
    query: str
    keywords: list[str] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("keywords", mode="before")
    @classmethod
    def normalize_keywords(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("必须是字符串数组")
        out = []
        seen = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("每个关键词必须是字符串")
            item = item.strip()
            if not item or item in seen:
                continue
            if len(item) > 32:
                raise ValueError("单个关键词不能超过 32 个字符")
            seen.add(item)
            out.append(item)
        if len(out) > 12:
            raise ValueError("关键词不能超过 12 个")
        return out


# ★ v7 冻结快照：只用于复现旧四工具训练列。运行时从阶段 6 起使用下方 PARAMETERS。
TRAINING_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "检索用的问句或关键词"},
    },
    "required": ["query"],
}


# ★ 运行时与新 Agent 训练契约：完整语义 query 给 dense/reranker，keywords 给 BM25。
#
# 模型可见参数保留业务含义；Pydantic 在执行前校验空值与类型。
# 历史训练参数只用于离线导出，不决定 API 的消息序列或字段顺序。
PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "语义完整、自包含的检索问题，供语义召回和精排使用",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "供关键词召回使用的简短领域术语；没有可靠关键词时传空数组",
        },
    },
    "required": ["query"],
}
