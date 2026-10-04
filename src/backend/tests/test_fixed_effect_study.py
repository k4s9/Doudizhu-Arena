"""Behavioral checks for the effect experiment, including deliberate corruption."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time

import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.budget import BudgetExceeded, BudgetLedger
from arena.evaluation.fixed_corpus import build_corpus, verify_corpus
from arena.evaluation.fixed_mock import FixedStudyMockProvider
from arena.evaluation.fixed_report import audit_fixed, export_fixed_report, paired_interval, summarize
from arena.evaluation.fixed_study import FixedProtocol, FixedStudyRunner
from arena.evaluation.spec import PreflightError, build_run_manifest, load_experiment_spec
from arena.llm.base import LLMError, LLMUsage

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def corpus():
    return build_corpus(["effect-dev-20260926-00"], ["effect-test-20260926-00"])


@pytest.fixture
def setup(tmp_path, corpus):
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps({"seed_set_id": "fixed-test", "seeds": corpus["test_seeds"]}))
    spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/uni-api-minimax-smoke-20260924.yaml")
    spec.seed_set_path = str(seed_path)
    spec.max_calls = 40
    spec.models[0].provider = "mock"
    spec.models[0].model = "fixed-study-mock"
    spec.models[0].base_url = "mock://local"
    spec.models[0].system_prompt = ""
    repo = DatabaseRepository(str(tmp_path / "arena.db"))
    repo.init()
    protocol = FixedProtocol(bootstrap_resamples=1000)
    def make(factory=FixedStudyMockProvider):
        return FixedStudyRunner(repo, ROOT, spec, build_run_manifest(spec, ROOT, repo), corpus, protocol,
                                provider_factory=factory)
    yield repo, spec, protocol, make
    repo.close()


def test_rule_corpus_replay_and_source_isolation(corpus):
    assert verify_corpus(corpus)["test_observations"] == 8
    assert len({i["source_match"] for i in corpus["observations"]}) == 2
    forged = deepcopy(corpus)
    forged["observations"][0]["user_prompt"] += " leaked answer"
    from arena.engine.projection import digest
    forged["sha256"] = digest({k: v for k, v in forged.items() if k != "sha256"})
    with pytest.raises(ValueError, match="replay|prompt"):
        verify_corpus(forged)


def test_shared_first_physical_budget_and_paired_outcomes(setup, corpus, tmp_path):
    repo, _, _, make = setup
    runner = make()
    run_id = asyncio.run(runner.run(mock=True))
    audit, rows, calls, _ = audit_fixed(repo, run_id, corpus, root=ROOT)
    assert audit["complete"], audit
    assert audit["source_version_check"] == "matched"
    # Eight first calls. Seven playing failures need two generic retries and
    # one feedback retry each. The single bidding state succeeds immediately.
    assert len(calls) == 8 + 7 * 3 == 29
    assert repo.conn.execute("SELECT COUNT(*) FROM budget_ledger").fetchone()[0] == 29
    for oid in {r["observation_id"] for r in rows}:
        group = [r for r in rows if r["observation_id"] == oid]
        assert len(group) == 3
        assert len({r["call_ids"][0] for r in group}) == 1
    assert sum(r["success"] for r in rows if r["arm"] == "rule_feedback") == 8
    assert sum(r["success"] for r in rows if r["arm"] == "generic_retry") == 1
    summary = export_fixed_report(repo, run_id, corpus, tmp_path / "report", root=ROOT)
    assert summary["paired"]["playing"]["success_difference_percentage_points"]["estimate"] == 100
    assert summary["paired"]["playing"]["success_difference_percentage_points"]["independent_seeds"] == 1
    assert summary["physical_calls"] < sum(r["calls"] for r in rows)


@pytest.mark.parametrize("mutation", ["raw", "prompt", "label", "model", "ledger", "orphan", "first_link"])
def test_audit_rejects_corrupted_evidence(setup, corpus, mutation):
    repo, _, _, make = setup
    rid = asyncio.run(make().run(mock=True))
    cid = repo.conn.execute("SELECT call_id FROM fixed_calls WHERE arm='shared_first' LIMIT 1").fetchone()[0]
    if mutation == "raw":
        repo.conn.execute("UPDATE call_evidence SET raw_output='tampered' WHERE call_id=?", (cid,))
    elif mutation == "prompt":
        repo.conn.execute("UPDATE call_evidence SET user_prompt=user_prompt || ' answer hint' WHERE call_id=?", (cid,))
    elif mutation == "label":
        repo.conn.execute("UPDATE fixed_calls SET validation_json=? WHERE call_id=?", (json.dumps({"valid": True}), cid))
    elif mutation == "model":
        repo.conn.execute("UPDATE llm_call_logs SET model='unfrozen-model' WHERE id=?", (cid,))
    elif mutation == "ledger":
        repo.conn.execute("UPDATE budget_ledger SET actual_usd=99 WHERE call_id=?", (cid,))
    elif mutation == "orphan":
        repo.conn.execute("UPDATE budget_ledger SET call_id='missing' WHERE call_id=?", (cid,))
    else:
        row = repo.conn.execute("SELECT observation_id,outcome_json FROM fixed_items LIMIT 1").fetchone()
        outcome = json.loads(row[1]); outcome["first_call_id"] = "wrong-first-response"
        repo.conn.execute("UPDATE fixed_items SET outcome_json=? WHERE observation_id=?", (json.dumps(outcome), row[0]))
    repo.conn.commit()
    assert not audit_fixed(repo, rid, corpus)[0]["complete"]


def test_budget_stop_produces_partial_failed_run_without_extra_calls(setup, corpus, monkeypatch):
    repo, _, _, make = setup
    reserve = BudgetLedger.reserve
    def stop_after_one(self, system, user):
        if self.repo.conn.execute("SELECT COUNT(*) FROM budget_ledger").fetchone()[0]:
            raise BudgetExceeded("injected cap")
        return reserve(self, system, user)
    monkeypatch.setattr(BudgetLedger, "reserve", stop_after_one)
    rid = asyncio.run(make().run(mock=True))
    assert repo.get_evaluation_run(rid)["status"] == "failed"
    assert repo.conn.execute("SELECT COUNT(*) FROM budget_ledger").fetchone()[0] == 1
    assert not audit_fixed(repo, rid, corpus)[0]["complete"]


def test_late_provider_is_fenced_and_usage_stays_unknown(setup):
    repo, spec, protocol, make = setup
    spec.cancellation_deadline_seconds = protocol.cancellation_deadline_seconds = .01
    protocol.call_timeout_seconds = .01
    entered = asyncio.Event()
    class Late(FixedStudyMockProvider):
        last_usage = None
        async def generate(self, user_prompt, system_prompt=""):
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(.06)
                return '{"bid":0}'
    async def scenario():
        runner = make(Late)
        task = asyncio.create_task(runner.run(mock=True))
        await entered.wait()
        before = time.monotonic()
        runner.cancel()
        rid = await task
        assert time.monotonic() - before < .2
        before_rows = [tuple(r) for r in repo.conn.execute("SELECT * FROM llm_call_logs")]
        await asyncio.sleep(.1)
        assert [tuple(r) for r in repo.conn.execute("SELECT * FROM llm_call_logs")] == before_rows
        assert repo.get_evaluation_run(rid)["status"] == "cancelled"
        assert all(r[0] == "unknown" and r[1] is None for r in repo.conn.execute("SELECT status,actual_usd FROM budget_ledger"))
    asyncio.run(scenario())


def test_explicit_mode_and_frozen_controls_required(setup):
    repo, spec, _, make = setup
    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(make().run())
    assert repo.conn.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0
    spec.variants[1].retry_limit = 1
    with pytest.raises(ValueError, match="controls"):
        make()


def test_real_mode_rejects_injected_provider_before_creating_run(setup):
    repo, _, _, make = setup
    with pytest.raises(PreflightError, match="injected provider_factory"):
        asyncio.run(make().run(real_models=True))
    assert repo.conn.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM budget_ledger").fetchone()[0] == 0


def test_provider_failures_receive_identical_generic_feedback_and_unknown_usage(setup, corpus):
    repo, _, _, make = setup
    class Unavailable(FixedStudyMockProvider):
        last_usage = None
        async def generate(self, user_prompt, system_prompt=""):
            raise LLMError("injected unavailable endpoint")
    rid = asyncio.run(make(Unavailable).run(mock=True))
    audit, rows, calls, _ = audit_fixed(repo, rid, corpus)
    assert audit["complete"], audit
    assert len(calls) == 40
    assert audit["usage_known_calls"] == 0
    assert all(not r["success"] for r in rows)
    assert all("次重试反馈" not in c["user_prompt"] for c in calls)
    for oid in {r["observation_id"] for r in rows}:
        for attempt in (2, 3):
            pair = [c for c in calls if c["decision_id"] == oid and c["attempt"] == attempt]
            assert len(pair) == 2 and pair[0]["user_prompt"] == pair[1]["user_prompt"]


def test_cluster_interval_counts_seeds_and_keeps_null_results():
    interval = paired_interval([0, 100, -100, 0], "fixed", 1000)
    assert interval["estimate"] == 0
    assert interval["independent_seeds"] == 4
    assert interval["ci95"][0] < 0 < interval["ci95"][1]
    null = paired_interval([0] * 24, "fixed", 1000)
    assert null["degenerate"] and null["estimate"] == 0


def test_report_distinguishes_unchecked_and_mismatched_source(setup, corpus, monkeypatch):
    repo, _, _, make = setup
    rid = asyncio.run(make().run(mock=True))
    unchecked, _, _, _ = audit_fixed(repo, rid, corpus)
    assert unchecked["complete"] and unchecked["source_version_check"] == "not_checked"
    assert unchecked["runtime_source_sha256"] is None
    monkeypatch.setattr("arena.evaluation.fixed_report.source_sha256", lambda root: "different-source")
    integrity, rows, calls, manifest = audit_fixed(repo, rid, corpus, root=ROOT)
    assert not integrity["complete"] and integrity["source_version_check"] == "mismatch"
    assert integrity["runtime_source_sha256"] != integrity["frozen_source_sha256"]
    summary, _ = summarize(rows, manifest, integrity, calls)
    assert summary["source_version_check"] == "mismatch"
    assert summary["paired"]["playing"]["success_difference_percentage_points"] is None


@pytest.mark.parametrize("metadata,excluded_reason,single_name_pairs", [
    ("complete", None, 7),
    ("no_fingerprints", None, 7),
    ("missing_name", "missing_response_model", 6),
    ("changed_name", "mixed_response_models", 6),
    ("missing_fingerprint", "partial_fingerprint_coverage", 7),
    ("changed_fingerprint", "mixed_fingerprints", 7),
])
def test_response_identity_groups_require_complete_pair_metadata(
    setup, corpus, metadata, excluded_reason, single_name_pairs,
):
    repo, _, _, make = setup
    rid = asyncio.run(make().run(mock=True))
    repo.conn.execute("UPDATE llm_call_logs SET system_fingerprint='fp-a' WHERE run_id=?", (rid,))
    cid = repo.conn.execute(
        "SELECT id FROM llm_call_logs WHERE run_id=? AND variant_id='generic_retry' AND phase='playing' LIMIT 1",
        (rid,),
    ).fetchone()[0]
    if metadata == "missing_name":
        repo.conn.execute("UPDATE llm_call_logs SET response_model=NULL WHERE id=?", (cid,))
    elif metadata == "changed_name":
        repo.conn.execute("UPDATE llm_call_logs SET response_model='other-response-name' WHERE id=?", (cid,))
    elif metadata == "missing_fingerprint":
        repo.conn.execute("UPDATE llm_call_logs SET system_fingerprint=NULL WHERE id=?", (cid,))
    elif metadata == "changed_fingerprint":
        repo.conn.execute("UPDATE llm_call_logs SET system_fingerprint='fp-b' WHERE id=?", (cid,))
    elif metadata == "no_fingerprints":
        repo.conn.execute("UPDATE llm_call_logs SET system_fingerprint=NULL WHERE run_id=?", (rid,))
    repo.conn.commit()

    integrity, rows, calls, manifest = audit_fixed(repo, rid, corpus)
    assert integrity["complete"], integrity
    summary, _ = summarize(rows, manifest, integrity, calls)
    paired = summary["paired"]["playing"]
    assert paired["single_returned_name_pairs"] == single_name_pairs
    assert paired["response_identity_excluded_pairs"] == ({excluded_reason: 1} if excluded_reason else {})
    groups = paired["response_identity_groups_descriptive"]
    assert len(groups) == 1 and groups[0]["pairs"] == (6 if excluded_reason else 7)
    assert groups[0]["system_fingerprint"] == (None if metadata == "no_fingerprints" else "fp-a")
    assert groups[0]["metadata_scope"] == (
        "name_only_fingerprint_unavailable" if metadata == "no_fingerprints" else "name_and_fingerprint"
    )
    assert "across all recorded response identities" in paired["comparison_target"]
    # The original requested-alias sample remains intact; metadata subsets
    # neither alter its denominator nor acquire a new significance claim.
    assert paired["success_difference_percentage_points"]["estimate"] == 100
    assert "ci95" not in groups[0]
    coverage = summary["response_identity_coverage"]
    assert coverage["physical_calls"] == 29
    assert coverage["response_model_unknown_calls"] == (1 if metadata == "missing_name" else 0)
    assert coverage["fingerprint_unknown_calls"] == (
        29 if metadata == "no_fingerprints" else (1 if metadata == "missing_fingerprint" else 0)
    )


@pytest.mark.parametrize("overrun", ["input", "output"])
def test_token_overrun_stops_run_even_with_free_pricing(setup, corpus, tmp_path, overrun):
    repo, spec, _, make = setup
    invoked = []
    class Overrun(FixedStudyMockProvider):
        last_usage = LLMUsage(
            spec.max_input_tokens + 1 if overrun == "input" else 20,
            spec.models[0].parameters.max_tokens + 1 if overrun == "output" else 10,
            0,
        )

        async def generate(self, user_prompt, system_prompt=""):
            invoked.append(user_prompt)
            return await super().generate(user_prompt, system_prompt)

    rid = asyncio.run(make(Overrun).run(mock=True))
    assert repo.get_evaluation_run(rid)["status"] == "failed"
    assert len(invoked) == 1
    evidence = repo.conn.execute("SELECT raw_output,provider_status FROM call_evidence").fetchall()
    assert len(evidence) == 1 and evidence[0][0] and evidence[0][1] == "returned"
    assert repo.conn.execute("SELECT COUNT(*) FROM budget_ledger").fetchone()[0] == 1
    summary = export_fixed_report(repo, rid, corpus, tmp_path / "nonconforming-report", root=ROOT)
    integrity = json.loads((tmp_path / "nonconforming-report/integrity.json").read_text())
    assert f"{overrun} allowance" in integrity["issues"]
    assert not summary["complete"]
    assert summary["paired"]["playing"]["success_difference_percentage_points"] is None
    assert repo.conn.execute("SELECT SUM(actual_usd) FROM budget_ledger").fetchone()[0] == 0
