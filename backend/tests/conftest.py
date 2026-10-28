"""Explicit offline API configurations for transport regression tests."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def api_config(monkeypatch):
    from core.config import settings
    from llm import llm_api_config

    def configure(protocol='chat_completions', writer='legacy', model='test-api',
                  base_url='https://provider.invalid/v1', profile='test'):
        data = json.loads((Path(__file__).resolve().parents[2] / 'deploy/llm-api.json').read_text(encoding='utf-8'))
        data.update(active_profile=profile, writer_profile=writer)
        data['profiles'] = {profile: {'protocol': protocol, 'base_url': base_url, 'model': model,
                                     'api_key_env': 'OFFLINE_API_KEY',
                                     'parameters': {'temperature': 0, 'reasoning': {'effort': 'none'}}}}
        config = llm_api_config.APIConfig.model_validate(data)
        monkeypatch.setattr(settings, 'llm_api_config_path', 'offline-test-api.json')
        monkeypatch.setattr(llm_api_config, '_load', lambda path: config)
        monkeypatch.setenv('OFFLINE_API_KEY', 'offline-test-key')
        return config
    return configure
