"""Offline contracts through the real SDKs; no gateway or model is contacted."""
import asyncio
import json
import socket
from pathlib import Path

import anthropic
import httpx
import openai
import pytest
from fastapi import FastAPI

from arena.api.routes import config, evaluation, match
from arena.db.repository import DatabaseRepository
from arena.evaluation.spec import ExperimentSpec, PreflightError, build_run_manifest, load_experiment_spec, validate_execution
from arena.llm import create_provider
from arena.llm.base import LLMError, LLMUsage
from arena.tournament.match import MatchRunner

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = 'https://gateway.invalid/v1'
TOKEN = 'offline-test-token'


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Network connections are forbidden in offline contracts')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


def use_transport(monkeypatch, provider, handler):
    module, name = (openai, 'AsyncOpenAI') if provider == 'openai' else (anthropic, 'AsyncAnthropic')
    original = getattr(module, name)
    def create(**kwargs):
        return original(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module, name, create)


def response_body(provider, usage=True):
    if provider == 'openai':
        body = {'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'resolved-model',
            'system_fingerprint': 'offline-fingerprint',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"bid":3}'}}]}
        if usage: body['usage'] = {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}
    else:
        body = {'id': 'offline', 'type': 'message', 'role': 'assistant', 'model': 'resolved-model',
            'content': [{'type': 'text', 'text': '{"bid":3}'}], 'stop_reason': 'end_turn', 'stop_sequence': None}
        if usage: body['usage'] = {'input_tokens': 12, 'output_tokens': 8}
    return body


@pytest.mark.parametrize('provider', ['openai', 'claude'])
def test_custom_gateway_request_and_usage(monkeypatch, provider):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=response_body(provider))
    use_transport(monkeypatch, provider, handle)
    base = GATEWAY if provider == 'openai' else 'https://gateway.invalid/anthropic'
    adapter = create_provider(provider, 'requested-model', TOKEN, base_url=base)
    async def run():
        try:
            assert await adapter.generate('user text', 'system text') == '{"bid":3}'
        finally:
            await adapter._client.close()
    asyncio.run(run())
    assert len(calls) == 1
    request = calls[0]
    assert request.url.host == 'gateway.invalid'
    assert request.url.path == ('/v1/chat/completions' if provider == 'openai' else '/anthropic/v1/messages')
    assert request.headers['authorization' if provider == 'openai' else 'x-api-key'] == (f'Bearer {TOKEN}' if provider == 'openai' else TOKEN)
    body = json.loads(request.content)
    assert body['model'] == 'requested-model' and body['max_tokens'] == 4096
    assert body['messages'][-1] == {'role': 'user', 'content': 'user text'}
    assert (body['messages'][0]['content'] if provider == 'openai' else body['system']) == 'system text'
    assert adapter.last_usage == LLMUsage(12, 8, 20)
    assert adapter.last_response_model == 'resolved-model'


@pytest.mark.parametrize('provider', ['openai', 'claude'])
@pytest.mark.parametrize('status', [401, 429, 500])
def test_gateway_error_has_no_hidden_retries_or_stale_usage(monkeypatch, provider, status):
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1: return httpx.Response(200, json=response_body(provider))
        return httpx.Response(status, json={'error': {'type': 'api_error', 'message': 'offline injected failure'}})
    use_transport(monkeypatch, provider, handle)
    adapter = create_provider(provider, 'requested-model', TOKEN, base_url=GATEWAY)
    async def run():
        try:
            await adapter.generate('first')
            assert adapter.last_usage is not None
            with pytest.raises(LLMError): await adapter.generate('second')
            assert adapter.last_usage is None and adapter.last_response_model is None
        finally:
            await adapter._client.close()
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize('provider', ['openai', 'claude'])
@pytest.mark.parametrize('usage_kind', ['absent', 'incomplete', 'negative'])
def test_missing_usage_preserves_text_and_unknown_cost(monkeypatch, provider, usage_kind):
    body = response_body(provider, usage=False)
    if usage_kind == 'incomplete':
        body['usage'] = {'prompt_tokens': 12} if provider == 'openai' else {'input_tokens': 12}
    elif usage_kind == 'negative':
        body['usage'] = {'prompt_tokens': 12, 'completion_tokens': -1, 'total_tokens': 11} if provider == 'openai' else {'input_tokens': 12, 'output_tokens': -1}
    use_transport(monkeypatch, provider, lambda _: httpx.Response(200, json=body))
    adapter = create_provider(provider, 'requested-model', TOKEN, base_url=GATEWAY)
    async def run():
        try:
            assert await adapter.generate('test') == '{"bid":3}'
            assert adapter.last_usage is None
        finally:
            await adapter._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('provider', ['openai', 'claude'])
