"""Offline configuration check; never opens the application DB or calls a model.

Run with the doudizhu-arena conda Python and -I -B. Optional --spec validates a
fully specified experiment through the actual preflight API without creating it.
Credential values, server responses and exception details are never printed.
"""
import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def offline_guard():
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('Network disabled by configuration preflight')

    originals = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = originals


async def check_configuration(spec_path=None):
    import httpx
    import yaml
    from fastapi import FastAPI
    from arena.config.settings import settings
    from arena.agent.loader import load_agents_from_yaml
    from arena.api.routes import config, evaluation
    from arena.db.repository import DatabaseRepository
    from arena.security.credentials import has_usable_credential

    credentials = {
        key: bool(has_usable_credential(os.environ.get(key)))
        for key in ('CSTCLOUD_API_KEY', 'DOUDIZHU_CREDENTIAL_MASTER_KEY')
    }
    repo = DatabaseRepository(':memory:')
    repo.init()
    try:
        load_agents_from_yaml(repo, settings.agents_yaml_path)
        app = FastAPI()
        app.state.db_repo = repo
        app.include_router(config.router, prefix='/api/v1')
        app.include_router(evaluation.router, prefix='/api/v1')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline.invalid') as client:
            response = await client.get('/api/v1/configs')
            response.raise_for_status()
            configs = response.json()['configs']
            remote = [c for c in configs if c['provider'] in ('openai', 'claude')]
            decrypted = [c for c in repo.list_player_configs() if c['provider'] in ('openai', 'claude')]
            values = [c['api_key'] for c in decrypted if has_usable_credential(c.get('api_key'))]
            api_redacted = all('api_key' not in c for c in configs) and all(value not in response.text for value in values)
            stored = repo.conn.execute("SELECT api_key FROM player_configs WHERE provider IN ('openai','claude')").fetchall()
            encrypted = bool(stored) and all(row[0].startswith('enc:v1:') for row in stored)
            roundtrip = bool(decrypted) and all(has_usable_credential(c.get('api_key')) for c in decrypted)
            report = {
                'kind': 'offline-uni-api-preflight',
                'env_files_present': {'root': (ROOT / '.env').is_file(), 'backend': (ROOT / 'src/backend/.env').is_file()},
                'credentials_available_locally': credentials,
                'remote_config_count': len(remote),
                'all_remote_configs_have_credentials': bool(remote) and all(c['has_api_key'] for c in remote),
                'config_api_redacts_credentials': api_redacted,
                'in_memory_credentials_encrypted': encrypted,
                'credential_roundtrip_ok': roundtrip,
                'persistent_database_opened': False,
                'remote_authentication_verified': False,
            }
            report['configuration_ok'] = all(credentials.values()) and all((
                report['all_remote_configs_have_credentials'], api_redacted, encrypted, roundtrip))
            if spec_path is None:
                report['experiment_preflight'] = {'status': 'not_requested', 'ok': False,
                    'requires': ['selected_model', 'budget_usd', 'verified_pricing_and_source']}
            else:
                raw = yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
                result = await client.post('/api/v1/evaluations/preflight', json=raw)
                body = result.json()
                # Only these fields are safe to publish; validation errors can echo input.
                report['experiment_preflight'] = {
                    'status': 'checked', 'ok': result.is_success and body.get('ok') is True,
                    'http_status': result.status_code,
                }
                if result.is_success:
                    for key in ('task_count', 'seed_count', 'manifest_sha256'):
                        report['experiment_preflight'][key] = body[key]
            return report
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--output', type=Path, help='new JSON file; refuses to overwrite')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'src/backend'))
    with offline_guard() as attempts:
        previous_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            # Third-party parsers may include configuration text in errors.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                report = asyncio.run(check_configuration(args.spec))
        except Exception:
            report = {'kind': 'offline-uni-api-preflight', 'configuration_ok': False,
                      'error': 'Local configuration or experiment validation failed; details withheld.'}
        finally:
            logging.disable(previous_logging)
    report['network_connection_attempts'] = len(attempts)
    report['ok'] = report['configuration_ok'] and not attempts and (
        args.spec is None or report.get('experiment_preflight', {}).get('ok') is True)
    output = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(output)
    print(output, end='')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
