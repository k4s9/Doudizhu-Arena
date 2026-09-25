"""Configuration checks with fake credentials and no provider traffic."""
import asyncio
import json
from pathlib import Path
import runpy
import socket

import pytest
import yaml

from arena.api.routes.evaluation import get_template
from arena.config.settings import settings

ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT = runpy.run_path(str(ROOT / 'scripts/preflight_uni_api.py'))
TOKEN = 'fake-uni-api-preflight-token'
MASTER = 'fake-uni-api-preflight-master-key'


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / 'agents.yaml'
    path.write_text(yaml.safe_dump({'agent_definitions': [{
        'name': 'uni-test', 'provider': 'openai', 'model': 'qwen3.5',
        'base_url': 'https://gateway.invalid/v1', 'api_key': '${CSTCLOUD_API_KEY}',
    }]}))
    monkeypatch.setattr(settings, 'agents_yaml_path', str(path))
    monkeypatch.setenv('CSTCLOUD_API_KEY', TOKEN)
    monkeypatch.setenv('DOUDIZHU_CREDENTIAL_MASTER_KEY', MASTER)
    return path


def check(spec_path=None):
    with PREFLIGHT['offline_guard']() as attempts:
        result = asyncio.run(PREFLIGHT['check_configuration'](spec_path))
    assert not attempts
    serialized = json.dumps(result)
    assert TOKEN not in serialized and MASTER not in serialized
    assert result['persistent_database_opened'] is False
    assert result['remote_authentication_verified'] is False
    return result


@pytest.mark.parametrize('token, available', [(TOKEN, True), ('', False), ('  ', False), ('${ABSENT}', False), (None, False)])
def test_configuration_status_without_credentials_in_output(config_file, monkeypatch, token, available):
    if token is None:
        monkeypatch.delenv('CSTCLOUD_API_KEY')
    else:
        monkeypatch.setenv('CSTCLOUD_API_KEY', token)
    result = check()
    assert result['configuration_ok'] is available
    assert result['credentials_available_locally']['CSTCLOUD_API_KEY'] is available
    assert result['all_remote_configs_have_credentials'] is available
    assert result['config_api_redacts_credentials'] is True
    assert result['experiment_preflight']['ok'] is False


def test_missing_master_key_is_not_ready(config_file, monkeypatch):
    monkeypatch.delenv('DOUDIZHU_CREDENTIAL_MASTER_KEY')
    result = check()
    assert result['configuration_ok'] is False
    assert result['in_memory_credentials_encrypted'] is False


@pytest.mark.parametrize('pricing_supplied', [True, False])
def test_optional_spec_uses_real_preflight_without_execution(config_file, tmp_path, pricing_supplied):
    spec = asyncio.run(get_template('real'))
    spec['budget_limit_usd'] = 1
    spec['models'] = [{
        'config_name': 'uni-test', 'provider': 'openai', 'model': 'qwen3.5',
        'base_url': 'https://gateway.invalid/v1',
        'parameters': {'max_tokens': 512, 'temperature': 0},
        'pricing': {'input_per_million': 1, 'output_per_million': 2, 'currency': 'USD',
                    'source': 'offline-fixture-only', 'effective_date': '2026-09-23'} if pricing_supplied else None,
    }]
    path = tmp_path / 'spec.yaml'
    path.write_text(yaml.safe_dump(spec))
    result = check(path)
    assert result['configuration_ok'] is True
    assert result['experiment_preflight']['ok'] is pricing_supplied
    assert result['experiment_preflight']['task_count'] == 3


def test_real_template_requires_budget_and_replaces_mock_timeouts():
    mock = asyncio.run(get_template('mock'))
    real = asyncio.run(get_template('real'))
    assert real['models'] == [] and real['budget_limit_usd'] is None
    assert real['pricing_version'] != mock['pricing_version']
    assert real['timeout_config']['bidding_seconds'] >= 60
    assert real['timeout_config']['individual_play_seconds'] >= 120
    assert real['task_timeout_seconds'] >= 1800
    assert real['variants'] == mock['variants']
    assert mock['timeout_config']['bidding_seconds'] == 2


def test_guard_records_caught_network_attempts():
    original = socket.socket.connect
    with PREFLIGHT['offline_guard']() as attempts:
        with socket.socket() as sock:
            with pytest.raises(RuntimeError, match='Network disabled'):
                sock.connect(('127.0.0.1', 1))
        assert len(attempts) == 1
    assert socket.socket.connect is original
