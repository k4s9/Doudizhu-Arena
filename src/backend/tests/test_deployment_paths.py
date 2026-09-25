"""Exercise the deployable tree independently of the checkout and its .env."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from arena.config import paths
from arena.config.settings import Settings


@pytest.mark.parametrize("cwd", [".", "src/backend", "unrelated"])
def test_config_paths_do_not_depend_on_working_directory(tmp_path, monkeypatch, cwd):
    root = tmp_path / "project"
    workdir = root / cwd
    workdir.mkdir(parents=True)
    monkeypatch.setattr(paths, "PROJECT_ROOT", root)
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/arena.db")
    monkeypatch.setenv("LOG_DIR", "src/backend/logs")
    monkeypatch.setenv("AGENTS_YAML_PATH", "src/backend/arena/config/agents.yaml")
    settings = Settings()
    assert settings.database_path == str(root / "data/arena.db")
    assert settings.log_dir == str(root / "src/backend/logs")
    assert settings.agents_yaml_path == str(root / "src/backend/arena/config/agents.yaml")
    assert paths.sqlite_path(f"sqlite:///{tmp_path}/absolute.db") == str(tmp_path / "absolute.db")
    assert paths.sqlite_path("sqlite:///:memory:") == ":memory:"


@pytest.mark.parametrize("url", ["postgresql://host/db", "sqlite:///"])
def test_invalid_database_url_fails_explicitly(url):
    with pytest.raises(ValueError, match="DATABASE_URL"):
        paths.sqlite_path(url)


def test_relocated_deployment_api_cli_and_restart(tmp_path):
    root = tmp_path / "app"
    backend = root / "src/backend"
    backend.mkdir(parents=True)
    # These are the runtime resources shipped by the backend Dockerfile.
    for name in ("arena", "evaluation"):
        shutil.copytree(paths.BACKEND_DIR / name, backend / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("main.py", "pyproject.toml", "requirements.txt"):
        shutil.copy2(paths.BACKEND_DIR / name, backend / name)
    (root / "src/frontend").mkdir()
    shutil.copy2(paths.PROJECT_ROOT / "src/frontend/package-lock.json", root / "src/frontend/package-lock.json")
    (root / ".env").write_text(
        "DATABASE_URL=sqlite:///data/arena.db\nLOG_DIR=src/backend/logs\n"
        "AGENTS_YAML_PATH=src/backend/arena/config/agents.yaml\n"
        "CSTCLOUD_API_KEY=deployment-test-token\n"
        "DOUDIZHU_CREDENTIAL_MASTER_KEY=deployment-test-master-key\n"
        "LOG_LEVEL=ERROR\n", encoding="utf-8")
    (backend / ".env").write_text("LOG_LEVEL=WARNING\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in (
        "DATABASE_URL", "LOG_DIR", "AGENTS_YAML_PATH", "CSTCLOUD_API_KEY",
        "DOUDIZHU_CREDENTIAL_MASTER_KEY", "LOG_LEVEL")}
    env["LOG_LEVEL"] = "CRITICAL"  # Process environment has highest priority.
    code = r'''
import asyncio, json, socket, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'src/backend'))
attempts = []
def deny(*args, **kwargs):
    attempts.append(True)
    raise AssertionError('Deployment regression must not connect to a provider')
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
import httpx
from main import create_app
from arena.config.paths import PROJECT_ROOT
from arena.config.settings import settings
from arena.evaluation.spec import source_sha256, _dependency_hash
assert PROJECT_ROOT == root
assert settings.database_path == str(root / 'data/arena.db')
assert settings.log_level == 'CRITICAL'
async def verify():
    app = create_app()
    async with app.router.lifespan_context(app):
        repo = app.state.db_repo
        config = repo.get_player_config_by_name('Qwen-Balanced')
        assert config['api_key'] == 'deployment-test-token'
        stored = repo.conn.execute('SELECT api_key FROM player_configs WHERE id=?', (config['id'],)).fetchone()[0]
        assert stored.startswith('enc:v1:')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/v1/health')).is_success
            spec = (await client.get('/api/v1/evaluations/template')).json()
            spec['mock_scenario'] = 'play_error'
            preflight = await client.post('/api/v1/evaluations/preflight', json=spec)
            assert preflight.json()['ok'], preflight.text
            response = await client.post('/api/v1/evaluations', json=spec)
            response.raise_for_status()
            rid = response.json()['run_id']
            response = await client.post(f'/api/v1/evaluations/{rid}/start', json={})
            response.raise_for_status()
            for _ in range(1000):
                result = (await client.get(f'/api/v1/evaluations/{rid}')).json()
                if result['run']['status'] in ('finished', 'failed', 'cancelled'):
                    break
                await asyncio.sleep(.01)
            assert result['run']['status'] == 'finished', result
            artifact = await client.get(f'/api/v1/evaluations/{rid}/artifacts/integrity.json')
            assert artifact.json()['complete'], artifact.text
            assert (root / 'data/evaluations' / rid / 'manifest.json').is_file()
            manifest = (await client.get(f'/api/v1/evaluations/{rid}')).json()['run']['manifest']
            assert manifest['source_sha256'] == source_sha256(root)
            assert manifest['dependency_sha256'] == _dependency_hash(root)
            assert manifest['models'][0]['prompt_templates']
            (root / 'run-id.txt').write_text(rid)
    assert not attempts
asyncio.run(verify())
print(json.dumps({'finished': True, 'provider_connections': len(attempts)}))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(root)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["provider_connections"] == 0
    # A new interpreter simulates a service restart, including credential reload.
    restart = r'''
import asyncio, sys
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root / 'src/backend'))
from main import create_app
from arena.evaluation.report import build_report
async def verify():
    app = create_app()
    async with app.router.lifespan_context(app):
        repo = app.state.db_repo
        rid = (root / 'run-id.txt').read_text()
        assert repo.get_evaluation_run(rid)['status'] == 'finished'
        assert build_report(repo, rid)[1]['complete']
        assert repo.get_player_config_by_name('Qwen-Balanced')['api_key'] == 'deployment-test-token'
asyncio.run(verify())
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", restart, str(root)],
                            cwd=backend, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    # The CLI must read the same database and resolve output relative to the root.
    cli = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('arena.evaluation.run',run_name='__main__')"
    result = subprocess.run([
        sys.executable, "-I", "-B", "-c", cli, str(backend),
        "--spec", "src/backend/evaluation/experiments/mock-v2.yaml", "--mock",
        "--output", "data/cli-report",
    ], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((root / "data/cli-report/integrity.json").read_text())["complete"]
    assert not (tmp_path / "data").exists()
