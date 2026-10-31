"""Product metadata and revision scope are execution actions, not prose contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.runtime.registry import ToolSpec


class BriefPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=300)
    audience: str | None = Field(default=None, min_length=1, max_length=300)
    page_count: int | None = Field(default=None, ge=3, le=20)
    duration_minutes: int | None = Field(default=None, ge=1, le=120)
    style: str | None = Field(default=None, max_length=1000)


class RevisionScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['replace', 'split', 'append', 'rebuild']
    slide_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1000,
                        description='说明用户本轮要求如何支持所选修改范围；当前页面只是上下文')


def tools(state, change_scope):
    def update_brief(**patch):
        aliases = {'title': 'goal'}
        for key, value in patch.items():
            if value is not None:
                state['brief'][aliases.get(key, key)] = value
        from agent.tools.create_ppt.scripts.master.master_workspace import MasterWorkspace
        MasterWorkspace(state['master']['project']).import_materials(state)
        return {'status': 'success', 'data': {'brief': state['brief']}}

    return [
        ToolSpec('update_presentation_brief',
                 '根据本次对话更新作品名称、受众、页数、时长或视觉偏好；只传需要更新的字段，不要求完整简报。',
                 BriefPatch, update_brief),
        ToolSpec('set_revision_scope',
                 '修改已有成品前确定执行范围并创建新版本工作副本。根据用户完整要求选择页面或整份，'
                 '不要仅因当前选中某页就忽略整份要求，也不能在用户仅要求单页修改时自行扩大范围。'
                 '范围有实质歧义时先自然语言补问。replace修改明确页面，split拆一页，append追加，rebuild重做整份。',
                 RevisionScope, change_scope),
    ]
