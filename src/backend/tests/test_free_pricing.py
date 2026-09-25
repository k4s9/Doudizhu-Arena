"""Free accounts still need explicit prices, provenance, and bounded calls."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from arena.db.repository import DatabaseRepository
from arena.evaluation.budget import BudgetExceeded, BudgetLedger
from arena.evaluation.spec import (
    ExperimentSpec, PreflightError, build_run_manifest, load_experiment_spec,
    validate_execution,
)
from arena.llm.base import LLMUsage

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo():
    repo = DatabaseRepository(':memory:')
    repo.init()
    repo.create_player_config('free-model', 'openai', 'qwen3.5', 'fake-test-token')
    yield repo
    repo.close()


def real_spec(**pricing):
    raw = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml').model_dump()
    raw['budget_limit_usd'] = 0
    raw['models'] = [{
        'config_name': 'free-model', 'provider': 'openai', 'model': 'qwen3.5',
        'base_url': 'https://gateway.invalid/v1', 'parameters': {'max_tokens': 2048},
        'pricing': {'input_per_million': 0, 'output_per_million': 0,
                    'source': 'User-confirmed free account', 'effective_date': '2026-09-24',
                    'currency': 'USD', **pricing},
    }]
    return ExperimentSpec.model_validate(raw)


def test_free_account_passes_real_execution_preflight(repo):
    spec = real_spec()
    manifest = build_run_manifest(spec, ROOT, repo)
    validate_execution(spec, manifest, ROOT, repo)
    assert manifest.frozen_spec['budget_limit_usd'] == 0
    assert manifest.models[0].pricing['source'] == 'User-confirmed free account'


@pytest.mark.parametrize('pricing', [
    {'input_per_million': 1}, {'output_per_million': 1},
    {'source': ''}, {'source': '  '}, {'effective_date': ''}, {'effective_date': '  '},
])
def test_zero_budget_rejects_paid_or_undocumented_pricing(repo, pricing):
    spec = real_spec(**pricing)
    with pytest.raises(PreflightError):
        validate_execution(spec, build_run_manifest(spec, ROOT, repo), ROOT, repo)


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_invalid_budget_rejected(value):
    raw = real_spec().model_dump()
    raw['budget_limit_usd'] = value
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(raw)


def test_free_calls_preserve_unknown_usage_and_call_limit(repo):
    ledger = BudgetLedger(repo, 'free-run', 0, 2, 2048, 2048,
                          {'input_per_million': 0, 'output_per_million': 0})
    known = ledger.reserve('system', 'user')
    ledger.settle(known, 'known-call', LLMUsage.from_counts(10, 20, 30))
    unknown = ledger.reserve('system', 'user')
    ledger.settle(unknown, 'unknown-call', None)
    rows = repo.conn.execute('SELECT actual_usd, status FROM budget_ledger ORDER BY created_at').fetchall()
    assert [tuple(row) for row in rows] == [(0, 'settled'), (None, 'unknown')]
    with pytest.raises(BudgetExceeded, match='call count'):
        ledger.reserve('system', 'user')
