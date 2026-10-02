"""Sequential memory training and immutable artifact export."""

from __future__ import annotations

import asyncio
import json
import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..agent.llm_agent import LLMAgent
from ..db.repository import DatabaseRepository
from ..engine.timeout import TimeoutConfig
from ..llm.claude import ClaudeProvider
from ..llm.logging import LoggingLLMProvider
from ..llm.openai import OpenAIProvider
from ..security.credentials import has_usable_credential
from ..tournament.match import MatchConfig, MatchRunner
from ..tournament.seating import assign_seating
from .spec import (
    ExperimentSpec,
    PreflightError,
    RunManifest,
    canonical_hash,
    load_manifest,
    load_seed_set,
    source_sha256,
    _dependency_hash,
    _sdk_version,
)
from .budget import BudgetExceeded, BudgetLedger
from .execution import ExecutionScope


@dataclass(frozen=True, slots=True)
class MemoryTrainingPlan:
    experiment_id: str
    training_seed_set_id: str
    training_seed_set_sha256: str
    training_seeds: tuple[str, ...]
    variant_id: str
    variant_snapshot: dict[str, Any]
    total_hands: int
    plan_sha256: str


def build_memory_training_plan(
    spec: ExperimentSpec, manifest: RunManifest, root: str | Path,
) -> MemoryTrainingPlan:
    if not spec.training_seed_set_path:
        raise PreflightError("memory training requires training_seed_set_path")
    if not spec.memory_training_variant_id:
        raise PreflightError("memory training requires memory_training_variant_id")
    if len(manifest.models) != 1:
        raise PreflightError("memory training requires exactly one frozen model")
    seed_path = Path(spec.training_seed_set_path)
    if not seed_path.is_absolute():
        seed_path = Path(root) / seed_path
    seed_set_id, seeds, seed_hash = load_seed_set(seed_path)
    if set(seeds) & set(manifest.seeds):
        raise PreflightError("training and evaluation seed sets overlap")
    variant = next(
        variant for variant in spec.variants
        if variant.variant_id == spec.memory_training_variant_id
    )
    variant_snapshot = variant.model_dump(mode="json")
    if not variant.enable_reflection or variant.memory_mode == "disabled":
        raise PreflightError("memory training requires a reflection/memory variant")
    payload = {
        "experiment_id": spec.experiment_id,
        "training_seed_set_id": seed_set_id,
        "training_seed_set_sha256": seed_hash,
        "training_seeds": seeds,
        "variant_id": spec.memory_training_variant_id,
        "variant_snapshot": variant_snapshot,
        "total_hands": spec.training_total_hands,
        "model_snapshot": asdict(manifest.models[0]),
        "source_revision": manifest.source_revision,
        "source_dirty": manifest.source_dirty,
        "source_sha256": manifest.source_sha256,
        "dependency_sha256": manifest.dependency_sha256,
    }
    return MemoryTrainingPlan(
        experiment_id=spec.experiment_id,
        training_seed_set_id=seed_set_id,
        training_seed_set_sha256=seed_hash,
        training_seeds=seeds,
        variant_id=spec.memory_training_variant_id,
        variant_snapshot=variant_snapshot,
        total_hands=spec.training_total_hands,
        plan_sha256=canonical_hash(payload),
    )


def load_training_inputs(repo: DatabaseRepository, run_id: str):
    """Restore behavior from the saved manifest; only credentials remain live."""
    run = repo.get_evaluation_run(run_id)
    if not run:
        raise PreflightError("memory training run not found")
    saved = json.loads(run["manifest_json"])
    if (saved.get("run_kind") != "memory_training"
            or canonical_hash(saved) != run["manifest_sha256"]
            or not saved.get("base_manifest")):
        raise PreflightError("resume requires a complete original training manifest")
    manifest = load_manifest(saved["base_manifest"])
    spec = ExperimentSpec.model_validate(manifest.frozen_spec)
    raw_plan = dict(saved["plan"])
    raw_plan["training_seeds"] = tuple(raw_plan["training_seeds"])
    return spec, manifest, MemoryTrainingPlan(**raw_plan)


