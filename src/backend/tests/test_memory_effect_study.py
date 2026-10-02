"""Offline memory controls, durable evidence corruption, and CLI acceptance."""
import asyncio
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.fixed_corpus import build_corpus
from arena.evaluation.memory_study import (
    ARMS, MemoryProtocol, MemoryStudyMockProvider, MemoryStudyRunner, make_plan,
    prompts, verify_plan,
)
from arena.evaluation.memory_study_report import audit_memory_study, export_memory_report
from arena.evaluation.spec import canonical_hash

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def inputs():
    spec = importlib.util.spec_from_file_location("memory_cli", ROOT / "scripts/memory_effect_study.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    model, artifact = cli.mock_inputs()
    corpus = build_corpus(["memory-development-v1-001"], ["memory-test-v1-001"])
    return corpus, model, artifact


@pytest.fixture
def setup(tmp_path, inputs):
    corpus, model, artifact = deepcopy(inputs)
    protocol = MemoryProtocol(budget_limit_usd=0).model_dump(mode="json")
    plan = make_plan(ROOT, corpus, artifact, "固定初始策略：检查出牌合法性。", model, protocol, mock=True)
    repo = DatabaseRepository(str(tmp_path / "study.db"))
    repo.init()
    yield repo, plan
    repo.close()


def test_frozen_three_arm_calls_are_independent_and_memory_is_read_only(setup, tmp_path):
    repo, plan = setup
    before = canonical_hash(plan["memory_artifact"])
    runner = MemoryStudyRunner(repo, ROOT, plan)
    run_id = asyncio.run(runner.run(mock=True))
    audit, rows, calls, _ = audit_memory_study(repo, run_id, root=ROOT)
    assert audit["complete"], audit
    assert len(rows) == 8 * 3
    assert len(calls) == (1 + 7 * 2) * 3
    assert len({c["id"] for c in calls}) == len(calls)
    assert canonical_hash(plan["memory_artifact"]) == before
    assert all(r["final_valid"] for r in rows)
    assert repo.conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0] == 0
    item = next(i for i in plan["corpus"]["observations"] if i["split"] == "test")
    assert prompts(plan, item, "no_memory")[0] == item["system_prompt"]
    assert plan["initial_strategy"] in prompts(plan, item, "fixed_initial")[0]
    assert plan["memory_artifact"]["memories"]["player-0"] in prompts(plan, item, "frozen_memory")[0]
    report = export_memory_report(repo, run_id, tmp_path / "report", root=ROOT)
    assert report["provenance"] == "mock"
    assert report["paired_by_seed"][0]["frozen_minus_initial_final_valid"] == 0
    assert all(report["arms"][arm]["all"]["executed_fallbacks"] == 0 for arm in ARMS)


@pytest.mark.parametrize("mutation", ["raw", "prompt", "ledger", "model", "outcome", "orphan"])
def test_offline_audit_rejects_corrupted_evidence(setup, mutation):
    repo, plan = setup
    rid = asyncio.run(MemoryStudyRunner(repo, ROOT, plan).run(mock=True))
    call_id = repo.conn.execute("SELECT id FROM llm_call_logs LIMIT 1").fetchone()[0]
    if mutation == "raw":
        repo.conn.execute("UPDATE call_evidence SET raw_output='tampered' WHERE call_id=?", (call_id,))
    elif mutation == "prompt":
        repo.conn.execute("UPDATE call_evidence SET system_prompt=system_prompt || ' new memory' WHERE call_id=?", (call_id,))
    elif mutation == "ledger":
        repo.conn.execute("UPDATE budget_ledger SET actual_usd=99 WHERE call_id=?", (call_id,))
    elif mutation == "model":
        repo.conn.execute("UPDATE llm_call_logs SET model='not-frozen' WHERE id=?", (call_id,))
    elif mutation == "outcome":
        repo.conn.execute("UPDATE memory_study_decisions SET outcome_json='{}' WHERE rowid=(SELECT MIN(rowid) FROM memory_study_decisions)")
    else:
        repo.conn.execute("UPDATE budget_ledger SET call_id='missing' WHERE call_id=?", (call_id,))
    repo.conn.commit()
    assert not audit_memory_study(repo, rid, root=ROOT)[0]["complete"]


def test_invalid_outputs_require_but_never_execute_fallback(setup):
    repo, plan = setup
    class Invalid(MemoryStudyMockProvider):
        async def generate(self, user_prompt, system_prompt=""):
            return "not JSON"
    rid = asyncio.run(MemoryStudyRunner(repo, ROOT, plan, provider_factory=Invalid).run(mock=True))
    audit, rows, _, _ = audit_memory_study(repo, rid, root=ROOT)
    assert audit["complete"], audit
    assert all(r["fallback_required"] and r["executed_fallbacks"] == 0 for r in rows)
    assert all(r["retries"] == plan["protocol"]["retry_limit"] for r in rows)


def test_training_test_overlap_and_artifact_tampering_rejected(inputs):
    corpus, model, artifact = deepcopy(inputs)
    artifact["training_seeds"] = list(corpus["test_seeds"])
    artifact["training_seed_set_sha256"] = canonical_hash({"seed_set_id": artifact["training_seed_set_id"], "seeds": artifact["training_seeds"]})
    artifact["artifact_sha256"] = canonical_hash({k: v for k, v in artifact.items() if k != "artifact_sha256"})
    with pytest.raises(ValueError, match="overlap"):
        make_plan(ROOT, corpus, artifact, "initial", model, {}, mock=True)
    artifact["memories"]["player-0"] = "tampered"
    with pytest.raises(ValueError, match="artifact hash"):
        make_plan(ROOT, corpus, artifact, "initial", model, {}, mock=True)


