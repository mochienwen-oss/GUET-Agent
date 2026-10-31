# -*- coding: utf-8 -*-
"""表格工具的参数模型。"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DescribeArgs(BaseModel):
    file_id: str = Field(description="会话内文件的标识，来自附件或上一步结果")
    sheet: str | None = Field(default=None, description="工作表名；不填用第一张")


class ComputeArgs(BaseModel):
    file_id: str = Field(description="要处理的会话内文件标识")
    operation: Literal["group_stats", "weighted", "missing", "duplicates"]
    sheet: str | None = None
    group_by: list[str] = Field(default_factory=list, description="分组列，例如班级")
    values: list[str] = Field(default_factory=list, description="要统计的数值列，例如成绩")
    weights: dict[str, float] = Field(default_factory=dict,
                                      description="加权计算：{列名: 权重}，权重之和须为 1")
    id_column: str | None = Field(default=None, description="查重用的列，例如学号")

    @model_validator(mode="after")
    def check(self):
        if self.operation == "group_stats":
            if not self.group_by:
                raise ValueError("group_stats 需要 group_by（按哪一列分组）")
            if not self.values:
                raise ValueError("group_stats 需要 values（统计哪几列）")
        if self.operation == "weighted":
            if not self.weights:
                raise ValueError("weighted 需要 weights（列名到权重）")
        if self.operation == "duplicates" and not self.id_column:
            raise ValueError("duplicates 需要 id_column（按哪一列查重）")
        return self
