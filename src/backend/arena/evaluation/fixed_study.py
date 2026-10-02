"""Budgeted shared-first-response experiment with durable physical call evidence.

The baseline response is paid for once, then referenced by all three protocols.
Only failed first responses branch. This estimates feedback's incremental value
for this frozen observation/first-response sample, not self-play strength.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import random
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agent.llm_agent import LLMAgent
from ..agent.parser import ParseError
from ..llm.logging import LoggingLLMProvider
from ..llm.openai import OpenAIProvider
from ..llm.claude import ClaudeProvider
from .budget import BudgetLedger
from .execution import ExecutionScope
from .fixed_corpus import verify_corpus
from .observations import validate_output
from .spec import canonical_hash, validate_execution

ARMS = ("generic_retry", "rule_feedback")
GENERIC = "\n\n请重新独立生成一个完整 JSON 回复。"
DDL = """
CREATE TABLE IF NOT EXISTS fixed_items (
 run_id TEXT NOT NULL REFERENCES evaluation_runs(id), observation_id TEXT NOT NULL,
 status TEXT NOT NULL, outcome_json TEXT, PRIMARY KEY(run_id, observation_id)
);
CREATE TABLE IF NOT EXISTS fixed_calls (
 call_id TEXT PRIMARY KEY REFERENCES llm_call_logs(id), run_id TEXT NOT NULL,
 observation_id TEXT NOT NULL, arm TEXT NOT NULL, attempt INTEGER NOT NULL,
 validation_json TEXT NOT NULL, UNIQUE(run_id, observation_id, arm, attempt)
);
"""


class FixedProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["shared-first-response-v1"] = "shared-first-response-v1"
    split: str = Field(default="test", pattern="^(development|test)$")
    concurrency: int = Field(default=2, ge=1, le=4)
    call_timeout_seconds: float = Field(default=300, gt=0, le=600)
    decision_timeout_seconds: float = Field(default=600, gt=0, le=1800)
    cancellation_deadline_seconds: float = Field(default=5, gt=0, le=30)
    retry_limit: int = Field(default=2, ge=1, le=2)
    schedule_seed: str = "fixed-feedback-order-20260926-v1"
    bootstrap_seed: str = "fixed-feedback-bootstrap-20260926-v1"
    bootstrap_resamples: int = Field(default=20000, ge=1000, le=100000)
    primary_phase: Literal["playing"] = "playing"
    primary_contrast: Literal["rule_feedback_minus_generic_retry"] = "rule_feedback_minus_generic_retry"
    first_response: str = "one physical response shared across all protocols"
    provider_errors: str = "same generic retry in both branches"
    latency_estimand: str = "sum of own provider call durations, including shared first; excludes queue and branch scheduling"
    model_version_policy: str = "freeze requested model; record all returned names/fingerprints; mixed names limit version-specific conclusions"


def assess(item, call):
    if call["provider_status"] != "returned":
        return {"valid": False, "action": None, "error_kind": "provider_error",
                "error": call["error_message"] or "provider did not return a response"}
    try:
        action = validate_output(item, call["raw_output"] or "")
        return {"valid": True, "action": action, "error_kind": None, "error": None}
    except ParseError as exc:
        return {"valid": False, "action": None, "error_kind": "invalid_output", "error": str(exc)}


def retry_prompt(user, validation, arm, attempt):
    if arm == "rule_feedback" and validation["error_kind"] == "invalid_output":
        return LLMAgent._append_retry_feedback(user, validation["error"], attempt)
    return user + GENERIC


def order_for(protocol, observation_id, round_index):
    arms = list(ARMS)
    random.Random(f"{protocol.schedule_seed}/{observation_id}/{round_index}").shuffle(arms)
    return arms


def read_call(repo, call_id):
    row = repo.conn.execute("""SELECT l.*,e.system_prompt,e.user_prompt,e.raw_output,e.provider_status
        FROM llm_call_logs l JOIN call_evidence e ON e.call_id=l.id WHERE l.id=?""", (call_id,)).fetchone()
    if row is None:
        raise ValueError("physical call evidence missing")
    return dict(row)


def make_manifest(spec, run_manifest, corpus, protocol):
    if protocol.version != "shared-first-response-v1" or protocol.primary_phase != "playing" or protocol.primary_contrast != "rule_feedback_minus_generic_retry":
        raise ValueError("unsupported fixed protocol")
    selected = [item for item in corpus["observations"] if item["split"] == protocol.split]
    seeds = corpus[f"{protocol.split}_seeds"]
    if list(run_manifest.seeds) != seeds:
        raise ValueError("manifest seeds must exactly match the selected corpus split")
    expected = {"single_generation": (0, False), "generic_retry": (protocol.retry_limit, False),
                "rule_feedback": (protocol.retry_limit, True)}
    actual = {v.variant_id: (v.retry_limit, v.rule_feedback) for v in spec.variants}
    if actual != expected:
        raise ValueError("protocol retry controls differ from frozen experiment")
    if run_manifest.models[0].system_prompt:
        raise ValueError("fixed corpus owns prompts; use an explicit empty system_prompt override")
    bound = len(selected) * (1 + 2 * protocol.retry_limit)
    if spec.max_calls != bound:
        raise ValueError(f"max_calls must equal the predeclared physical bound {bound}")
    if spec.cancellation_deadline_seconds != protocol.cancellation_deadline_seconds:
        raise ValueError("cancellation controls disagree")
    manifest = {"kind": "fixed-observation-effect-study", "run_manifest": asdict(run_manifest),
                "protocol": protocol.model_dump(mode="json"), "corpus_sha256": corpus["sha256"],
                "observation_ids": [item["observation_id"] for item in selected], "physical_call_bound": bound}
    manifest["sha256"] = canonical_hash(manifest)
    return manifest


class FixedStudyRunner:
    def __init__(self, repo, root, spec, run_manifest, corpus, protocol, *, provider_factory=None):
        self.repo, self.root, self.spec = repo, Path(root), spec
        self.base_manifest, self.corpus, self.protocol = run_manifest, corpus, protocol
        self.manifest = make_manifest(spec, run_manifest, corpus, protocol)
        self.provider_factory = provider_factory
        self.cancelled = False
        self.tasks = set()
        self.detached = set()
        self.run_id = None

    def cancel(self):
        self.cancelled = True
        for task in list(self.tasks):
            task.cancel()

    def _consume(self, task):
        self.detached.discard(task)
        if not task.cancelled():
            task.exception()

    async def _stop_call(self, worker, scope):
        scope.cancel_requested = True
        worker.cancel()
        done, _ = await asyncio.wait({worker}, timeout=self.protocol.cancellation_deadline_seconds)
        scope.revoke()
        if done:
            await asyncio.gather(worker, return_exceptions=True)
        else:
            self.detached.add(worker)
            worker.add_done_callback(self._consume)

    def _provider(self):
        if self.provider_factory:
            return self.provider_factory()
        model = self.base_manifest.models[0]
        credential = self.repo.get_player_config_by_name(model.config_name)["api_key"]
        kwargs = {k: v for k, v in model.parameters.items() if v is not None}
        if model.provider == "claude":
            kwargs.pop("seed", None)
        return (ClaudeProvider if model.provider == "claude" else OpenAIProvider)(
            model=model.model, api_key=credential, base_url=model.base_url, **kwargs)

    async def _call(self, item, arm, attempt, user, remaining):
        scope = ExecutionScope()
        inner = self._provider()
        provider = LoggingLLMProvider(inner, repo=scope.guard(self.repo), agent_id=self.player_id,
                                      budget=scope.guard(self.budget), execution_scope=scope)
        provider.set_context(run_id=self.run_id, variant_id=arm, decision_id=item["observation_id"],
                             attempt=attempt, phase=item["phase"])
        worker = asyncio.create_task(provider.generate(user, item["system_prompt"]))
        try:
            try:
                done, _ = await asyncio.wait({worker}, timeout=min(remaining, self.protocol.call_timeout_seconds))
            except asyncio.CancelledError:
                await self._stop_call(worker, scope)
                raise
            if not done:
                await self._stop_call(worker, scope)
            else:
                from ..llm.base import LLMError
                try:
                    await worker
                except LLMError:
                    pass  # Recorded provider failure participates in both protocols.
            row = self.repo.conn.execute("""SELECT id FROM llm_call_logs
                WHERE run_id=? AND decision_id=? AND variant_id=? AND attempt=?""",
                (self.run_id, item["observation_id"], arm, attempt)).fetchone()
            if row is None:
                raise RuntimeError("call did not produce durable evidence")
            call = read_call(self.repo, row[0])
            validation = assess(item, call)
            self.repo.conn.execute("INSERT INTO fixed_calls VALUES(?,?,?,?,?,?)", (
                call["id"], self.run_id, item["observation_id"], arm, attempt, json.dumps(validation, ensure_ascii=False)))
            self.repo.conn.commit()
            return {"call_id": call["id"], "validation": validation, "latency_ms": call["latency_ms"]}
        finally:
            if worker.done():
                client = getattr(inner, "_client", None)
                if client is not None:
                    try:
                        await asyncio.wait_for(client.close(), 5)
                    except (Exception, asyncio.CancelledError):
                        pass

    async def _observation(self, item, semaphore):
        oid = item["observation_id"]
        try:
            async with semaphore:
                if self.cancelled:
                    raise asyncio.CancelledError
                self.repo.conn.execute("UPDATE fixed_items SET status='running' WHERE run_id=? AND observation_id=?", (self.run_id, oid))
                self.repo.conn.commit()
                first = await self._call(item, "shared_first", 1, item["user_prompt"], self.protocol.decision_timeout_seconds)
                chains = {arm: [first] for arm in ARMS}
                prompts = {arm: item["user_prompt"] for arm in ARMS}
                schedule = []
                for retry_index in range(1, self.protocol.retry_limit + 1):
                    for arm in order_for(self.protocol, oid, retry_index):
                        chain = chains[arm]
                        remaining = self.protocol.decision_timeout_seconds - sum(c["latency_ms"] for c in chain) / 1000
                        if chain[-1]["validation"]["valid"] or remaining <= 0:
                            continue
                        prompts[arm] = retry_prompt(prompts[arm], chain[-1]["validation"], arm, retry_index)
                        call = await self._call(item, arm, retry_index + 1, prompts[arm], remaining)
                        chain.append(call)
                        schedule.append({"arm": arm, "attempt": retry_index + 1, "call_id": call["call_id"]})
                outcome = {"first_call_id": first["call_id"], "schedule": schedule, "branches": {}}
                for arm, chain in chains.items():
                    outcome["branches"][arm] = {
                        "call_ids": [c["call_id"] for c in chain], "success": chain[-1]["validation"]["valid"],
                        "action": chain[-1]["validation"]["action"],
                        "stop_reason": "valid" if chain[-1]["validation"]["valid"] else (
                            "exhausted" if len(chain) == 1 + self.protocol.retry_limit else "deadline"),
                    }
                self.repo.conn.execute("UPDATE fixed_items SET status='finished',outcome_json=? WHERE run_id=? AND observation_id=?",
                                       (json.dumps(outcome, ensure_ascii=False), self.run_id, oid))
                self.repo.conn.commit()
        except asyncio.CancelledError:
            self.repo.conn.execute("UPDATE fixed_items SET status='cancelled' WHERE run_id=? AND observation_id=?", (self.run_id, oid))
            self.repo.conn.commit()
        except Exception as exc:
            self.repo.conn.execute("UPDATE fixed_items SET status='failed',outcome_json=? WHERE run_id=? AND observation_id=?",
                                   (json.dumps({"error": f"{type(exc).__name__}: {exc}"}), self.run_id, oid))
            self.repo.conn.commit()
            # An infrastructure/budget/evidence fault invalidates the batch.
            self.cancelled = True
            for task in list(self.tasks):
                if task is not asyncio.current_task():
                    task.cancel()

    async def run(self, *, real_models=False, mock=False):
        if real_models == mock:
            raise ValueError("select exactly one explicit real/mock execution mode")
        validate_execution(self.spec, self.base_manifest, self.root, self.repo, mock=mock)
        verify_corpus(self.corpus)
        if self.manifest != make_manifest(self.spec, self.base_manifest, self.corpus, self.protocol):
            raise ValueError("fixed manifest changed before execution")
        self.repo.conn.executescript(DDL)
        eid = self.repo.create_experiment(self.spec.experiment_id, self.spec.model_dump(mode="json"), self.base_manifest.spec_sha256)
        self.run_id = self.repo.create_evaluation_run(eid, self.manifest, self.manifest["sha256"])
        model = self.base_manifest.models[0]
        self.repo.add_model_snapshot(self.run_id, asdict(model))
        config = self.repo.get_player_config_by_name(model.config_name)
        if config is None and mock:
            cid = self.repo.create_player_config(model.config_name, "mock", model.model, "")
        else:
            cid = config["id"]
        self.player_id = self.repo.create_player(cid, "fixed-observation benchmark")
        self.budget = BudgetLedger(self.repo, self.run_id, self.spec.budget_limit_usd, self.spec.max_calls,
                                   self.spec.max_input_tokens, model.parameters["max_tokens"], model.pricing)
        items = [item for item in self.corpus["observations"] if item["split"] == self.protocol.split]
        self.repo.conn.executemany("INSERT INTO fixed_items VALUES(?,?,'planned',NULL)",
                                  [(self.run_id, item["observation_id"]) for item in items])
        self.repo.conn.commit()
        self.repo.update_evaluation_run_status(self.run_id, "running")
        # Mix seeds/categories across time while keeping the ordering reproducible.
        random.Random(self.protocol.schedule_seed).shuffle(items)
        semaphore = asyncio.Semaphore(self.protocol.concurrency)
        self.tasks = {asyncio.create_task(self._observation(item, semaphore)) for item in items}
        try:
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            self.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
        finally:
            self.repo.conn.execute("UPDATE fixed_items SET status='cancelled' WHERE run_id=? AND status IN ('planned','running')", (self.run_id,))
            self.repo.conn.commit()
            states = [r[0] for r in self.repo.conn.execute("SELECT status FROM fixed_items WHERE run_id=?", (self.run_id,))]
            status = "failed" if "failed" in states else ("cancelled" if self.cancelled else "finished")
            self.repo.update_evaluation_run_status(self.run_id, status)
        return self.run_id
