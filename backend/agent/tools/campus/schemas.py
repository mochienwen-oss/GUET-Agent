# -*- coding: utf-8 -*-
"""空教室查询参数：语义由模型理解，执行边界由程序检查。"""
from typing import Literal
from pydantic import BaseModel, Field


class EmptyClassroomQuery(BaseModel):
    start_date: str | None = Field(default=None, description='起始日期，YYYY-MM-DD；无法确定时不要猜')
    end_date: str | None = Field(default=None, description='结束日期，YYYY-MM-DD；未指定留空')
    weekdays: list[str] | None = Field(default=None, description='指定星期编号数组，周一至周日为 1 至 7；未指定留空')
    units: list[str] | None = Field(default=None, description='学校接口节次编号数组；未指定留空表示全天')
    building: str | None = Field(default=None, description='用户指定的教学楼名称，不知道时不要编造编号')
    room: str | None = Field(default=None, description='用户指定的教室名称或教室号')
    room_type: str | None = Field(default=None, description='教室类型：普通、电教室、语音室、机房、实验室、专业实验室、基础实验室、实验场所、其他')
    seats: int | None = Field(default=None, ge=0, description='用户明确要求的最少座位数')


class CampusArgs(BaseModel):
    service: Literal['empty_classroom'] = Field(description='校园服务，目前为查询空教室')
    conditions: list[str] = Field(default_factory=list, description='用户明确说出的原始查询条件')
    query: EmptyClassroomQuery = Field(default_factory=EmptyClassroomQuery, description='根据用户需求提取的查询条件')


class CampusContinueArgs(BaseModel):
    instruction: str = Field(description='用户补充的查询要求原文')
    query: EmptyClassroomQuery = Field(default_factory=EmptyClassroomQuery, description='本次明确修改的条件，其余不填')
    clear: list[Literal['end_date', 'weekdays', 'units', 'building', 'room', 'room_type', 'seats']] = Field(
        default_factory=list, description='用户明确取消的过滤条件，例如不限楼栋时清除 building')
