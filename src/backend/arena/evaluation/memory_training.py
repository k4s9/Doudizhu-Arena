"""Sequential memory training and immutable artifact export."""

from __future__ import annotations

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
from ..tournament.match import MatchConfig, MatchRunner
from ..tournament.seating import assign_seating
from .spec import (
    ExperimentSpec,
    PreflightError,
    RunManifest,
    canonical_hash,
    load_seed_set,
)


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


class MemoryTrainingRunner:
    """Run training seeds sequentially so long-term memory carries forward."""

    def __init__(self, repo: DatabaseRepository, root: str | Path) -> None:
        self.repo = repo
        self.root = Path(root)
        self._runtime_model_config_snapshot: dict[str, Any] | None = None

    def create_run(
        self, spec: ExperimentSpec, manifest: RunManifest, plan: MemoryTrainingPlan,
    ) -> str:
        experiment_db_id = self.repo.create_experiment(
            spec.experiment_id, spec.model_dump(mode="json"), manifest.spec_sha256,
        )
        training_manifest = {
            "run_kind": "memory_training",
            "base_manifest_sha256": manifest.manifest_sha256,
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
        checkpoint = self.repo.get_memory_training_checkpoint(run_id)
        memories = dict(checkpoint["memories"]) if checkpoint else {}
        agents = self._build_agents(run_id, spec, manifest, plan, memories)
        self.repo.update_evaluation_run_status(run_id, "running")

        for task in self.repo.get_evaluation_tasks(run_id):
            if task["status"] == "finished":
                continue
            self.repo.update_evaluation_task(task["id"], "running")
            try:
                match_id = await self._run_training_task(task, spec, plan, agents)
                self.repo.update_evaluation_task(
                    task["id"], "finished", match_id=match_id or None,
                )
                memories = self._collect_memories(agents)
                self.repo.save_memory_training_checkpoint(
                    run_id, memories, completed_task_id=task["id"],
                )
            except Exception as exc:
                self.repo.update_evaluation_task(
                    task["id"], "failed", failure_reason=str(exc),
                )
                self.repo.update_evaluation_run_status(run_id, "failed", str(exc))
                raise

        self.repo.update_evaluation_run_status(run_id, "finished")
        artifact = self._build_artifact(run_id, spec, manifest, plan, memories)
        self._write_artifact(output_path, artifact)
        return artifact

    def _build_agents(
        self, run_id: str, spec: ExperimentSpec, manifest: RunManifest,
        plan: MemoryTrainingPlan, memories: dict[str, str],
    ) -> dict[str, LLMAgent]:
        model_spec = spec.models[0]
        config = self.repo.get_player_config_by_name(model_spec.config_name)
        if not config:
            raise PreflightError(f"model config not found: {model_spec.config_name}")
        if model_spec.provider not in {"openai", "claude"}:
            raise PreflightError(f"unsupported real-model provider: {model_spec.provider}")
        if not config.get("api_key"):
            raise PreflightError(f"model config has no API key: {model_spec.config_name}")
        system_prompt = config.get("system_prompt") or ""
        self._runtime_model_config_snapshot = {
            "config_name": model_spec.config_name,
            "provider": model_spec.provider,
            "model": model_spec.model,
            "base_url": model_spec.base_url or config.get("base_url"),
            "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        }
        variant = next(v for v in spec.variants if v.variant_id == plan.variant_id)
        agents: dict[str, LLMAgent] = {}
        for index in range(8):
            agent_id = f"{run_id}-memory-player-{index}"
            kwargs: dict[str, Any] = {
                "model": model_spec.model,
                "api_key": config.get("api_key", ""),
                "max_tokens": model_spec.parameters.max_tokens,
            }
            if model_spec.parameters.temperature is not None:
                kwargs["temperature"] = model_spec.parameters.temperature
            if model_spec.parameters.top_p is not None:
                kwargs["top_p"] = model_spec.parameters.top_p
            if model_spec.provider == "claude":
                inner = ClaudeProvider(**kwargs)
            else:
                kwargs["base_url"] = model_spec.base_url or config.get("base_url")
                kwargs["seed"] = model_spec.parameters.seed
                inner = OpenAIProvider(**kwargs)
            provider = LoggingLLMProvider(inner, repo=self.repo, agent_id=agent_id)
            agent = LLMAgent(
                agent_id, provider,
                retry_limit=variant.retry_limit,
                rule_feedback=variant.rule_feedback,
                long_term_memory=memories.get(f"player-{index}", ""),
                system_prompt_override=config.get("system_prompt"),
            )
            agent.set_observability_context(
                self.repo, run_id=run_id, variant_id=f"{plan.variant_id}:train",
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
            seating, agents, db_repo=self.repo,
            match_name=f"memory-train:{task['id']}",
        )
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
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