def test_explicit_zero_usage_is_known(monkeypatch, provider):
    body = response_body(provider)
    body['usage'] = {key: 0 for key in body['usage']}
    use_transport(monkeypatch, provider, lambda _: httpx.Response(200, json=body))
    adapter = create_provider(provider, 'requested-model', TOKEN, base_url=GATEWAY)
    async def run():
        try:
            assert await adapter.generate('test') == '{"bid":3}'
            assert adapter.last_usage == LLMUsage(0, 0, 0)
        finally:
            await adapter._client.close()
    asyncio.run(run())


@pytest.fixture
def api_repo(tmp_path, monkeypatch):
    monkeypatch.delenv('DOUDIZHU_CREDENTIAL_MASTER_KEY', raising=False)
    repo = DatabaseRepository(str(tmp_path / 'gateway.db')); repo.init()
    app = FastAPI()
    app.state.db_repo = repo
    app.state.active_matches = {}
    app.include_router(config.router)
    app.include_router(evaluation.router)
    app.include_router(match.router)
    yield app, repo
    repo.close()


@pytest.mark.parametrize('token, available', [('${MISSING_GATEWAY_TEST_KEY}', False), ('', False), ('  ', False), (TOKEN, True)])
def test_credential_status_and_preflight_agree(api_repo, token, available):
    app, repo = api_repo
    repo.create_player_config('gateway-test', 'openai', 'requested-model', token, base_url=GATEWAY)
    raw = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml').model_dump(mode='json')
    raw['models'][0].update(config_name='gateway-test', provider='openai', model='requested-model', base_url=GATEWAY)
    raw['models'][0]['pricing'].update(input_per_million=1, output_per_million=1, source='offline-test-fixture')
    spec = ExperimentSpec.model_validate(raw)
    manifest = build_run_manifest(spec, ROOT, repo)
    if available:
        validate_execution(spec, manifest, ROOT, repo)
    else:
        with pytest.raises(PreflightError, match='credential unavailable'):
            validate_execution(spec, manifest, ROOT, repo)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            configs = (await client.get('/configs')).json()['configs']
            assert configs[0]['has_api_key'] is available
            assert 'api_key' not in configs[0]
            preflight = await client.post('/evaluations/preflight', json=raw)
            assert preflight.status_code == 200 and preflight.json()['ok'] is available
    asyncio.run(run())


@pytest.mark.parametrize('provider', ['openai', 'claude'])
def test_match_uses_configured_gateway(api_repo, monkeypatch, provider):
    app, repo = api_repo
    cid = repo.create_player_config('gateway-test', provider, 'requested-model', TOKEN, base_url=GATEWAY)
    mid = repo.create_match('offline match', {}, 'offline-seed')
    for table in ('A', 'B'):
        for seat in ('S', 'E', 'N', 'W'):
            pid = repo.create_player(cid, f'{table}-{seat}')
            repo.add_participant(mid, pid, 'red' if seat in ('S', 'N') else 'blue', **{f'seat_table_{table.lower()}': seat})
    endpoints = []
    async def fake_run(runner):
        for agent in runner.agents.values():
            endpoints.append(agent._provider._inner._base_url)
    monkeypatch.setattr(MatchRunner, 'run', fake_run)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(f'/matches/{mid}/start')
            assert response.is_success, response.text
            await asyncio.sleep(0)
    asyncio.run(run())
    assert endpoints and set(endpoints) == {GATEWAY}


def test_match_rejects_unresolved_credential_before_scheduling(api_repo):
    app, repo = api_repo
    cid = repo.create_player_config('unresolved', 'openai', 'requested-model', '${MISSING_GATEWAY_TEST_KEY}')
    mid = repo.create_match('not runnable', {}, 'seed')
    pid = repo.create_player(cid, 'player')
    repo.add_participant(mid, pid, 'red', seat_table_a='S')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(f'/matches/{mid}/start')
            assert response.status_code == 400
            assert response.json()['detail']['error']['code'] == 'CREDENTIAL_UNAVAILABLE'
    asyncio.run(run())
    assert repo.get_match(mid)['status'] == 'created'
    assert not app.state.active_matches
