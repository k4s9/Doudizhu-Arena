"""Reparse original outputs and reconcile memory-study prompts and call budgets."""
from collections import Counter
import csv
from decimal import Decimal
import json
from pathlib import Path
from statistics import mean

from .memory_study import ARMS, arm_order, assess, prompts, read_calls, verify_plan
from .spec import canonical_hash


def audit_memory_study(repo, run_id, *, root=None):
    issues = []
    def check(condition, message):
        if not condition:
            issues.append(message)
    run = repo.get_evaluation_run(run_id)
    plan = json.loads(run["manifest_json"])
    try:
        verify_plan(plan, root)
    except (ValueError, KeyError) as exc:
        issues.append(str(exc))
    check(run["manifest_sha256"] == plan["sha256"], "stored manifest linkage")
    check(run["status"] == "finished", f"run status: {run['status']}")
    check(repo.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
    check(not list(repo.conn.execute("PRAGMA foreign_key_check")), "SQLite foreign keys")
    items = {i["observation_id"]: i for i in plan["corpus"]["observations"] if i["split"] == "test"}
    expected = {(oid, arm) for oid in items for arm in ARMS}
    outcomes = [dict(r) for r in repo.conn.execute("SELECT * FROM memory_study_decisions WHERE run_id=?", (run_id,))]
    check({(r["observation_id"], r["arm"]) for r in outcomes} == expected, "decision coverage")
    calls = read_calls(repo, run_id)
    check(len(calls) == repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs WHERE run_id=?", (run_id,)).fetchone()[0], "physical call evidence coverage")
    ledgers = [dict(r) for r in repo.conn.execute("SELECT * FROM budget_ledger WHERE run_id=?", (run_id,))]
    check(Counter(c["id"] for c in calls) == Counter(r["call_id"] for r in ledgers), "call/budget one-to-one linkage")
    model, protocol = plan["model"], plan["protocol"]
    check(plan["max_calls"] == len(expected) * (1 + protocol["retry_limit"]), "declared physical call bound")
    check(len(calls) <= plan["max_calls"], "physical call cap")
    price = model["pricing"]
    def cost(i, o):
        return (Decimal(i) * Decimal(str(price["input_per_million"]))
                + Decimal(o) * Decimal(str(price["output_per_million"]))) / 1_000_000
    reserved = cost(protocol["max_input_tokens"], model["parameters"]["max_tokens"])
    by_id = {c["id"]: c for c in calls}
    ledger_by_call = {row["call_id"]: row for row in ledgers}
    expected_order = [(oid, arm) for oid in items for arm in arm_order(plan, oid)]
    observed_order = []
    for call in calls:
        key = (call["decision_id"], call["variant_id"])
        if not observed_order or key != observed_order[-1]:
            observed_order.append(key)
    check(observed_order == expected_order, "frozen arm execution order")
    accounted = Decimal(0)
    for row in ledgers:
        check(Decimal(str(row["reserved_usd"])) == reserved, "reservation amount")
        accounted += Decimal(str(row["actual_usd"] if row["actual_usd"] is not None else row["reserved_usd"]))
        call = by_id.get(row["call_id"])
        if call is None:
            continue
        known = call["prompt_tokens"] is not None and call["completion_tokens"] is not None
        check(row["status"] == ("settled" if known else "unknown"), "usage settlement status")
        if known:
            check(0 <= call["prompt_tokens"] <= protocol["max_input_tokens"], "provider input allowance")
            check(0 <= call["completion_tokens"] <= model["parameters"]["max_tokens"], "provider output allowance")
            check(row["actual_usd"] is not None and Decimal(str(row["actual_usd"])) == cost(call["prompt_tokens"], call["completion_tokens"]), "settled cost")
        else:
            check(row["actual_usd"] is None, "unknown usage must remain unknown")
    check(accounted <= Decimal(str(protocol["budget_limit_usd"])), "monetary cap")
    results, consumed = [], []
    for outcome_row in outcomes:
        oid, arm = outcome_row["observation_id"], outcome_row["arm"]
        if (oid, arm) not in expected:
            continue
        item = items[oid]
        chain = sorted((c for c in calls if c["decision_id"] == oid and c["variant_id"] == arm), key=lambda c: c["attempt"])
        check(outcome_row["status"] == "finished", f"unfinished decision: {oid}/{arm}")
        check(0 < len(chain) <= 1 + protocol["retry_limit"], "decision call count")
        if not chain:
            continue
        validations = []
        for attempt, call in enumerate(chain, 1):
            consumed.append(call["id"])
            check(call["attempt"] == attempt, "attempt sequence")
            check(call["provider"] == model["provider"] and call["model"] == model["model"], "requested model")
            check(call["phase"] == item["phase"], "call phase")
            check((call["system_prompt"], call["user_prompt"]) == prompts(plan, item, arm, attempt), "frozen memory/prompt mismatch")
            check(len((call["system_prompt"] + call["user_prompt"]).encode()) + 1024 <= protocol["max_input_tokens"], "prompt input allowance")
            check(call["provider_status"] in ("returned", "error", "cancelled"), "provider status")
            check(bool(call["success"]) == (call["provider_status"] == "returned"), "provider return flag")
            check(call["latency_ms"] is not None and call["latency_ms"] >= 0, "provider latency")
            validation = assess(item, call)
            if validations:
                check(not validations[-1]["valid"], "call after valid output")
            validations.append({"call_id": call["id"], "raw_output_sha256": canonical_hash(call["raw_output"]), **validation})
        success = validations[-1]["valid"]
        check(success or len(chain) == 1 + protocol["retry_limit"], "incomplete retry chain")
        rebuilt = {"chain": validations, "fallback_required": not success, "executed_fallbacks": 0}
        try:
            check(json.loads(outcome_row["outcome_json"] or "null") == rebuilt, "raw output/outcome linkage")
        except json.JSONDecodeError:
            issues.append("malformed outcome JSON")
        known = [c for c in chain if c["prompt_tokens"] is not None and c["completion_tokens"] is not None]
        chain_ledger = [ledger_by_call[c["id"]] for c in chain if c["id"] in ledger_by_call]
        results.append({"observation_id": oid, "source_seed": item["source_seed"], "phase": item["phase"],
                        "arm": arm, "first_valid": validations[0]["valid"], "final_valid": success,
                        "fallback_required": not success, "executed_fallbacks": 0,
                        "calls": len(chain), "retries": len(chain) - 1,
                        "provider_error_calls": sum(c["provider_status"] != "returned" for c in chain),
                        "known_usage_calls": len(known), "unknown_usage_calls": len(chain) - len(known),
                        "known_input_tokens": sum(c["prompt_tokens"] for c in known),
                        "known_output_tokens": sum(c["completion_tokens"] for c in known),
                        "known_cost_usd": str(sum((cost(c["prompt_tokens"], c["completion_tokens"]) for c in known), Decimal(0))),
                        "accounted_cost_usd": str(sum((Decimal(str(r["actual_usd"] if r["actual_usd"] is not None else r["reserved_usd"]))
                                                       for r in chain_ledger), Decimal(0))),
                        "latency_ms": sum(c["latency_ms"] or 0 for c in chain),
                        "call_ids": [c["id"] for c in chain]})
    check(Counter(consumed) == Counter(list(by_id)), "every physical call consumed exactly once")
    return {"complete": not issues, "issues": issues, "run_id": run_id, "plan_sha256": plan["sha256"],
            "physical_calls": len(calls), "accounted_usd": str(accounted)}, results, calls, plan


def metrics(rows):
    if not rows:
        return {"decisions": 0}
    return {"decisions": len(rows), "first_valid_rate": mean(r["first_valid"] for r in rows),
            "final_valid_rate": mean(r["final_valid"] for r in rows),
            "fallback_required_rate": mean(r["fallback_required"] for r in rows),
            "executed_fallbacks": 0, "mean_retries": mean(r["retries"] for r in rows),
            "mean_provider_latency_ms": mean(r["latency_ms"] for r in rows),
            **{key: str(sum((Decimal(r[key]) for r in rows), Decimal(0)))
               for key in ("known_cost_usd", "accounted_cost_usd")},
            **{key: sum(r[key] for r in rows) for key in ("calls", "provider_error_calls", "known_usage_calls",
                                                       "unknown_usage_calls", "known_input_tokens", "known_output_tokens")}}


def export_memory_report(repo, run_id, output, *, root=None):
    audit, rows, calls, plan = audit_memory_study(repo, run_id, root=root)
    paired = []
    for seed in plan["corpus"]["test_seeds"]:
        per_arm = {arm: metrics([r for r in rows if r["source_seed"] == seed and r["arm"] == arm]) for arm in ARMS}
        entry = {"source_seed": seed, "arms": per_arm}
        if all(per_arm[a].get("decisions") for a in ARMS):
            entry["frozen_minus_initial_final_valid"] = per_arm["frozen_memory"]["final_valid_rate"] - per_arm["fixed_initial"]["final_valid_rate"]
            entry["frozen_minus_none_final_valid"] = per_arm["frozen_memory"]["final_valid_rate"] - per_arm["no_memory"]["final_valid_rate"]
            for control, label in (("fixed_initial", "initial"), ("no_memory", "none")):
                entry[f"frozen_minus_{label}_accounted_cost_usd"] = str(
                    Decimal(per_arm["frozen_memory"]["accounted_cost_usd"]) - Decimal(per_arm[control]["accounted_cost_usd"]))
        paired.append(entry)
    summary = {**audit, "provenance": "mock" if plan["mock"] else "real", "memory_slot": plan["protocol"]["memory_slot"],
               "memory_artifact_sha256": plan["memory_artifact"]["artifact_sha256"],
               "arms": {arm: {phase: metrics([r for r in rows if r["arm"] == arm and (phase == "all" or r["phase"] == phase)])
                              for phase in ("all", "bidding", "playing")} for arm in ARMS},
               "paired_by_seed": paired,
               "returned_models": sorted({c["response_model"] for c in calls if c["response_model"]}),
               "system_fingerprints": sorted({c["system_fingerprint"] for c in calls if c["system_fingerprint"]}),
               "limitations": ["Mock results verify the pipeline only; they do not show learning gains." if plan["mock"] else "Descriptive fixed-observation comparison; no causal or statistical significance claim.",
                   "No full games are executed: no win rate, score or playing-strength estimate.",
                   "fallback_required marks exhausted model output; no fallback action is executed.",
                   "One predeclared frozen memory slot; no reflection or memory updates during evaluation.",
                   "Rule-policy corpus and seed-paired descriptive outcomes; not representative human play.",
                   "Known token totals exclude unknown usage; unknown cost retains its reserved allowance.",
                   "Provider randomness can remain despite frozen inputs; exact output reproduction is provider-dependent."]}
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("summary.json", summary), ("decisions.json", rows)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "paired-seed.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_seed", "arm", "decisions", "first_valid_rate", "final_valid_rate", "mean_retries", "fallback_required_rate", "known_cost_usd", "accounted_cost_usd"])
        for entry in paired:
            for arm, values in entry["arms"].items():
                writer.writerow([entry["source_seed"], arm, *[values.get(key) for key in
                    ("decisions", "first_valid_rate", "final_valid_rate", "mean_retries", "fallback_required_rate", "known_cost_usd", "accounted_cost_usd")]])
    text = ["# 固定局面记忆对照评测", "", f"来源：{summary['provenance']}；审计通过：{summary['complete']}；物理调用：{len(calls)}。", "",
            "本报告只衡量固定可见局面的输出合法性和成本，没有完整对局分数或胜率。mock 不证明学习增益。", "",
            "| 组别 | 决策数 | 首答合法率 | 最终合法率 | 平均重试 | 需要兜底率 | 预算占用 USD |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        m = summary["arms"][arm]["all"]
        if m["decisions"]:
            text.append(f"| {arm} | {m['decisions']} | {m['first_valid_rate']:.1%} | {m['final_valid_rate']:.1%} | {m['mean_retries']:.2f} | {m['fallback_required_rate']:.1%} | {m['accounted_cost_usd']} |")
    text += ["", "“需要兜底”只表示模型重试耗尽，本实验实际执行的兜底动作数为 0。逐 seed 对照见 paired-seed.csv。", "", *summary["limitations"]]
    (out / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return summary