class TrainingBudget(BudgetLedger):
    def settle(self, reservation, call_id, usage):
        super().settle(reservation, call_id, usage)
        if usage is not None and any(value is not None and not 0 <= value <= limit
                                     for value, limit in ((usage.prompt_tokens, self.input_limit),
                                                          (usage.completion_tokens, self.output_limit))):
            self.stopped = True
            raise BudgetExceeded("training provider usage exceeds declared token allowance")


class MemoryTrainingRunner:
    """Run training seeds sequentially so long-term memory carries forward."""

    def __init__(self, repo: DatabaseRepository, root: str | Path) -> None:
        self.repo = repo
        self.root = Path(root)
        self._runtime_model_config_snapshot: dict[str, Any] | None = None
        self.budget: TrainingBudget | None = None
        self.scope = ExecutionScope()
        self.detached: set[asyncio.Task] = set()

    def create_run(
        self, spec: ExperimentSpec, manifest: RunManifest, plan: MemoryTrainingPlan,
    ) -> str:
        experiment_db_id = self.repo.create_experiment(
            spec.experiment_id, spec.model_dump(mode="json"), manifest.spec_sha256,
        )
        training_manifest = {
            "run_kind": "memory_training",
            "base_manifest_sha256": manifest.manifest_sha256,
            "base_manifest": asdict(manifest),
            "plan": asdict(plan),
            "created_at_ns": time.time_ns(),
        }
        run_manifest_sha256 = canonical_hash(training_manifest)
        run_id = self.repo.create_evaluation_run(
            experiment_db_id, training_manifest, run_manifest_sha256,
        )
        for index, seed in enumerate(plan.training_seeds):
            self.repo.create_evaluation_task(run_id, plan.variant_id, seed, index)
        for snapshot in manifest.models:
            self.repo.add_model_snapshot(run_id, asdict(snapshot))
        return run_id

    async def run(
        self, run_id: str, spec: ExperimentSpec, manifest: RunManifest,
        plan: MemoryTrainingPlan, output_path: str | Path, *, real_models: bool = False,
    ) -> dict[str, Any]:
        if not real_models:
            raise ValueError("memory training requires explicit real_models=true")
        if Path(output_path).exists():
            raise FileExistsError(f"memory artifact already exists: {output_path}")
        manifest_payload = asdict(manifest)
        manifest_payload.pop("manifest_sha256")
        if canonical_hash(manifest_payload) != manifest.manifest_sha256:
            raise PreflightError("training manifest hash mismatch")
        if (canonical_hash(spec.model_dump(mode="json")) != manifest.spec_sha256
                or source_sha256(self.root) != manifest.source_sha256):
            raise PreflightError("training spec or source differs from the frozen manifest")
        if (_dependency_hash(self.root) != manifest.dependency_sha256
                or any(_sdk_version(model.provider) != model.sdk_version for model in manifest.models)):
            raise PreflightError("training dependencies or SDK differ from the frozen manifest")
        if build_memory_training_plan(spec, manifest, self.root) != plan:
            raise PreflightError("training plan or seed file differs from the frozen manifest")
        stored_run = self.repo.get_evaluation_run(run_id)
        if not stored_run:
            raise PreflightError("memory training run not found")
        stored = json.loads(stored_run["manifest_json"])
        if (stored.get("run_kind") != "memory_training"
                or canonical_hash(stored) != stored_run["manifest_sha256"]
                or stored.get("base_manifest_sha256") != manifest.manifest_sha256
                or canonical_hash(stored.get("base_manifest")) != canonical_hash(asdict(manifest))
                or canonical_hash(stored.get("plan")) != canonical_hash(asdict(plan))):
            raise PreflightError("resume requires the original frozen training manifest and plan")
        tasks = sorted(self.repo.get_evaluation_tasks(run_id), key=lambda task: task["seat_rotation"])
        if [(t["variant_id"], t["seed"], t["seat_rotation"]) for t in tasks] != [
            (plan.variant_id, seed, index) for index, seed in enumerate(plan.training_seeds)
        ]:
            raise PreflightError("training tasks differ from the ordered training plan")
        checkpoint = self.repo.get_memory_training_checkpoint(run_id)
        completed = [task for task in tasks if task["status"] == "finished"]
        if (completed != tasks[:len(completed)]
                or bool(checkpoint) != bool(completed)
                or (completed and checkpoint["completed_task_id"] != completed[-1]["id"])):
            raise PreflightError("training checkpoint and completed task prefix disagree")
        memories = dict(checkpoint["memories"]) if checkpoint else {}
        if checkpoint and (set(memories) != {f"player-{i}" for i in range(8)}
                           or not all(isinstance(value, str) for value in memories.values())):
            raise PreflightError("training checkpoint requires all eight stable player slots")
        self.scope = ExecutionScope()
        self.budget = None
        self._runtime_model_config_snapshot = None
        agents = self._build_agents(run_id, spec, manifest, plan, memories)
        self.repo.update_evaluation_run_status(run_id, "running")

        for task in tasks:
            if task["status"] == "finished":
                continue
            self.repo.update_evaluation_task(task["id"], "running")
            try:
                match_id = await self._run_with_deadline(task, spec, plan, agents)
                if self.budget is not None and self.budget.stopped:
                    raise BudgetExceeded("training budget exhausted during learning")
                memories = self._collect_memories(agents)
                with self.repo.atomic():
                    self.repo.update_evaluation_task(
                        task["id"], "finished", match_id=match_id or None,
                    )
                    self.repo.save_memory_training_checkpoint(
                        run_id, memories, completed_task_id=task["id"],
                    )
            except BaseException as exc:
                if not isinstance(exc, (Exception, asyncio.CancelledError)):
                    raise
                status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                self.repo.update_evaluation_task(
                    task["id"], status, failure_reason=str(exc),
                )
                self.repo.update_evaluation_run_status(run_id, status, str(exc))
                raise

        artifact = self._build_artifact(run_id, spec, manifest, plan, memories)
        try:
            self._write_artifact(output_path, artifact)
        except Exception as exc:
            self.repo.update_evaluation_run_status(run_id, "failed", str(exc))
            raise
        self.repo.update_evaluation_run_status(run_id, "finished")
        return artifact

    async def _run_with_deadline(self, task, spec, plan, agents):
        worker = asyncio.create_task(self._run_training_task(task, spec, plan, agents))

        async def stop():
            self.scope.cancel_requested = True
            worker.cancel()
            done, _ = await asyncio.wait({worker}, timeout=spec.cancellation_deadline_seconds)
            self.scope.revoke()
            if done:
                await asyncio.gather(worker, return_exceptions=True)
            else:
                self.detached.add(worker)
                def consume(finished):
                    self.detached.discard(finished)
                    if not finished.cancelled():
                        finished.exception()
                worker.add_done_callback(consume)

        try:
            done, _ = await asyncio.wait({worker}, timeout=spec.task_timeout_seconds)
            if not done:
                raise TimeoutError("memory training task deadline exceeded")
            return await worker
        except asyncio.CancelledError:
            await stop()
            raise
        except Exception:
            await stop()
            raise

    def _build_agents(
        self, run_id: str, spec: ExperimentSpec, manifest: RunManifest,
        plan: MemoryTrainingPlan, memories: dict[str, str],
    ) -> dict[str, LLMAgent]:
        model_spec = manifest.models[0]
        config = self.repo.get_player_config_by_name(model_spec.config_name)
        if not config:
            raise PreflightError(f"model config not found: {model_spec.config_name}")
        if model_spec.provider not in {"openai", "claude"}:
            raise PreflightError(f"unsupported real-model provider: {model_spec.provider}")
        if not has_usable_credential(config.get("api_key")):
            raise PreflightError(f"model config has no API key: {model_spec.config_name}")
        if model_spec.provider == "claude" and model_spec.parameters.get("seed") is not None:
            raise PreflightError("Claude does not accept a sampling seed")
        pricing = model_spec.pricing
        if (not pricing or pricing.get("currency") != "USD"
                or not pricing.get("source", "").strip() or not pricing.get("effective_date", "").strip()):
            raise PreflightError("memory training requires explicit USD pricing, source and effective date")
        if self.budget is None:
            self.budget = TrainingBudget(self.repo, run_id, spec.budget_limit_usd, spec.max_calls,
                                         spec.max_input_tokens, model_spec.parameters["max_tokens"], pricing)
        system_prompt = model_spec.system_prompt or ""
        self._runtime_model_config_snapshot = {
            "config_name": model_spec.config_name,
            "provider": model_spec.provider,
            "model": model_spec.model,
            "base_url": model_spec.base_url,
            "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        }
        variant = next(v for v in spec.variants if v.variant_id == plan.variant_id)
        agents: dict[str, LLMAgent] = {}
        for index in range(8):
            agent_id = f"{run_id}-memory-player-{index}"
            if self.repo.get_player(agent_id) is None:
                self.repo.create_player(config["id"], f"Memory training P{index + 1}", player_id=agent_id)
            kwargs: dict[str, Any] = {
                "model": model_spec.model,
                "api_key": config.get("api_key", ""),
                "base_url": model_spec.base_url,
                "max_tokens": model_spec.parameters["max_tokens"],
            }
            if model_spec.parameters["temperature"] is not None:
                kwargs["temperature"] = model_spec.parameters["temperature"]
            if model_spec.parameters["top_p"] is not None:
                kwargs["top_p"] = model_spec.parameters["top_p"]
            if model_spec.provider == "claude":
                inner = ClaudeProvider(**kwargs)
            else:
                kwargs["seed"] = model_spec.parameters["seed"]
                inner = OpenAIProvider(**kwargs)
            provider = LoggingLLMProvider(inner, repo=self.scope.guard(self.repo), agent_id=agent_id,
                                          budget=self.scope.guard(self.budget), execution_scope=self.scope)
            agent = LLMAgent(
                agent_id, provider,
                retry_limit=variant.retry_limit,
                rule_feedback=variant.rule_feedback,
                long_term_memory=memories.get(f"player-{index}", ""),
                system_prompt_override=system_prompt,
            )
            agent.set_observability_context(
                self.scope.guard(self.repo), run_id=run_id, variant_id=f"{plan.variant_id}:train",
            )
            agents[agent_id] = agent
        return agents

    async def _run_training_task(
        self, task: dict[str, Any], spec: ExperimentSpec,
        plan: MemoryTrainingPlan, agents: dict[str, LLMAgent],
    ) -> str:
        agent_ids = list(agents)
        seating = assign_seating(
            agent_ids[:4], agent_ids[4:], seed=f"{task['seed']}/memory-training",
        )
        runner = MatchRunner(
            MatchConfig(
                total_hands=plan.total_hands,
                ko_enabled=False,
                seed=task["seed"],
                timeout_config=TimeoutConfig(**spec.timeout_config),
                enable_reflection=True,
                enable_summary=True,
                persist_long_term_memory=False,
            ),
            seating, agents, db_repo=self.scope.guard(self.repo),
            match_name=f"memory-train:{task['id']}",
        )
        self.scope.match_runner = runner
        await runner.run()
        return runner.match_id

    @staticmethod
    def _collect_memories(agents: dict[str, LLMAgent]) -> dict[str, str]:
        return {
            f"player-{index}": agent.memory.get_long_term()
            for index, agent in enumerate(agents.values())
        }

    def _build_artifact(
        self, run_id: str, spec: ExperimentSpec, manifest: RunManifest,
        plan: MemoryTrainingPlan, memories: dict[str, str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "provenance": "real" if self._runtime_model_config_snapshot else "mock",
            "memory_artifact_id": f"{spec.experiment_id}-{plan.plan_sha256[:12]}",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "training_run_id": run_id,
            "training_plan_sha256": plan.plan_sha256,
            "training_seed_set_id": plan.training_seed_set_id,
            "training_seed_set_sha256": plan.training_seed_set_sha256,
            "training_seeds": list(plan.training_seeds),
            "training_total_hands": plan.total_hands,
            "variant_id": plan.variant_id,
            "variant_snapshot": plan.variant_snapshot,
            "model_snapshot": asdict(manifest.models[0]),
            "runtime_model_config": self._runtime_model_config_snapshot or {"status": "test-double"},
            "source_revision": manifest.source_revision,
            "source_dirty": manifest.source_dirty,
            "source_sha256": manifest.source_sha256,
            "dependency_sha256": manifest.dependency_sha256,
            "memories": memories,
        }
        payload["artifact_sha256"] = canonical_hash(payload)
        return payload

    @staticmethod
    def _write_artifact(path: str | Path, artifact: dict[str, Any]) -> None:
        output = Path(path)
        if output.exists():
            raise FileExistsError(f"memory artifact already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects an existing result even if another writer
        # creates it after the preflight check. A partial write remains evidence.
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
