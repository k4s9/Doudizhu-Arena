"""Independent runs, append-only task attempts and bounded sequential scheduling."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from ..agent.llm_agent import LLMAgent
from ..db import reliability
from ..llm.logging import LoggingLLMProvider
from ..llm.openai import OpenAIProvider
from ..llm.claude import ClaudeProvider
from ..tournament.match import MatchConfig, MatchRunner
from ..tournament.seating import assign_seating
from .budget import BudgetLedger, BudgetExceeded
from .spec import ExperimentSpec, RunManifest, PreflightError, validate_execution, canonical_hash
from .execution import ExecutionScope


class EvaluationRunner:
    def __init__(self, repo, root, active_matches=None):
        self.repo, self.root = repo, root
        self.active_matches = active_matches if active_matches is not None else {}
        self._cancel_requested = False
        self._worker = None
        self._budget = None
        self._owner = uuid.uuid4().hex
        self._cancel_event = asyncio.Event()
        self._cancel_at = None
        self._detached = set()
        self._scope = None

    def create_run(self, spec, manifest):
        # An experiment label can name revisions; each run carries its own full spec.
        experiment = self.repo.create_experiment(spec.experiment_id, spec.model_dump(mode="json"), manifest.spec_sha256)
        run_id = self.repo.create_evaluation_run(experiment, asdict(manifest), manifest.manifest_sha256)
        for variant in manifest.variants:
            for seed in manifest.seeds:
                for rotation in range(spec.seat_rotations):
                    self.repo.create_evaluation_task(run_id, variant["variant_id"], seed, rotation)
        for model in manifest.models:
            self.repo.add_model_snapshot(run_id, asdict(model))
        return run_id

    def _claim(self, run_id):
        now = time.time()
        with self.repo.atomic():
            cursor = self.repo.conn.execute("""INSERT INTO evaluation_leases VALUES(?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
                WHERE evaluation_leases.expires_at < ?""", (run_id, self._owner, now+30, now))
            if cursor.rowcount != 1:
                raise PreflightError("run already owned by a live executor")

    async def _heartbeat(self, run_id):
        while True:
            self.repo.conn.execute("UPDATE evaluation_leases SET expires_at=? WHERE run_id=? AND owner=?", (time.time()+30, run_id, self._owner))
            self.repo.conn.commit()
            await asyncio.sleep(2)

    def _recover(self, run_id):
        # Only called after acquiring an expired/unowned lease. Never claim an
        # interrupted table as finished; recovery means a new whole-task attempt.
        attempts = self.repo.conn.execute("SELECT a.* FROM task_attempts a JOIN evaluation_tasks t ON t.id=a.task_id WHERE t.run_id=? AND a.status='running'", (run_id,)).fetchall()
        for a in attempts:
            reliability.finish_attempt(self.repo, a["id"], "interrupted", reason="executor lease lost")
            self.repo.update_evaluation_task(a["task_id"], "failed", failure_reason="interrupted; rerun whole task")
            if a["match_id"]:
                self.repo.update_match_status(a["match_id"], "interrupted")
        self.repo.conn.execute("UPDATE decisions SET resolution='failed_no_action',reason='executor_lost',finished_at=? WHERE run_id=? AND resolution IS NULL", (time.time(), run_id))
        self.repo.conn.commit()

    async def run(self, run_id, spec, manifest, *, real_models=False, mock=False):
        if not real_models and not mock:
            raise ValueError("real model execution requires explicit real_models=true")
        validate_execution(spec, manifest, self.root, self.repo, mock=mock)
        stored = self.repo.get_evaluation_run(run_id)
        if not stored or stored["manifest_sha256"] != manifest.manifest_sha256:
            raise PreflightError("run manifest mismatch")
        self._claim(run_id)
        self._recover(run_id)
        model = manifest.models[0]
        self._budget = BudgetLedger(self.repo, run_id, spec.budget_limit_usd, spec.max_calls,
                                    spec.max_input_tokens, model.parameters["max_tokens"], model.pricing)
        self._mock = mock
        self.repo.update_evaluation_run_status(run_id, "running")
        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        try:
            for task in self.repo.get_evaluation_tasks(run_id):
                if task["status"] == "finished":
                    continue
                if self._cancel_requested or self._budget.stopped:
                    self.repo.update_evaluation_task(task["id"], "cancelled", failure_reason="not started: cancellation or budget limit")
                    continue
                task["attempt_id"] = reliability.start_attempt(self.repo, task["id"])
                match_id, status, reason = None, "finished", None
                try:
                    scope = ExecutionScope()
                    self._scope = scope
                    self._worker = asyncio.create_task(self._run_task(task, spec, manifest, scope))
                    match_id = await self._wait_worker(spec, scope, task['attempt_id'])
                    from .report import audit_match
                    issues = audit_match(self.repo, match_id, spec)
                    if issues:
                        raise RuntimeError("terminal audit: " + "; ".join(issues))
                except asyncio.CancelledError:
                    status, reason = "cancelled", "cancel_requested"
                    self._cancel_requested = True
                except Exception as exc:
                    status, reason = "failed", str(exc)
                    if getattr(exc, "stop_run", False): self._budget.stopped = True
                finally:
                    self._worker = None
                    row = self.repo.conn.execute("SELECT match_id FROM task_attempts WHERE id=?", (task["attempt_id"],)).fetchone()
                    match_id = match_id or row[0]
                    reliability.finish_attempt(self.repo, task["attempt_id"], status, match_id, reason)
                    self.repo.update_evaluation_task(task["id"], status, match_id=match_id, failure_reason=reason)
                    if status != "finished" and match_id:
                        self.repo.update_match_status(match_id, status)
                        self.repo.conn.execute("UPDATE table_hands SET status=? WHERE hand_id IN (SELECT id FROM hands WHERE match_id=?) AND status NOT IN ('finished','void')", (status, match_id))
                        self.repo.conn.commit()
            tasks = self.repo.get_evaluation_tasks(run_id)
            status = "cancelled" if self._cancel_requested else ("failed" if any(t["status"] != "finished" for t in tasks) else "finished")
            self.repo.update_evaluation_run_status(run_id, status)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.repo.conn.execute("UPDATE evaluation_leases SET expires_at=0 WHERE run_id=? AND owner=?", (run_id,self._owner))
            self.repo.conn.commit()
            from .report import export_report
            export_report(self.repo, run_id, Path(self.root)/"data/evaluations"/run_id)

    def cancel(self):
        self._cancel_requested = True
        if self._cancel_at is None:
            self._cancel_at = time.monotonic()
        self._cancel_event.set()
        if self._scope:
            self._scope.cancel_requested = True
        if self._budget: self._budget.stopped = True
        if self._worker: self._worker.cancel()

    async def _wait_worker(self, spec, scope, attempt_id):
        worker = self._worker
        cancelled = asyncio.create_task(self._cancel_event.wait())
        try:
            try:
                done, _ = await asyncio.wait({worker, cancelled}, timeout=spec.task_timeout_seconds,
                                             return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                self.cancel()
                done = set()
            if worker in done and not self._cancel_requested:
                return await worker
            cancellation = self._cancel_requested
            scope.cancel_requested = True
            deadline = (self._cancel_at if cancellation else time.monotonic()) + spec.cancellation_deadline_seconds
            if not worker.cancelling():
                worker.cancel()
            done, _ = await asyncio.wait({worker}, timeout=max(0, deadline-time.monotonic()))
            # Fence before publishing terminal status, even if the provider
            # suppressed cancellation and returned a value during cleanup.
            scope.revoke()
            if scope.match_runner:
                self.active_matches.pop(scope.match_runner.match_id, None)
            now = time.time()
            self.repo.conn.execute("""UPDATE decisions SET resolution=?,reason=?,finished_at=?,
                latency_ms=MAX(0,(?-started_at)*1000) WHERE task_attempt_id=? AND resolution IS NULL""",
                ('cancelled' if cancellation else 'failed_no_action',
                 'cancel_requested' if cancellation else 'task_timeout', now, now, attempt_id))
            self.repo.conn.commit()
            if not cancellation:
                self.repo.conn.execute("UPDATE decisions SET resolution='failed_no_action',reason='task_timeout' WHERE task_attempt_id=? AND resolution='cancelled' AND reason='cancel_requested'", (attempt_id,))
                self.repo.conn.commit()
            if worker in done:
                await asyncio.gather(worker, return_exceptions=True)
            else:
                self._detached.add(worker)
                def consume(task):
                    self._detached.discard(task)
                    if not task.cancelled():
                        task.exception()
                worker.add_done_callback(consume)
            if cancellation:
                raise asyncio.CancelledError
            raise TimeoutError('task deadline exceeded')
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _run_task(self, task, spec, manifest, scope):
        repo = scope.guard(self.repo)
        model = manifest.models[0]
        variant = next(v for v in spec.variants if v.variant_id == task["variant_id"])
        variant_name = {'single_generation': '单次生成', 'generic_retry': '通用重试', 'rule_feedback': '规则反馈'}[variant.variant_id]
        # Resolve only credentials from the mutable DB. All behavior is frozen.
        config = repo.get_player_config_by_name(model.config_name)
        if config is None and self._mock:
            config_id = repo.create_player_config(model.config_name, "mock", model.model, "")
            config = repo.get_player_config(config_id)
        ids, agents = [], {}
        for index in range(8):
            agent_id = repo.create_player(config["id"], f"{variant_name} P{index + 1}")
            ids.append(agent_id)
            if self._mock:
                from .mock import ReliabilityMockProvider
                inner = ReliabilityMockProvider(scenario=spec.mock_scenario)
            else:
                kwargs = {k:v for k,v in model.parameters.items() if v is not None}
                if model.provider == "claude": kwargs.pop("seed", None)
                inner = (ClaudeProvider if model.provider == "claude" else OpenAIProvider)(
                    model=model.model, api_key=config["api_key"], base_url=model.base_url, **kwargs)
            provider = LoggingLLMProvider(inner, repo=repo, agent_id=agent_id,
                                          budget=scope.guard(self._budget), execution_scope=scope)
            agent = LLMAgent(agent_id, provider, retry_limit=variant.retry_limit, rule_feedback=variant.rule_feedback,
                             system_prompt_override=model.system_prompt, prompt_name=f"player-{index}")
            agent.set_observability_context(repo, run_id=task["run_id"], variant_id=task["variant_id"], task_attempt_id=task["attempt_id"])
            agents[agent_id] = agent
        seating = assign_seating(ids[:4], ids[4:], seed=f"{task['seed']}/rotation-{task['seat_rotation']}")
        from ..engine.timeout import TimeoutConfig
        config_match = MatchConfig(total_hands=spec.total_hands, ko_enabled=spec.ko_enabled,
            max_tiebreaker_hands=spec.max_tiebreaker_hands, seed=task["seed"], timeout_config=TimeoutConfig(**spec.timeout_config),
            enable_reflection=False, enable_summary=False, persist_long_term_memory=False)
        runner = MatchRunner(config_match, seating, agents, db_repo=repo, match_name=f"{variant_name} · {task['seed']}")
        scope.match_runner = runner
        runner.match_id = repo.create_match(runner.match_name, asdict(config_match), task["seed"])
        runner.table_a.match_id = runner.table_b.match_id = runner.match_id
        repo.conn.execute("UPDATE task_attempts SET match_id=? WHERE id=?", (runner.match_id,task["attempt_id"]))
        repo.conn.commit()
        for aid in ids:
            table, seat = seating.agent_seats[aid]
            repo.add_participant(runner.match_id, aid, seating.agent_teams[aid],
                                      seat_table_a=seat if table == "A" else None, seat_table_b=seat if table == "B" else None)
        self.active_matches[runner.match_id] = runner
        try:
            await runner.run()
            return runner.match_id
        finally:
            if runner.event_bus: runner.event_bus.close()
            self.active_matches.pop(runner.match_id, None)


def _manifest_json(manifest):
    return asdict(manifest)