def test_input_budget_mode_and_source_checks_precede_any_calls(setup, inputs):
    repo, plan = setup
    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(MemoryStudyRunner(repo, ROOT, plan).run())
    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(MemoryStudyRunner(repo, ROOT, plan).run(real_models=True))
    changed = deepcopy(plan)
    changed["implementation"]["source_sha256"] = "changed"
    changed["sha256"] = canonical_hash({k: v for k, v in changed.items() if k != "sha256"})
    with pytest.raises(ValueError, match="source"):
        verify_plan(changed, ROOT)
    corpus, model, artifact = deepcopy(inputs)
    with pytest.raises(ValueError, match="input allowance"):
        make_plan(ROOT, corpus, artifact, "x" * 32768, model, {}, mock=True)
    model = deepcopy(model)
    model["pricing"]["input_per_million"] = 1
    with pytest.raises(ValueError, match="budget"):
        make_plan(ROOT, corpus, artifact, "initial", model, {"budget_limit_usd": 0}, mock=True)
    assert repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs").fetchone()[0] == 0


def test_real_path_rejects_missing_credentials_without_constructing_provider(setup, inputs):
    repo, _ = setup
    corpus, model, artifact = deepcopy(inputs)
    model["provider"], model["model"], model["base_url"] = "openai", "declared-model", "https://example.invalid/v1"
    artifact["model_snapshot"] = deepcopy(model)
    artifact["provenance"] = "real"
    artifact["training_total_hands"] = 1
    artifact["memories"] = {f"player-{i}": "frozen lesson" for i in range(8)}
    artifact["runtime_model_config"] = {key: model[key] for key in ("provider", "model", "base_url")}
    artifact["runtime_model_config"]["system_prompt_sha256"] = hashlib.sha256(b"").hexdigest()
    artifact["artifact_sha256"] = canonical_hash({k: v for k, v in artifact.items() if k != "artifact_sha256"})
    plan = make_plan(ROOT, corpus, artifact, "initial", model, {}, mock=False)
    with pytest.raises(ValueError, match="credential"):
        asyncio.run(MemoryStudyRunner(repo, ROOT, plan).run(real_models=True))
    assert repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs").fetchone()[0] == 0


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", 2, "schema"),
    ("provenance", None, "provenance"),
    ("source_sha256", "missing", "fingerprint"),
    ("memories", [], "string-to-string"),
])
def test_artifact_provenance_is_validated_even_when_hash_is_recomputed(inputs, field, value, match):
    corpus, model, artifact = deepcopy(inputs)
    artifact[field] = value
    artifact["artifact_sha256"] = canonical_hash({k: v for k, v in artifact.items() if k != "artifact_sha256"})
    with pytest.raises(ValueError, match=match):
        make_plan(ROOT, corpus, artifact, "initial", model, {}, mock=True)


def test_training_endpoint_cannot_change_between_training_and_evaluation(inputs):
    corpus, model, artifact = deepcopy(inputs)
    model = deepcopy(model)
    model["base_url"] = "mock://other-model-route"
    with pytest.raises(ValueError, match="base_url"):
        make_plan(ROOT, corpus, artifact, "initial", model, {}, mock=True)


def test_per_arm_unknown_usage_retains_full_cost_reservation(setup, tmp_path):
    from decimal import Decimal

    repo, previous = setup
    model = deepcopy(previous["model"])
    model["pricing"].update(input_per_million=1, output_per_million=2)
    plan = make_plan(ROOT, previous["corpus"], previous["memory_artifact"], "initial", model,
                     {"budget_limit_usd": 3}, mock=True)
    class UnknownUsage(MemoryStudyMockProvider):
        last_usage = None
    rid = asyncio.run(MemoryStudyRunner(repo, ROOT, plan, provider_factory=UnknownUsage).run(mock=True))
    report = export_memory_report(repo, rid, tmp_path / "unknown-report", root=ROOT)
    assert report["complete"], report
    reserved = Decimal("0.033024")
    assert all(Decimal(report["arms"][arm]["all"]["accounted_cost_usd"]) == reserved * 15 for arm in ARMS)
    assert all(Decimal(report["arms"][arm]["all"]["known_cost_usd"]) == 0 for arm in ARMS)
    assert Decimal(report["accounted_usd"]) == reserved * report["physical_calls"]


def test_free_pricing_still_enforces_output_token_cap(setup):
    from arena.llm.base import LLMUsage

    repo, plan = setup
    class Overrun(MemoryStudyMockProvider):
        last_usage = LLMUsage(20, 10000, 10020)
    rid = asyncio.run(MemoryStudyRunner(repo, ROOT, plan, provider_factory=Overrun).run(mock=True))
    assert repo.get_evaluation_run(rid)["status"] == "failed"
    assert repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs").fetchone()[0] == 1
    assert not audit_memory_study(repo, rid, root=ROOT)[0]["complete"]


def test_mock_cli_prepare_run_and_read_only_audit_without_key(tmp_path):
    def cli(*args):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/memory_effect_study.py"), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])
    plan, run, audit = (tmp_path / name for name in ("plan", "run", "audit"))
    assert cli("prepare", "--mock", "--output", plan)["mock"]
    assert cli("run", "--mock", "--plan", plan, "--output", run)["complete"]
    assert cli("audit", "--run-dir", run, "--output", audit)["complete"]
    report = json.loads((run / "report/summary.json").read_text())
    assert report["provenance"] == "mock"
    assert len(report["paired_by_seed"]) == 2
    assert report["physical_calls"] == 90
