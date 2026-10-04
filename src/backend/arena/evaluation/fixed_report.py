"""Offline semantic/ledger audit and paired seed-cluster effect estimates."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from decimal import Decimal
import json
import math
from pathlib import Path
import random
from statistics import mean

from .fixed_corpus import verify_corpus
from .fixed_study import ARMS, FixedProtocol, assess, make_manifest, order_for, read_call, retry_prompt
from .spec import ExperimentSpec, canonical_hash, load_manifest, source_sha256


def audit_fixed(repo, run_id, corpus, *, root=None):
    issues = []
    def check(ok, message):
        if not ok:
            issues.append(message)
    run = repo.get_evaluation_run(run_id)
    manifest = json.loads(run["manifest_json"])
    check(manifest["sha256"] == canonical_hash({k: v for k, v in manifest.items() if k != "sha256"}), "fixed manifest hash")
    check(run["manifest_sha256"] == manifest["sha256"], "stored manifest linkage")
    base = manifest["run_manifest"]
    check(base["manifest_sha256"] == canonical_hash({k: v for k, v in base.items() if k != "manifest_sha256"}), "base manifest hash")
    check(base["spec_sha256"] == canonical_hash(base["frozen_spec"]), "frozen specification hash")
    runtime_source = source_sha256(root) if root is not None else None
    source_version_check = "not_checked" if runtime_source is None else (
        "matched" if runtime_source == base["source_sha256"] else "mismatch"
    )
    if root is not None:
        check(source_version_check == "matched", "runtime source differs from frozen source")
    try:
        verify_corpus(corpus)
    except ValueError as exc:
        issues.append(str(exc))
    check(manifest["corpus_sha256"] == corpus["sha256"], "corpus linkage")
    check(repo.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
    check(not list(repo.conn.execute("PRAGMA foreign_key_check")), "SQLite foreign keys")
    protocol = FixedProtocol.model_validate(manifest["protocol"])
    try:
        rebuilt = make_manifest(ExperimentSpec.model_validate(base["frozen_spec"]), load_manifest(base), corpus, protocol)
        # Dataclass tuples become JSON arrays in SQLite. Compare canonical
        # serializations so a valid round trip is not mistaken for corruption.
        check(canonical_hash(rebuilt) == canonical_hash(manifest), "frozen protocol/specification agreement")
    except ValueError as exc:
        issues.append(f"protocol controls: {exc}")
    items = {item["observation_id"]: item for item in corpus["observations"] if item["split"] == protocol.split}
    check(manifest["observation_ids"] == list(items), "selected observation IDs/order")
    check(base["seeds"] == corpus[f"{protocol.split}_seeds"], "selected source seeds")
    check(manifest["physical_call_bound"] == len(items) * (1 + 2 * protocol.retry_limit), "physical call bound")
    item_rows = [dict(r) for r in repo.conn.execute("SELECT * FROM fixed_items WHERE run_id=?", (run_id,))]
    check(set(items) == {r["observation_id"] for r in item_rows}, "planned observation coverage")
    check(run["status"] == "finished", f"run status: {run['status']}")
    check(all(r["status"] == "finished" for r in item_rows), "unfinished observations")
    ids = [r[0] for r in repo.conn.execute("SELECT id FROM llm_call_logs WHERE run_id=?", (run_id,))]
    calls = {}
    for cid in ids:
        try:
            calls[cid] = read_call(repo, cid)
        except ValueError as exc:
            issues.append(str(exc))
    entries = [dict(r) for r in repo.conn.execute("SELECT * FROM fixed_calls WHERE run_id=?", (run_id,))]
    check(set(calls) == {r["call_id"] for r in entries}, "physical call/validation coverage")
    ledgers = [dict(r) for r in repo.conn.execute("SELECT * FROM budget_ledger WHERE run_id=?", (run_id,))]
    check(Counter(r["call_id"] for r in ledgers) == Counter(ids), "physical call/budget one-to-one linkage")
    spec, model = base["frozen_spec"], base["models"][0]
    check(len(ledgers) <= spec["max_calls"] == manifest["physical_call_bound"], "call count cap")
    price = model["pricing"]
    def cost(i, o):
        return (Decimal(str(i)) * Decimal(str(price["input_per_million"])) +
                Decimal(str(o)) * Decimal(str(price["output_per_million"]))) / 1000000
    reserved = cost(spec["max_input_tokens"], model["parameters"]["max_tokens"])
    accounted = Decimal(0)
    for ledger in ledgers:
        call = calls.get(ledger["call_id"])
        check(Decimal(str(ledger["reserved_usd"])) == reserved, "reservation amount")
        accounted += Decimal(str(ledger["actual_usd"] if ledger["actual_usd"] is not None else ledger["reserved_usd"]))
        if call is None:
            continue
        known = call["prompt_tokens"] is not None and call["completion_tokens"] is not None
        check(ledger["status"] == ("settled" if known else "unknown"), "unknown/settled usage status")
        if known:
            check(0 <= call["prompt_tokens"] <= spec["max_input_tokens"], "input allowance")
            check(0 <= call["completion_tokens"] <= model["parameters"]["max_tokens"], "output allowance")
            check(ledger["actual_usd"] is not None and Decimal(str(ledger["actual_usd"])) == cost(call["prompt_tokens"], call["completion_tokens"]), "settled amount")
        else:
            check(ledger["actual_usd"] is None, "unknown usage must not be zero cost")
    check(accounted <= Decimal(str(spec["budget_limit_usd"])), "monetary cap")
    indexed = {}
    for entry in entries:
        oid, cid = entry["observation_id"], entry["call_id"]
        if oid not in items or cid not in calls:
            issues.append("call references unplanned observation or missing evidence")
            continue
        call = calls[cid]
        check(call["decision_id"] == oid and call["variant_id"] == entry["arm"] and call["attempt"] == entry["attempt"], "call correlation")
        check(call["phase"] == items[oid]["phase"], "call phase")
        check(bool(call["success"]) == (call["provider_status"] == "returned"), "provider return status")
        check(call["provider_status"] in ("returned", "error", "cancelled"), "unknown provider status")
        check(call["latency_ms"] is not None and call["latency_ms"] >= 0, "call latency")
        check(len((call["system_prompt"] + call["user_prompt"]).encode()) + 1024 <= spec["max_input_tokens"], "frozen input byte bound")
        check(call["provider"] == model["provider"] and call["model"] == model["model"], "requested provider/model")
        check(call["system_prompt"] == items[oid]["system_prompt"], "frozen system prompt")
        validation = assess(items[oid], call)
        check(json.loads(entry["validation_json"]) == validation, f"semantic validation: {cid}")
        indexed[oid, entry["arm"], entry["attempt"]] = (call, validation)
    results = []
    consumed = set()
    for row in item_rows:
        if row["status"] != "finished" or row["observation_id"] not in items:
            continue
        oid = row["observation_id"]
        item = items[oid]
        try:
            first, validation = indexed[oid, "shared_first", 1]
            check(first["user_prompt"] == item["user_prompt"], "frozen first prompt")
            consumed.add(first["id"])
            chains = {arm: [(first, validation)] for arm in ARMS}
            prompts = {arm: item["user_prompt"] for arm in ARMS}
            schedule = []
            for index in range(1, protocol.retry_limit + 1):
                for arm in order_for(protocol, oid, index):
                    chain = chains[arm]
                    remaining = protocol.decision_timeout_seconds - sum(c[0]["latency_ms"] for c in chain) / 1000
                    if chain[-1][1]["valid"] or remaining <= 0:
                        check((oid, arm, index + 1) not in indexed, "call after success/deadline")
                        continue
                    call, validation = indexed[oid, arm, index + 1]
                    prompts[arm] = retry_prompt(prompts[arm], chain[-1][1], arm, index)
                    check(call["user_prompt"] == prompts[arm], "retry prompt/control mismatch")
                    consumed.add(call["id"])
                    chain.append((call, validation))
                    schedule.append({"arm": arm, "attempt": index + 1, "call_id": call["id"]})
            expected = {"first_call_id": first["id"], "schedule": schedule, "branches": {}}
            for arm, chain in chains.items():
                expected["branches"][arm] = {
                    "call_ids": [c[0]["id"] for c in chain], "success": chain[-1][1]["valid"],
                    "action": chain[-1][1]["action"], "stop_reason": "valid" if chain[-1][1]["valid"] else (
                        "exhausted" if len(chain) == protocol.retry_limit + 1 else "deadline")}
            check(json.loads(row["outcome_json"]) == expected, f"outcome/physical call linkage: {oid}")
            for arm, chain in [("single_generation", [(first, assess(item, first))]), *chains.items()]:
                physical = [c[0] for c in chain]
                known = [c for c in physical if c["prompt_tokens"] is not None and c["completion_tokens"] is not None]
                names = sorted({c["response_model"] for c in physical if c["response_model"]})
                results.append({
                    "observation_id": oid, "source_seed": item["source_seed"], "phase": item["phase"],
                    "category": item["category"], "arm": arm, "success": chain[-1][1]["valid"],
                    "first_valid": chain[0][1]["valid"], "first_error_kind": chain[0][1]["error_kind"],
                    "calls": len(chain), "known_usage_calls": len(known),
                    "known_input_tokens": sum(c["prompt_tokens"] for c in known),
                    "known_output_tokens": sum(c["completion_tokens"] for c in known),
                    "call_time_ms": sum(c["latency_ms"] for c in physical),
                    "response_models": names, "call_ids": [c["id"] for c in physical],
                    "complete_usage": len(known) == len(physical),
                })
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            issues.append(f"missing/malformed chain for {oid}: {exc}")
    check(consumed == set(calls), "unconsumed/orphan physical calls")
    return {"complete": not issues, "issues": sorted(set(issues)), "run_id": run_id,
            "manifest_sha256": manifest["sha256"], "planned_observations": len(items),
            "finished_observations": sum(r["status"] == "finished" for r in item_rows),
            "physical_calls": len(calls), "usage_known_calls": sum(c["prompt_tokens"] is not None and c["completion_tokens"] is not None for c in calls.values()),
            "source_version_check": source_version_check,
            "frozen_source_sha256": base["source_sha256"], "runtime_source_sha256": runtime_source,
            "source_replay": True}, results, list(calls.values()), manifest


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def paired_interval(seed_values, seed, resamples):
    values = list(seed_values)
    if not values:
        return None
    rng = random.Random(seed)
    draws = [sum(rng.choices(values, k=len(values))) / len(values) for _ in range(resamples)]
    return {"estimate": mean(values), "ci95": [percentile(draws, .025), percentile(draws, .975)],
            "independent_seeds": len(values), "resamples": resamples,
            "degenerate": len(set(values)) == 1,
            "method": "paired source-seed cluster percentile bootstrap; equal seed weights"}


def pair_response_identity(generic, feedback, calls_by_id):
    """Classify complete paired chains, counting their shared first call once.

    A returned name is only a provider label. Matching names/fingerprints do
    not prove an immutable weight version, and missing metadata is not a match.
    """
    physical = [calls_by_id[cid] for cid in set(generic["call_ids"] + feedback["call_ids"])]
    names = {c["response_model"] for c in physical if c["response_model"]}
    if not physical or any(not c["response_model"] for c in physical):
        return False, None, "missing_response_model"
    if len(names) != 1:
        return False, None, "mixed_response_models"
    fingerprints = {c["system_fingerprint"] for c in physical if c["system_fingerprint"]}
    known_fingerprints = sum(bool(c["system_fingerprint"]) for c in physical)
    if len(fingerprints) > 1:
        return True, None, "mixed_fingerprints"
    if 0 < known_fingerprints < len(physical):
        return True, None, "partial_fingerprint_coverage"
    return True, (next(iter(names)), next(iter(fingerprints), None)), None


def summarize(results, manifest, integrity, calls):
    protocol = FixedProtocol.model_validate(manifest["protocol"])
    calls_by_id = {c["id"]: c for c in calls}
    metrics = []
    for phase in ("all", "bidding", "playing"):
        for arm in ("single_generation", *ARMS):
            group = [r for r in results if r["arm"] == arm and (phase == "all" or r["phase"] == phase)]
            if not group:
                continue
            invalid = [r for r in group if r["first_error_kind"] == "invalid_output"]
            provider_failed = [r for r in group if r["first_error_kind"] == "provider_error"]
            metrics.append({"phase": phase, "arm": arm, "n": len(group),
                "successes": sum(r["success"] for r in group), "success_rate": mean(r["success"] for r in group),
                "initial_invalid": len(invalid), "invalid_recovered": sum(r["success"] for r in invalid),
                "initial_provider_errors": len(provider_failed), "provider_recovered": sum(r["success"] for r in provider_failed),
                "logical_calls": sum(r["calls"] for r in group), "known_usage_calls": sum(r["known_usage_calls"] for r in group),
                "known_input_tokens": sum(r["known_input_tokens"] for r in group),
                "known_output_tokens": sum(r["known_output_tokens"] for r in group),
                "mean_call_time_ms": mean(r["call_time_ms"] for r in group),
                "call_time_p50_ms": percentile([r["call_time_ms"] for r in group], .5),
                "call_time_p95_ms": percentile([r["call_time_ms"] for r in group], .95)})
    paired, seed_rows = {}, []
    for phase in ("playing", "all", "bidding"):
        by_seed = defaultdict(list)
        lookup = {(r["observation_id"], r["arm"]): r for r in results}
        for row in results:
            if row["arm"] != "rule_feedback" or (phase != "all" and row["phase"] != phase):
                continue
            generic = lookup[row["observation_id"], "generic_retry"]
            by_seed[row["source_seed"]].append((generic, row))
        for seed, pairs in sorted(by_seed.items()):
            for arm in ARMS:
                rows = [pair[0 if arm == "generic_retry" else 1] for pair in pairs]
                seed_rows.append({"phase": phase, "source_seed": seed, "arm": arm, "n": len(rows),
                                  "successes": sum(r["success"] for r in rows), "calls": sum(r["calls"] for r in rows)})
        differences = [mean(int(f["success"]) - int(g["success"]) for g, f in pairs) * 100 for pairs in by_seed.values()]
        all_pairs = [pair for pairs in by_seed.values() for pair in pairs]
        stable_pairs, version_groups, excluded = [], defaultdict(list), Counter()
        for generic, feedback in all_pairs:
            single_name, identity, reason = pair_response_identity(generic, feedback, calls_by_id)
            if single_name:
                stable_pairs.append((generic, feedback))
            if identity is None:
                excluded[reason] += 1
            else:
                version_groups[identity].append((generic, feedback))
        grouped = []
        for (name, fingerprint), pairs in sorted(version_groups.items(), key=lambda entry: (entry[0][0], entry[0][1] or "")):
            grouped.append({
                "response_model": name, "system_fingerprint": fingerprint,
                "metadata_scope": "name_and_fingerprint" if fingerprint else "name_only_fingerprint_unavailable",
                "pairs": len(pairs), "source_seeds": len({f["source_seed"] for _, f in pairs}),
                "feedback_only_success": sum(f["success"] and not g["success"] for g, f in pairs),
                "generic_only_success": sum(g["success"] and not f["success"] for g, f in pairs),
                "both_success": sum(g["success"] and f["success"] for g, f in pairs),
                "both_failed": sum(not g["success"] and not f["success"] for g, f in pairs),
                "success_difference_pp_descriptive": mean(int(f["success"]) - int(g["success"]) for g, f in pairs) * 100,
            })
        paired[phase] = {
            "comparison_target": "requested model alias/endpoint across all recorded response identities",
            "success_difference_percentage_points": paired_interval(differences, protocol.bootstrap_seed + phase, protocol.bootstrap_resamples) if integrity["complete"] else None,
            "feedback_only_success": sum(f["success"] and not g["success"] for g, f in all_pairs),
            "generic_only_success": sum(g["success"] and not f["success"] for g, f in all_pairs),
            "both_success": sum(g["success"] and f["success"] for g, f in all_pairs),
            "both_failed": sum(not g["success"] and not f["success"] for g, f in all_pairs),
            "mean_extra_calls": mean(f["calls"] - g["calls"] for g, f in all_pairs) if all_pairs else None,
            "mean_extra_call_time_ms": mean(f["call_time_ms"] - g["call_time_ms"] for g, f in all_pairs) if all_pairs else None,
            "complete_usage_pairs": sum(g["complete_usage"] and f["complete_usage"] for g, f in all_pairs),
            "mean_extra_tokens_known_pairs": mean(
                f["known_input_tokens"] + f["known_output_tokens"] - g["known_input_tokens"] - g["known_output_tokens"]
                for g, f in all_pairs if g["complete_usage"] and f["complete_usage"])
                if any(g["complete_usage"] and f["complete_usage"] for g, f in all_pairs) else None,
            "single_returned_name_pairs": len(stable_pairs),
            "single_returned_name_difference_pp_descriptive": mean(int(f["success"]) - int(g["success"]) for g, f in stable_pairs) * 100 if stable_pairs else None,
            "response_identity_groups_descriptive": grouped,
            "response_identity_excluded_pairs": dict(sorted(excluded.items())),
        }
    limitations = [
        "Frozen deterministic rule-policy states with fixed category quotas; not representative human play or competitive strength.",
        "Shared first responses correlate protocols deliberately; do not count three independent baseline samples or sum logical calls as actual expenditure.",
        "One response realization per state/model; inference clusters by source seed. It does not cover all provider-time/version variability.",
        "Call-time estimates exclude queue/branch scheduling; they are not measured live-game end-to-end latency.",
        "Requested aliases may resolve to different returned names; names/fingerprints cannot prove weight-version identity.",
        "The overall interval describes the requested alias/endpoint's observed response mixture, not a fixed model revision. Metadata groups are post-observation descriptive subsets, not separate causal estimates.",
        "Single-returned-name pairs require a name on every physical call. Identity groups also exclude changed or partially missing fingerprints; all fingerprints absent is explicitly name-only evidence.",
        "Zero prices follow the previously user-confirmed free account, not an audited provider bill; unknown usage remains unknown.",
        "A degenerate zero bootstrap interval does not establish equivalence or absence of rare failures.",
        "The validator and source replay reuse the project engine; this is not an independent third-party rules audit.",
    ]
    return {"kind": "fixed-observation-effect-study", "complete": integrity["complete"],
            "run_id": integrity["run_id"], "model": manifest["run_manifest"]["models"][0]["model"],
            "source_version_check": integrity["source_version_check"],
            "split": protocol.split, "metrics": metrics, "paired": paired,
            "physical_calls": len(calls), "known_usage_calls": integrity["usage_known_calls"],
            "response_identity_coverage": {
                "physical_calls": len(calls),
                "response_model_known_calls": sum(bool(c["response_model"]) for c in calls),
                "response_model_unknown_calls": sum(not c["response_model"] for c in calls),
                "fingerprint_known_calls": sum(bool(c["system_fingerprint"]) for c in calls),
                "fingerprint_unknown_calls": sum(not c["system_fingerprint"] for c in calls),
            },
            "returned_models": dict(Counter(c["response_model"] for c in calls if c["response_model"])),
            "system_fingerprints": dict(Counter(c["system_fingerprint"] for c in calls if c["system_fingerprint"])),
            "known_physical_input_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
            "known_physical_output_tokens": sum(c["completion_tokens"] or 0 for c in calls),
            "limitations": limitations}, seed_rows


def export_fixed_report(repo, run_id, corpus, output, *, root=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    integrity, results, calls, manifest = audit_fixed(repo, run_id, corpus, root=root)
    summary, seed_rows = summarize(results, manifest, integrity, calls)
    for name, value in (("manifest.json", manifest), ("integrity.json", integrity), ("summary.json", summary)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, values in (("calls.jsonl", calls), ("decisions.jsonl", results)):
        (output / name).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")
    for name, values in (("metrics.csv", summary["metrics"]), ("seed_level.csv", seed_rows)):
        with (output / name).open("w", encoding="utf-8", newline="") as handle:
            if values:
                writer = csv.DictWriter(handle, list(values[0]))
                writer.writeheader()
                writer.writerows(values)
    lines = [f"# 固定局面规则反馈效果实验：{summary['model']}", "",
             f"完整性审计：{'通过' if integrity['complete'] else '未通过；不能据此作完整效果结论'}。",
             "审计源码与冻结版本：" + {
                 "matched": "一致。", "mismatch": "不一致；已将报告标为不完整。",
                 "not_checked": "未检查；不能据此声称在原冻结源码下复算。",
             }[summary["source_version_check"]],
             f"实际调用 {len(calls)} 次；usage 已知 {summary['known_usage_calls']}/{len(calls)}。", "",
             "三协议共享首答；仅失败时分别执行最多两次通用重试和规则反馈重试。", "",
             "| 阶段 | 协议 | 成功/样本 | 首答非法后恢复 | 逻辑调用 | 平均调用耗时合计 ms |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in summary["metrics"]:
        lines.append(f"| {row['phase']} | {row['arm']} | {row['successes']}/{row['n']} | {row['invalid_recovered']}/{row['initial_invalid']} | {row['logical_calls']} | {row['mean_call_time_ms']:.1f} |")
    lines += ["", "逻辑调用包含各协议引用的共同首答，不能相加当成实际消费。零分母表示 N/A。", ""]
    effect = summary["paired"]["playing"]["success_difference_percentage_points"]
    if effect:
        lo, hi = effect["ci95"]
        lines += [f"主指标（出牌）：规则反馈减通用重试为 **{effect['estimate']:.3f} 个百分点**，",
                  f"按 {effect['independent_seeds']} 个独立来源种子配对 bootstrap 的 95% 区间为 **[{lo:.3f}, {hi:.3f}]**。",
                  "区间跨过零时，本次样本不足以确认稳定的增益或劣化。", ""]
    lines += ["总体区间描述本次请求模型别名/端点的实际返回组合，不能当作某个固定权重版本的效果。", "",
              "返回身份分组只描述元数据一致的配对子样本；这些分组是在观察响应之后形成的，不另作因果或显著性结论。", "",
              "| 返回模型名（出牌） | 指纹 | 元数据范围 | 配对数 | 来源 seed 数 | 成功率差 pp（描述性） |",
              "| --- | --- | --- | ---: | ---: | ---: |"]
    for group in summary["paired"]["playing"]["response_identity_groups_descriptive"]:
        lines.append(f"| {group['response_model']} | {group['system_fingerprint'] or '未提供'} | {group['metadata_scope']} | {group['pairs']} | {group['source_seeds']} | {group['success_difference_pp_descriptive']:.3f} |")
    excluded = summary["paired"]["playing"]["response_identity_excluded_pairs"]
    lines += ["", f"因返回名称/指纹缺失或变化而未进入上述分组的出牌配对：{json.dumps(excluded, ensure_ascii=False)}。", ""]
    lines += ["## 解释边界", "", *[f"- {v}" for v in summary["limitations"]], ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary
