"""Single configuration source for generated text; credentials stay outside JSON."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.config import settings
from core.paths import PROJECT_ROOT

_RESERVED = {'model', 'input', 'instructions', 'messages', 'tools', 'tool_choice', 'stream',
             'text', 'response_format', 'max_tokens', 'max_completion_tokens', 'max_output_tokens',
             'api_key', 'authorization', 'headers'}


class Parameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_output_tokens: int = Field(default=2048, gt=0)
    parameters: dict = Field(default_factory=dict)

    @field_validator('parameters')
    @classmethod
    def parameters_only(cls, value):
        if {str(k).lower() for k in value} & _RESERVED:
            raise ValueError('生成参数不能覆盖消息、认证、模型、工具或输出上限字段')
        return value


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    protocol: Literal['responses', 'chat_completions']
    base_url: str
    model: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r'^[A-Z_][A-Z0-9_]*$')
    timeout_seconds: float = Field(default=120, gt=0)
    connect_timeout_seconds: float = Field(default=10, gt=0)
    parameters: dict = Field(default_factory=dict)
    structured_output: Literal['json_schema', 'json_object'] = 'json_schema'
    chat_max_tokens_field: Literal['max_tokens', 'max_completion_tokens'] = 'max_tokens'

    @field_validator('parameters')
    @classmethod
    def parameters_only(cls, value):
        return Parameters.parameters_only(value)

    @field_validator('base_url')
    @classmethod
    def base_endpoint(cls, value):
        parts = urlsplit(value)
        if (parts.scheme not in {'http', 'https'} or not parts.netloc or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError('base_url须为不含认证、query或fragment的HTTP(S)根路径')
        return value.rstrip('/')

    def api_key(self):
        value = os.environ.get(self.api_key_env)
        if not value:
            value = dotenv_values(settings.model_config['env_file']).get(self.api_key_env)
        if not value or not value.strip():
            raise ValueError('缺少API密钥环境变量：' + self.api_key_env)
        return value.strip()


class APIConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal['llm-api/1']
    active_profile: str
    profiles: dict[str, Profile]
    tasks: dict[str, Parameters]

    @model_validator(mode='after')
    def active_and_tasks(self):
        if self.active_profile not in self.profiles:
            raise ValueError('active_profile不存在')
        if set(self.tasks) != {'planning', 'title', 'grading', 'ppt'}:
            raise ValueError('tasks必须完整定义planning/title/grading/ppt')
        parameters = {**self.active.parameters, **self.tasks['planning'].parameters}
        if any(not isinstance(parameters.get(key, {}), dict) for key in ['reasoning', 'thinking']):
            raise ValueError('reasoning/thinking参数必须是对象')
        return self

    @property
    def active(self):
        return self.profiles[self.active_profile]

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=8)
def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return APIConfig.model_validate(data)
    except (OSError, ValueError, ValidationError):
        # ValidationError can echo an inline credential supplied by mistake.
        raise ValueError('LLM API配置无法加载或字段无效，请检查文件：' + str(path)) from None


def get_api_config():
    """API 配置为必需项；空路径、无效配置均明确失败。"""
    if not settings.llm_api_config_path:
        raise ValueError("LLM_API_CONFIG_PATH 不能为空；本地模型运行通道已移除")
    path = Path(settings.llm_api_config_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return _load(str(path.resolve()))
