"""Batch evaluation planner and resumable executor."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..agent.llm_agent import LLMAgent
from ..db.repository import DatabaseRepository
from ..llm.logging import LoggingLLMProvider
from ..llm.openai import OpenAIProvider
from ..llm.claude import ClaudeProvider
from ..tournament.match import MatchConfig, MatchRunner
from ..tournament.seating import assign_seating
from .spec import ExperimentSpec, RunManifest, PreflightError, load_memory_artifact


class EvaluationRunner:
    """Creates immutable task plans and executes them one at a time.

    Execution is deliberately sequential by default.  This keeps provider rate
    limits, team time pools and cost accounting deterministic; concurrency can be
    added later behind the same task state machine.
    """

    def __init__(self, repo: DatabaseRepository, root: str) -> None:
        self.repo = repo
        self.root = root
        self._cancel_requested = False

    def create_run(self, spec: ExperimentSpec, manifest: RunManifest) -> str:
        experiment_db_id = self.repo.create_experiment(
            spec.experiment_id, spec.model_dump(mode="json"), manifest.spec_sha256,
        )
        run_id = self.repo.create_evaluation_run(
            experiment_db_id, _manifest_json(manifest), manifest.manifest_sha256,
        )
        for variant in manifest.variants:
            for seed in manifest.seeds:
                for rotation in range(spec.seat_rotations):
                    self.repo.create_evaluation_task(run_id, variant["variant_id"], seed, rotation)
        for snapshot in manifest.models:
            self.repo.add_model_snapshot(run_id, asdict(snapshot))
        return run_id

    async def run(self, run_id: str, spec: ExperimentSpec, manifest: RunManifest, *, real_models: bool = False) -> None:
        if not real_models:
            raise ValueError("real model execution requires explicit real_models=true")
        if any(v.memory_mode == "read_only" for v in spec.variants) and not spec.memory_artifact_path:
            raise PreflightError("read_only memory variant requires memory_artifact_path")
        self.repo.update_evaluation_run_status(run_id, "running")
        self._cancel_requested = False
        for task in self.repo.get_evaluation_tasks(run_id):
            if task["status"] == "finished":
                continue
            if self._cancel_requested:
                self.repo.update_evaluation_task(task["id"], "cancelled")
                continue
            self.repo.update_evaluation_task(task["id"], "running")
            try:
                match_id = await self._run_task(task, spec, manifest)
                self.repo.update_evaluation_task(task["id"], "finished", match_id=match_id or None)
            except Exception as exc:
                self.repo.update_evaluation_task(task["id"], "failed", failure_reason=str(exc))
        tasks = self.repo.get_evaluation_tasks(run_id)
        if self._cancel_requested:
            self.repo.update_evaluation_run_status(run_id, "cancelled")
        elif any(task["status"] == "failed" for task in tasks):
            self.repo.update_evaluation_run_status(run_id, "failed", "one or more tasks failed")
        else:
            self.repo.update_evaluation_run_status(run_id, "finished")

    def cancel(self) -> None:
        self._cancel_requested = True

    async def _run_task(self, task: dict, spec: ExperimentSpec, manifest: RunManifest) -> str:
        model_spec = next(model for model in spec.models if model.config_name == manifest.models[0].config_name)
        variant = next(item for item in spec.variants if item.variant_id == task["variant_id"])
        frozen_memories: dict[str, str] = {}
        if variant.memory_mode == "read_only":
            if not spec.memory_artifact_path:
                raise PreflightError("read_only memory variant requires memory_artifact_path")
            artifact_path = Path(spec.memory_artifact_path)
            if not artifact_path.is_absolute():
                artifact_path = Path(self.root) / artifact_path
            frozen_memories, artifact_hash = load_memory_artifact(artifact_path)
            if artifact_hash != manifest.memory_artifact_sha256:
                raise PreflightError("memory artifact hash no longer matches run manifest")
        config = self.repo.get_player_config_by_name(model_spec.config_name)
        if not config:
            raise ValueError(f"model config not found: {model_spec.config_name}")
        provider_cls = ClaudeProvider if model_spec.provider == "claude" else OpenAIProvider
        agent_ids = [f"{task['id']}-player-{index}" for index in range(8)]
        agents = {}
        for index, agent_id in enumerate(agent_ids):
            kwargs = {
                "model": model_spec.model,
                "api_key": config.get("api_key", ""),
                "max_tokens": model_spec.parameters.max_tokens,
            }
            if model_spec.parameters.temperature is not None:
                kwargs["temperature"] = model_spec.parameters.temperature
            if model_spec.parameters.top_p is not None:
                kwargs["top_p"] = model_spec.parameters.top_p
            if provider_cls is OpenAIProvider:
                kwargs["base_url"] = model_spec.base_url or config.get("base_url")
                kwargs["seed"] = model_spec.parameters.seed
            provider = LoggingLLMProvider(provider_cls(**kwargs), repo=self.repo, agent_id=agent_id)
            agents[agent_id] = LLMAgent(
                agent_id, provider, retry_limit=variant.retry_limit,
                rule_feedback=variant.rule_feedback,
                long_term_memory=frozen_memories.get(f"player-{index}", ""),
                system_prompt_override=config.get("system_prompt"),
            )
            agents[agent_id].set_observability_context(
                self.repo, run_id=task["run_id"], variant_id=task["variant_id"]
            )
        red, blue = agent_ids[:4], agent_ids[4:]
        seating = assign_seating(red, blue, seed=f"{task['seed']}/rotation-{task['seat_rotation']}")
        from ..engine.timeout import TimeoutConfig
        runner = MatchRunner(
            MatchConfig(
                total_hands=spec.total_hands, ko_enabled=spec.ko_enabled, seed=task["seed"],
                timeout_config=TimeoutConfig(**spec.timeout_config),
                enable_reflection=variant.enable_reflection,
                enable_summary=variant.memory_mode == "train",
                persist_long_term_memory=variant.memory_mode == "train",
            ),
            seating, agents, db_repo=self.repo, match_name=f"eval:{task['id']}",
        )
        result = await runner.run()
        return runner.match_id


def _manifest_json(manifest: RunManifest) -> dict[str, Any]:
    raw = asdict(manifest)
    raw["models"] = [asdict(model) for model in manifest.models]
    raw["seeds"] = list(manifest.seeds)
    raw["variants"] = list(manifest.variants)
    return raw
