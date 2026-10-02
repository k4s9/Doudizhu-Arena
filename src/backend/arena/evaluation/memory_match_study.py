"""Frozen-memory teams against a seeded rule baseline in complete matches.

This is separate from both the reliability protocol and the fixed-observation
study. Each seed/arm is played once on each color, with no learning at test time.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import time
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ..agent.base import AgentContext
from ..agent.llm_agent import LLMAgent
from ..agent.memory import MAX_LONG_TERM_CHARS, MemoryManager, validate_memory_text
from ..agent.prompts.bidding import build_bidding_prompt
from ..agent.prompts.playing import build_playing_prompt
from ..agent.random_agent import RandomAgent
from ..db import reliability
from ..engine.card import Card, Rank, Suit
from ..engine.timeout import TimeoutConfig
from ..engine.trick import PatternType, Trick
from ..llm.logging import LoggingLLMProvider
from ..security.credentials import has_usable_credential
from ..tournament.match import MatchConfig, MatchRunner
from ..tournament.seating import assign_seating
from .budget import BudgetExceeded
from .execution import ExecutionScope
from .memory_study import ARMS, RETRY, implementation_snapshot, validate_artifact
from .memory_training import MemoryTrainingRunner, TrainingBudget
from .mock import ReliabilityMockProvider
from .spec import ModelSpec, canonical_hash

DDL = """CREATE TABLE IF NOT EXISTS memory_match_evidence (
 task_id TEXT PRIMARY KEY REFERENCES evaluation_tasks(id), payload_json TEXT NOT NULL);"""


class MemoryMatchProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal["memory-full-matches-v1"] = "memory-full-matches-v1"
    total_hands: int = Field(default=1, ge=1, le=200)
    memory_slot: str = "player-0"
    retry_limit: int = Field(default=1, ge=0, le=2)
    max_calls: int = Field(default=2000, ge=1, le=1_000_000)
    max_input_tokens: int = Field(default=32768, gt=1024)
    budget_limit_usd: float = Field(default=0, ge=0)
    task_timeout_seconds: float = Field(default=600, gt=0, le=86400)
    cancellation_deadline_seconds: float = Field(default=1, gt=0, le=30)
    call_timeout_seconds: float = Field(default=60, gt=0, le=600)
    schedule_seed: str = "memory-match-order-v1"


def arm_memory(plan, arm):
    return {"no_memory": "", "fixed_initial": plan["initial_strategy"],
            "frozen_memory": plan["memory_artifact"]["memories"][plan["protocol"]["memory_slot"]]}[arm].strip()


def schedule(plan):
    tasks = []
    for seed in plan["test_seeds"]:
        paired = [{"seed": seed, "arm": arm, "side": side} for arm in ARMS for side in ("red", "blue")]
        random.Random(f"{plan['protocol']['schedule_seed']}/{seed}").shuffle(paired)
        tasks.extend(paired)
    return tasks


def baseline_seed(seed, slot):
    return f"memory-random-baseline-v1/{seed}/{slot}"


def seating_for(target_ids, baseline_ids, side, seed):
    red, blue = (target_ids, baseline_ids) if side == "red" else (baseline_ids, target_ids)
    return assign_seating(red, blue, seed=f"memory-match-seating-v1/{seed}")


def context_from_observation(observation):
    """Restore the production context without adding private information."""
    raw = deepcopy(observation)
    def card(value):
        return Card.from_string(value) if isinstance(value, str) else Card(
            Rank(value["rank"]), Suit(value["suit"]) if value["suit"] is not None else None)
    raw["hand_cards"] = [card(c) for c in raw["hand_cards"]]
    for field in ("dizhu_cards", "initial_hand"):
        if raw.get(field) is not None:
            raw[field] = tuple(card(c) for c in raw[field])
    for play in raw.get("play_history", []):
        if play.get("cards"):
            play["cards"] = [card(c) for c in play["cards"]]
    target = raw.get("current_trick")
    if target:
        raw["current_trick"] = Trick(pattern=PatternType(target["pattern"]),
            main_cards=tuple(card(c) for c in target["main_cards"]),
            attached=tuple(card(c) for c in target["attached"]),
            main_rank=Rank(target["main_rank"]) if target["main_rank"] is not None else None,
            length=target["length"])
    return AgentContext(**raw)


def expected_prompt(plan, arm, slot, phase, observation, table, attempt):
    memory = MemoryManager.with_long_term(slot, arm_memory(plan, arm))
    ctx = context_from_observation(observation)
    if phase == "bidding":
        system, user = build_bidding_prompt(ctx, memory, slot)
    else:
        teams = {"S": "red", "N": "red", "E": "blue", "W": "blue"}
        if table == "B":
            teams = {seat: "blue" if team == "red" else "red" for seat, team in teams.items()}
        system, user = build_playing_prompt(ctx, memory, slot, seat_teams=teams)
    return system, user + RETRY * (attempt - 1)


def make_match_plan(root, artifact, initial_strategy, model, test_seeds, protocol, *, mock):
    model = ModelSpec.model_validate(model).model_dump(mode="json")
    protocol = MemoryMatchProtocol.model_validate(protocol).model_dump(mode="json")
    if (not isinstance(test_seeds, list) or not test_seeds
            or not all(isinstance(seed, str) and seed.strip() for seed in test_seeds)
            or len(test_seeds) != len(set(test_seeds))):
        raise ValueError("test seeds must be nonempty unique strings")
    if model["provider"] not in (("mock",) if mock else ("openai", "claude")):
        raise ValueError("explicit model provider matching real/mock mode required")
    if not model["model"].strip() or model["system_prompt"] not in (None, ""):
        raise ValueError("explicit model name and empty shared system override required")
    endpoint = urlsplit(model["base_url"] or "")
    if (not model["base_url"] or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or (not mock and (endpoint.scheme not in ("http", "https") or not endpoint.hostname))):
        raise ValueError("explicit base_url without credentials/query/fragment required")
    if model["provider"] == "claude" and model["parameters"]["seed"] is not None:
        raise ValueError("Claude does not accept a sampling seed")
    price = model["pricing"]
    if not price or price["currency"] != "USD" or not price["source"].strip() or not price["effective_date"].strip():
        raise ValueError("explicit USD pricing, source and effective date required")
    memory = validate_artifact(artifact, {"test_seeds": test_seeds}, model, protocol["memory_slot"], mock=mock)
    for value in (initial_strategy, memory):
        validate_memory_text(value, max_chars=MAX_LONG_TERM_CHARS)
        if len(value.encode()) + 1024 > protocol["max_input_tokens"]:
            raise ValueError("memory exceeds declared input allowance")
    reservation = (Decimal(protocol["max_input_tokens"]) * Decimal(str(price["input_per_million"]))
                   + Decimal(model["parameters"]["max_tokens"]) * Decimal(str(price["output_per_million"]))) / 1_000_000
    if reservation * protocol["max_calls"] > Decimal(str(protocol["budget_limit_usd"])):
        raise ValueError("budget must cover the predeclared call cap reservations")
    snapshot = implementation_snapshot(root)
    entrypoint = Path(root) / "scripts/memory_match_study.py"
    snapshot["match_entrypoint_sha256"] = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
    plan = {"kind": "memory-full-matches-v1", "mock": mock, "model": model, "protocol": protocol,
            "test_seeds": list(test_seeds), "memory_artifact": deepcopy(artifact),
            "initial_strategy": initial_strategy, "initial_strategy_sha256": canonical_hash(initial_strategy),
            "opponent": "RandomAgent; fresh seeded instances per match; no LLM calls",
            "maximum_reserved_usd": str(reservation * protocol["max_calls"]), "implementation": snapshot}
    plan["sha256"] = canonical_hash(plan)
    return plan


def verify_match_plan(plan, root=None):
    if plan.get("kind") != "memory-full-matches-v1" or plan.get("sha256") != canonical_hash(
            {key: value for key, value in plan.items() if key != "sha256"}):
        raise ValueError("memory match plan hash/kind mismatch")
    MemoryMatchProtocol.model_validate(plan["protocol"])
    ModelSpec.model_validate(plan["model"])
    validate_artifact(plan["memory_artifact"], {"test_seeds": plan["test_seeds"]}, plan["model"],
                      plan["protocol"]["memory_slot"], mock=plan["mock"])
    if root is not None and make_match_plan(root, plan["memory_artifact"], plan["initial_strategy"],
            plan["model"], plan["test_seeds"], plan["protocol"], mock=plan["mock"]) != plan:
        raise ValueError("source/dependencies or frozen match controls changed; prepare again")


class MemoryMatchMockProvider(ReliabilityMockProvider):
    """Memory-blind deterministic single/pass fixture, not learned behavior."""
    model = "memory-match-mock-v1"
    last_response_model = "memory-match-mock-v1"

    def __init__(self):
        super().__init__(scenario="valid_first")


class MemoryMatchRunner(MemoryTrainingRunner):
    """Reuse the training runner's tested deadline/revocation boundary only."""

    def __init__(self, repo, root, plan, *, provider_factory=None):
        super().__init__(repo, root)
        self.plan = plan
        self.provider_factory = provider_factory
        self.run_id = None

    def _provider(self, credential):
        if self.plan["mock"]:
            return (self.provider_factory or MemoryMatchMockProvider)()
        from ..llm.claude import ClaudeProvider
        from ..llm.openai import OpenAIProvider
        model = self.plan["model"]
        kwargs = {k: v for k, v in model["parameters"].items() if v is not None}
        if model["provider"] == "claude":
            kwargs.pop("seed", None)
        return (ClaudeProvider if model["provider"] == "claude" else OpenAIProvider)(
            model=model["model"], base_url=model["base_url"], api_key=credential, **kwargs)

    async def _run_training_task(self, task, spec, plan, agents):
        return await self.scope.match_runner.run()

    async def run(self, *, mock=False, real_models=False, credential=""):
        if mock == real_models or mock != self.plan["mock"]:
            raise ValueError("explicit matching --mock or --real-models mode required")
        verify_match_plan(self.plan, self.root)
        if real_models and (self.provider_factory or not has_usable_credential(credential)):
            raise ValueError("real matches require a usable credential and production provider")
        repo, plan = self.repo, self.plan
        artifact_hash_before = canonical_hash(plan["memory_artifact"])
        protocol, model = plan["protocol"], plan["model"]
        repo.conn.executescript(DDL)
        experiment = repo.create_experiment("memory-full-matches-v1", plan, plan["sha256"])
        self.run_id = repo.create_evaluation_run(experiment, plan, plan["sha256"])
        tasks = []
        for item in schedule(plan):
            task_id = repo.create_evaluation_task(self.run_id, item["arm"], item["seed"], 0 if item["side"] == "red" else 1)
            tasks.append({**item, "id": task_id})
        config_id = repo.create_player_config(f"memory-match-{self.run_id}", model["provider"], model["model"], "")
        baseline_config = repo.create_player_config(f"memory-baseline-{self.run_id}", "random", "random", "")
        self.budget = TrainingBudget(repo, self.run_id, protocol["budget_limit_usd"], protocol["max_calls"],
                                     protocol["max_input_tokens"], model["parameters"]["max_tokens"], model["pricing"])
        repo.update_evaluation_run_status(self.run_id, "running")
        for task in tasks:
            attempt_id = reliability.start_attempt(repo, task["id"])
            self.scope = ExecutionScope()
            scoped_repo = self.scope.guard(repo)
            agents, roster, targets, baselines = {}, [], [], []
            started = time.monotonic()
            match_id = None
            providers = []
            try:
                for kind in ("evaluated", "baseline"):
                    for index in range(4):
                        slot = f"target-{index}" if kind == "evaluated" else f"baseline-{index}"
                        aid = repo.create_player(config_id if kind == "evaluated" else baseline_config, slot)
                        if kind == "evaluated":
                            inner = self._provider(credential)
                            providers.append(inner)
                            provider = LoggingLLMProvider(inner, repo=scoped_repo, agent_id=aid,
                                budget=self.scope.guard(self.budget), execution_scope=self.scope)
                            agent = LLMAgent(aid, provider, long_term_memory=arm_memory(plan, task["arm"]),
                                retry_limit=protocol["retry_limit"], rule_feedback=False, prompt_name=slot)
                            agent.set_observability_context(scoped_repo, run_id=self.run_id, variant_id=task["arm"], task_attempt_id=attempt_id)
                            targets.append(aid)
                        else:
                            agent = RandomAgent(aid, seed=baseline_seed(task["seed"], slot))
                            baselines.append(aid)
                        agents[aid] = agent
                        roster.append({"slot": slot, "kind": kind, "player_id": aid,
                                       "baseline_seed": baseline_seed(task["seed"], slot) if kind == "baseline" else None})
                seating = seating_for(targets, baselines, task["side"], task["seed"])
                config = MatchConfig(total_hands=protocol["total_hands"], max_tiebreaker_hands=0, ko_enabled=False,
                    seed=task["seed"], enable_reflection=False, enable_summary=False, persist_long_term_memory=False,
                    timeout_config=TimeoutConfig(bidding_seconds=protocol["call_timeout_seconds"],
                        individual_play_seconds=protocol["call_timeout_seconds"],
                        team_pool_seconds=protocol["task_timeout_seconds"],
                        exhausted_individual_seconds=protocol["call_timeout_seconds"]))
                runner = MatchRunner(config, seating, agents, db_repo=scoped_repo,
                                     match_name=f"memory:{task['arm']}:{task['seed']}:{task['side']}")
                self.scope.match_runner = runner
                match_id = repo.create_match(runner.match_name, asdict(config), task["seed"])
                runner.match_id = runner.table_a.match_id = runner.table_b.match_id = match_id
                repo.conn.execute("UPDATE task_attempts SET match_id=? WHERE id=?", (match_id, attempt_id))
                repo.conn.commit()
                for entry in roster:
                    aid = entry["player_id"]
                    table, seat = seating.agent_seats[aid]
                    entry.update(table=table, seat=seat, team=seating.agent_teams[aid])
                    repo.add_participant(match_id, aid, entry["team"],
                                         seat_table_a=seat if table == "A" else None, seat_table_b=seat if table == "B" else None)
                def memories():
                    return {entry["slot"]: {"long_term": agents[entry["player_id"]].memory.get_long_term(),
                                            "short_term": agents[entry["player_id"]].memory.get_short_term(match_id)} for entry in roster}
                before = memories()
                await self._run_with_deadline(task, SimpleNamespace(**protocol), plan, agents)
                after = memories()
                if before != after or canonical_hash(plan["memory_artifact"]) != artifact_hash_before:
                    raise RuntimeError("frozen test memory changed")
                if self.budget.stopped:
                    raise BudgetExceeded("memory match budget exhausted")
                payload = {"roster": roster, "before": before, "after": after,
                           "elapsed_ms": (time.monotonic() - started) * 1000}
                payload["sha256"] = canonical_hash(payload)
                with repo.atomic():
                    repo.conn.execute("INSERT INTO memory_match_evidence VALUES(?,?)", (task["id"], json.dumps(payload, ensure_ascii=False)))
                    reliability.finish_attempt(repo, attempt_id, "finished", match_id)
                    repo.update_evaluation_task(task["id"], "finished", match_id=match_id)
            except (Exception, asyncio.CancelledError) as exc:
                self.scope.revoke()
                status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                reliability.finish_attempt(repo, attempt_id, status, match_id, str(exc))
                repo.update_evaluation_task(task["id"], status, match_id=match_id, failure_reason=str(exc))
                if match_id:
                    repo.update_match_status(match_id, status)
                repo.update_evaluation_run_status(self.run_id, status, str(exc))
                return self.run_id
            finally:
                if self.scope.match_runner and self.scope.match_runner.event_bus:
                    self.scope.match_runner.event_bus.close()
                for inner in providers:
                    client = getattr(inner, "_client", None)
                    if client is not None and not self.detached:
                        try:
                            await asyncio.wait_for(client.close(), timeout=5)
                        except (Exception, asyncio.CancelledError):
                            pass
        repo.update_evaluation_run_status(self.run_id, "finished")
        return self.run_id
