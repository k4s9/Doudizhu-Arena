"""Independent, read-only memory ablation on frozen visible observations.

This protocol measures output legality and cost, never playing strength. It does
not change the first-round reliability protocol or the historical fixed study.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ..agent.parser import ParseError
from ..llm.base import AbstractLLMProvider, LLMError, LLMUsage
from ..llm.logging import LoggingLLMProvider
from ..security.credentials import has_usable_credential
from .budget import BudgetExceeded, BudgetLedger
from .execution import ExecutionScope
from .fixed_corpus import verify_corpus
from .observations import validate_output
from .spec import ModelSpec, canonical_hash

ARMS = ("no_memory", "fixed_initial", "frozen_memory")
RETRY = "\n\n请重新独立生成一个完整 JSON 回复。"
DDL = """CREATE TABLE IF NOT EXISTS memory_study_decisions (
 run_id TEXT NOT NULL REFERENCES evaluation_runs(id), observation_id TEXT NOT NULL,
 arm TEXT NOT NULL, status TEXT NOT NULL, outcome_json TEXT,
 PRIMARY KEY(run_id, observation_id, arm));"""


class MemoryProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal["memory-fixed-observations-v1"] = "memory-fixed-observations-v1"
    memory_slot: str = "player-0"
    retry_limit: int = Field(default=1, ge=0, le=2)
    max_input_tokens: int = Field(default=32768, gt=1024)
    budget_limit_usd: float = Field(default=1, ge=0)
    call_timeout_seconds: float = Field(default=60, gt=0, le=600)
    cancellation_deadline_seconds: float = Field(default=1, gt=0, le=30)
    schedule_seed: str = "memory-study-order-v1"


def implementation_snapshot(root):
    root = Path(root)
    paths = sorted((root / "src/backend/arena").rglob("*.py"))
    paths += [root / "scripts/memory_effect_study.py"]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    dependencies = {}
    for relative in ("pyproject.toml", "src/backend/pyproject.toml", "src/backend/requirements.txt"):
        path = root / relative
        if path.exists():
            dependencies[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    versions = {}
    for package in ("openai", "anthropic", "pydantic"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    return {"source_files": hashes, "source_sha256": canonical_hash(hashes),
            "dependencies": dependencies, "sdk_versions": versions}


def validate_artifact(artifact, corpus, model, slot, *, mock):
    if not isinstance(artifact, dict) or artifact.get("schema_version") != 1:
        raise ValueError("unsupported frozen memory artifact schema")
    if artifact.get("artifact_sha256") != canonical_hash({
        k: v for k, v in artifact.items() if k != "artifact_sha256"
    }):
        raise ValueError("frozen memory artifact hash mismatch")
    seeds = artifact.get("training_seeds")
    if (not isinstance(seeds, list) or not seeds
            or not all(isinstance(s, str) and s for s in seeds)
            or len(seeds) != len(set(seeds))):
        raise ValueError("artifact requires nonempty unique training seeds")
    if set(seeds) & set(corpus["test_seeds"]):
        raise ValueError("training and test seeds overlap")
    seed_hash = canonical_hash({"seed_set_id": artifact.get("training_seed_set_id"), "seeds": seeds})
    if seed_hash != artifact.get("training_seed_set_sha256"):
        raise ValueError("training seed set hash mismatch")
    memories = artifact.get("memories")
    if not isinstance(memories, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in memories.items()):
        raise ValueError("artifact memories must be a string-to-string object")
    memory = memories.get(slot)
    if not isinstance(memory, str) or not memory.strip():
        raise ValueError("selected memory slot must contain nonempty frozen text")
    training_model = artifact.get("model_snapshot", {})
    if not isinstance(training_model, dict):
        raise ValueError("artifact requires a training model snapshot")
    for key in ("provider", "model", "parameters", "base_url"):
        if training_model.get(key) != model[key]:
            raise ValueError(f"training/evaluation model mismatch: {key}")
    if artifact.get("provenance") != ("mock" if mock else "real"):
        raise ValueError("artifact requires explicit matching real/mock provenance")
    for key in ("memory_artifact_id", "training_run_id", "training_seed_set_id", "source_revision"):
        if not isinstance(artifact.get(key), str) or not artifact[key]:
            raise ValueError(f"artifact requires training provenance: {key}")
    for key in ("training_plan_sha256", "source_sha256", "dependency_sha256"):
        value = artifact.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"artifact requires training fingerprint: {key}")
    if not mock:
        if (type(artifact.get("training_total_hands")) is not int or artifact["training_total_hands"] <= 0
                or set(memories) != {f"player-{i}" for i in range(8)}):
            raise ValueError("real artifact requires training hand count and all eight player slots")
        runtime = artifact.get("runtime_model_config", {})
        if not isinstance(runtime, dict) or any(runtime.get(k) != training_model.get(k)
                                              for k in ("provider", "model", "base_url")):
            raise ValueError("real artifact runtime model provenance mismatch")
        expected_prompt = hashlib.sha256((training_model.get("system_prompt") or "").encode()).hexdigest()
        if runtime.get("system_prompt_sha256") != expected_prompt:
            raise ValueError("real artifact training prompt provenance mismatch")
    return memory


def prompts(plan, item, arm, attempt=1):
    text = {"no_memory": "", "fixed_initial": plan["initial_strategy"],
            "frozen_memory": plan["memory_artifact"]["memories"][plan["protocol"]["memory_slot"]]}[arm]
    system = item["system_prompt"]
    if text:
        system += "\n\n## 固定策略经验（本次评测只读）\n" + text
    return system, item["user_prompt"] + RETRY * (attempt - 1)


def make_plan(root, corpus, artifact, initial_strategy, model, protocol, *, mock):
    verify_corpus(corpus)
    model = ModelSpec.model_validate(model).model_dump(mode="json")
    protocol = MemoryProtocol.model_validate(protocol).model_dump(mode="json")
    if model["provider"] not in (("mock",) if mock else ("openai", "claude")):
        raise ValueError("model provider disagrees with execution mode")
    if not model["model"].strip() or (not mock and model["model"].lower() in {"mock", "random"}):
        raise ValueError("explicit model name required")
    if model["system_prompt"] not in (None, ""):
        raise ValueError("corpus owns the shared system prompt; put the control in initial_strategy")
    if not initial_strategy.strip():
        raise ValueError("fixed initial strategy is required")
    if model["provider"] == "claude" and model["parameters"]["seed"] is not None:
        raise ValueError("Claude does not accept a sampling seed")
    endpoint = model["base_url"]
    if not endpoint:
        raise ValueError("explicit base_url required")
    url = urlsplit(endpoint)
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("base_url must not contain credentials, query or fragment")
    if not mock and (url.scheme not in ("https", "http") or not url.hostname):
        raise ValueError("real base_url must be an HTTP(S) endpoint")
    pricing = model["pricing"]
    if (not pricing or pricing["currency"] != "USD"
            or not pricing["source"].strip() or not pricing["effective_date"].strip()):
        raise ValueError("explicit USD pricing, source and effective date required")
    validate_artifact(artifact, corpus, model, protocol["memory_slot"], mock=mock)
    selected = [i for i in corpus["observations"] if i["split"] == "test"]
    max_calls = len(selected) * len(ARMS) * (protocol["retry_limit"] + 1)
    reserve = (Decimal(protocol["max_input_tokens"]) * Decimal(str(pricing["input_per_million"]))
               + Decimal(model["parameters"]["max_tokens"]) * Decimal(str(pricing["output_per_million"]))) / 1_000_000
    if reserve * max_calls > Decimal(str(protocol["budget_limit_usd"])):
        raise ValueError("budget must cover the predeclared worst-case call reservations")
    plan = {"kind": "memory-fixed-observations", "mock": mock, "model": model,
            "protocol": protocol, "corpus": corpus, "memory_artifact": artifact,
            "initial_strategy": initial_strategy, "initial_strategy_sha256": canonical_hash(initial_strategy),
            "max_calls": max_calls, "maximum_reserved_usd": str(reserve * max_calls),
            "implementation": implementation_snapshot(root)}
    prompt_hashes = {}
    for item in selected:
        for arm in ARMS:
            system, user = prompts(plan, item, arm, protocol["retry_limit"] + 1)
            if len((system + user).encode()) + 1024 > protocol["max_input_tokens"]:
                raise ValueError("frozen prompts exceed input allowance, including retries")
            prompt_hashes[f"{item['observation_id']}/{arm}"] = canonical_hash(prompts(plan, item, arm))
    plan["prompt_sha256"] = canonical_hash(prompt_hashes)
    plan["sha256"] = canonical_hash(plan)
    return plan


def verify_plan(plan, root=None):
    if plan.get("sha256") != canonical_hash({k: v for k, v in plan.items() if k != "sha256"}):
        raise ValueError("plan hash mismatch")
    verify_corpus(plan["corpus"])
    MemoryProtocol.model_validate(plan["protocol"])
    ModelSpec.model_validate(plan["model"])
    validate_artifact(plan["memory_artifact"], plan["corpus"], plan["model"],
                      plan["protocol"]["memory_slot"], mock=plan["mock"])
    if root is not None:
        rebuilt = make_plan(root, plan["corpus"], plan["memory_artifact"], plan["initial_strategy"],
                            plan["model"], plan["protocol"], mock=plan["mock"])
        if rebuilt != plan:
            raise ValueError("source/dependencies or frozen controls changed; prepare a new plan")


def arm_order(plan, observation_id):
    arms = list(ARMS)
    random.Random(f"{plan['protocol']['schedule_seed']}/{observation_id}").shuffle(arms)
    return arms


def assess(item, call):
    if call["provider_status"] != "returned":
        return {"valid": False, "action": None, "error_kind": "provider_error"}
    try:
        return {"valid": True, "action": validate_output(item, call["raw_output"] or ""), "error_kind": None}
    except ParseError:
        return {"valid": False, "action": None, "error_kind": "invalid_output"}


def read_calls(repo, run_id):
    return [dict(row) for row in repo.conn.execute("""SELECT l.*, e.system_prompt,
        e.user_prompt,e.raw_output,e.provider_status FROM llm_call_logs l
        JOIN call_evidence e ON e.call_id=l.id WHERE l.run_id=? ORDER BY l.rowid""", (run_id,))]


class MemoryStudyMockProvider(AbstractLLMProvider):
    """Memory-blind fixture: equal synthetic behavior in all arms, no gain claim."""
    provider_name = "mock"
    model = "memory-study-mock"
    last_response_model = "memory-study-mock"
    last_system_fingerprint = None
    last_usage = LLMUsage(20, 10, 30)

    async def generate(self, user_prompt, system_prompt=""):
        import re
        if "叫分规则" in system_prompt:
            return '{"bid":0}'
        if RETRY not in user_prompt:
            return '{"action":{"type":"play","cards":["不存在的牌"]}}'
        if "新一轮，自由出牌" not in user_prompt:
            return '{"action":{"type":"pass"}}'
        hand = re.search(r"你的手牌（\d+张）：\n([^\n]+)", user_prompt).group(1).split()
        return json.dumps({"action": {"type": "play", "cards": hand[:1]}}, ensure_ascii=False)


class MemoryStudyRunner:
    def __init__(self, repo, root, plan, *, provider_factory=None):
        self.repo, self.root, self.plan = repo, Path(root), plan
        self.provider_factory = provider_factory
        self.run_id = None
        self.detached = set()

    def _provider(self, credential):
        if self.plan["mock"]:
            return (self.provider_factory or MemoryStudyMockProvider)()
        from ..llm.claude import ClaudeProvider
        from ..llm.openai import OpenAIProvider
        model = self.plan["model"]
        kwargs = {k: v for k, v in model["parameters"].items() if v is not None}
        if model["provider"] == "claude":
            kwargs.pop("seed", None)
        return (ClaudeProvider if model["provider"] == "claude" else OpenAIProvider)(
            model=model["model"], base_url=model["base_url"], api_key=credential, **kwargs)

    async def _call(self, item, arm, attempt, credential):
        scope, inner = ExecutionScope(), self._provider(credential)
        provider = LoggingLLMProvider(inner, repo=scope.guard(self.repo), agent_id=self.player_id,
                                      budget=scope.guard(self.budget), execution_scope=scope)
        provider.set_context(run_id=self.run_id, variant_id=arm, decision_id=item["observation_id"],
                             attempt=attempt, phase=item["phase"])
        system, user = prompts(self.plan, item, arm, attempt)
        worker = asyncio.create_task(provider.generate(user, system))
        async def stop():
            scope.cancel_requested = True
            worker.cancel()
            done, _ = await asyncio.wait({worker}, timeout=self.plan["protocol"]["cancellation_deadline_seconds"])
            scope.revoke()
            if done:
                await asyncio.gather(worker, return_exceptions=True)
            else:
                self.detached.add(worker)
                def consume(task):
                    self.detached.discard(task)
                    if not task.cancelled():
                        task.exception()
                worker.add_done_callback(consume)
        try:
            try:
                done, _ = await asyncio.wait({worker}, timeout=self.plan["protocol"]["call_timeout_seconds"])
                if not done:
                    await stop()
                else:
                    try:
                        await worker
                    except LLMError:
                        pass  # Provider failures are measured; budget/evidence failures stop the run.
            except asyncio.CancelledError:
                await stop()
                raise
            calls = [c for c in read_calls(self.repo, self.run_id)
                     if c["decision_id"] == item["observation_id"] and c["variant_id"] == arm and c["attempt"] == attempt]
            if len(calls) != 1:
                raise RuntimeError("physical call evidence missing or duplicated")
            call = calls[0]
            for field, limit in (("prompt_tokens", self.plan["protocol"]["max_input_tokens"]),
                                 ("completion_tokens", self.plan["model"]["parameters"]["max_tokens"])):
                if call[field] is not None and not 0 <= call[field] <= limit:
                    raise BudgetExceeded(f"provider {field} exceeds declared allowance")
            return call
        finally:
            if worker.done():
                client = getattr(inner, "_client", None)
                if client is not None:
                    try:
                        await asyncio.wait_for(client.close(), 5)
                    except (Exception, asyncio.CancelledError):
                        pass

    async def run(self, *, mock=False, real_models=False, credential=""):
        if mock == real_models or mock != self.plan["mock"]:
            raise ValueError("explicit matching --mock or --real-models mode required")
        verify_plan(self.plan, self.root)
        if real_models and (self.provider_factory or not has_usable_credential(credential)):
            raise ValueError("real evaluation requires a usable credential and production provider")
        plan, repo = self.plan, self.repo
        repo.conn.executescript(DDL)
        experiment = repo.create_experiment("memory-effects-v1", plan, plan["sha256"])
        self.run_id = repo.create_evaluation_run(experiment, plan, plan["sha256"])
        model, protocol = plan["model"], plan["protocol"]
        config = repo.create_player_config(f"memory-study-{self.run_id}", model["provider"], model["model"], "")
        self.player_id = repo.create_player(config, "Frozen memory benchmark")
        self.budget = BudgetLedger(repo, self.run_id, protocol["budget_limit_usd"], plan["max_calls"],
                                   protocol["max_input_tokens"], model["parameters"]["max_tokens"], model["pricing"])
        items = [i for i in plan["corpus"]["observations"] if i["split"] == "test"]
        with repo.atomic():
            for item in items:
                for arm in ARMS:
                    repo.conn.execute("INSERT INTO memory_study_decisions VALUES(?,?,?,'planned',NULL)",
                                      (self.run_id, item["observation_id"], arm))
        repo.update_evaluation_run_status(self.run_id, "running")
        try:
            for item in items:
                for arm in arm_order(plan, item["observation_id"]):
                    chain = []
                    for attempt in range(1, protocol["retry_limit"] + 2):
                        call = await self._call(item, arm, attempt, credential)
                        validation = assess(item, call)
                        chain.append({"call_id": call["id"], "raw_output_sha256": canonical_hash(call["raw_output"]), **validation})
                        if validation["valid"]:
                            break
                    outcome = {"chain": chain, "fallback_required": not chain[-1]["valid"], "executed_fallbacks": 0}
                    repo.conn.execute("UPDATE memory_study_decisions SET status='finished',outcome_json=? WHERE run_id=? AND observation_id=? AND arm=?",
                                      (json.dumps(outcome, ensure_ascii=False), self.run_id, item["observation_id"], arm))
                    repo.conn.commit()
            repo.update_evaluation_run_status(self.run_id, "finished")
        except asyncio.CancelledError:
            repo.update_evaluation_run_status(self.run_id, "cancelled")
        except Exception as exc:
            repo.update_evaluation_run_status(self.run_id, "failed", f"{type(exc).__name__}: {exc}")
        return self.run_id
