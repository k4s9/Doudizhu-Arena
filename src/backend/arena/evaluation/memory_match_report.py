"""Offline, evidence-first audits for the frozen-memory full-match study.

Only evaluated players contribute model metrics. The seeded random opponents
are replayed separately. Match scores are nonnegative duplicate points; their
difference is oriented toward the evaluated team.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

from ..agent.random_agent import RandomAgent
from ..engine.projection import RULES_VERSION
from ..engine.timeout import TimeoutConfig
from ..tournament.match import MatchConfig
from ..tournament.seating import get_dealer_idle
from .audit import budget_issues
from .memory_match_study import (
    arm_memory, baseline_seed, context_from_observation, expected_prompt,
    schedule, seating_for, verify_match_plan,
)
from .memory_study import ARMS
from .report import audit_match, metric_group, rows
from .spec import canonical_hash


def _cost(model, input_tokens, output_tokens):
    price = model["pricing"]
    return (Decimal(input_tokens) * Decimal(str(price["input_per_million"]))
            + Decimal(output_tokens) * Decimal(str(price["output_per_million"]))) / 1_000_000


def _metrics(decisions, calls, events, ledger):
    result = metric_group(decisions, calls, events)
    call_ids = {call["id"] for call in calls}
    entries = [entry for entry in ledger if entry["call_id"] in call_ids]
    result.update(
        known_cost_usd=str(sum((Decimal(str(entry["actual_usd"])) for entry in entries
                                if entry["actual_usd"] is not None), Decimal(0))),
        accounted_cost_usd=str(sum((Decimal(str(entry["actual_usd"] if entry["actual_usd"] is not None
                                              else entry["reserved_usd"])) for entry in entries), Decimal(0))),
        known_usage_calls=sum(call["prompt_tokens"] is not None and call["completion_tokens"] is not None
                              for call in calls),
        unknown_usage_calls=sum(call["prompt_tokens"] is None or call["completion_tokens"] is None
                                for call in calls),
        retries=sum(max(count - 1, 0) for count in Counter(call["decision_id"] for call in calls).values()),
        provider_error_calls=sum(not call["success"] for call in calls),
    )
    return result


def _phase_metrics(decisions, calls, events, ledger):
    result = {}
    for phase in ("all", "bidding", "playing"):
        selected = [decision for decision in decisions if phase == "all" or decision["phase"] == phase]
        ids = {decision["decision_id"] for decision in selected}
        result[phase] = _metrics(selected, [call for call in calls if call["decision_id"] in ids],
                                 [event for event in events if event["decision_id"] in ids], ledger)
    return result


def _audit_roster(repo, plan, task, match, payload, check):
    """Rebuild seats and initial memory independently from the roster's labels."""
    roster = payload["roster"]
    check(len(roster) == 8, "roster must contain eight players")
    expected_slots = {f"target-{i}" for i in range(4)} | {f"baseline-{i}" for i in range(4)}
    by_slot = {entry["slot"]: entry for entry in roster}
    check(set(by_slot) == expected_slots, "roster slot coverage")
    check(len({entry["player_id"] for entry in roster}) == 8, "roster player uniqueness")
    targets = [by_slot[f"target-{i}"]["player_id"] for i in range(4)]
    baselines = [by_slot[f"baseline-{i}"]["player_id"] for i in range(4)]
    side = "red" if task["seat_rotation"] == 0 else "blue"
    seating = seating_for(targets, baselines, side, task["seed"])
    expected_memory, expected_participants = {}, []
    for slot in sorted(expected_slots):
        entry = by_slot[slot]
        target = slot.startswith("target-")
        player_id = entry["player_id"]
        table, seat = seating.agent_seats[player_id]
        check(entry == {"slot": slot, "kind": "evaluated" if target else "baseline", "player_id": player_id,
                        "baseline_seed": None if target else baseline_seed(task["seed"], slot),
                        "table": table, "seat": seat, "team": seating.agent_teams[player_id]},
              f"frozen roster/seat/baseline seed: {slot}")
        expected_participants.append((player_id, seating.agent_teams[player_id],
                                      seat if table == "A" else None, seat if table == "B" else None))
        expected_memory[slot] = {"long_term": arm_memory(plan, task["variant_id"]) if target else "", "short_term": ""}
        player = repo.get_player(player_id)
        check(player is not None, f"missing roster player: {slot}")
        if player:
            config = repo.get_player_config(player["config_id"])
            expected_provider = plan["model"]["provider"] if target else "random"
            expected_model = plan["model"]["model"] if target else "random"
            check(config and (config["provider"], config["model"]) == (expected_provider, expected_model),
                  f"roster player model: {slot}")
            # This study creates fresh players and never writes their shared memory.
            check((player["long_term_memory"] or "") == "", f"persistent memory changed: {slot}")
    actual_participants = [(entry["player_id"], entry["team"], entry["seat_table_a"], entry["seat_table_b"])
                           for entry in repo.get_participants(match["id"])]
    check(Counter(actual_participants) == Counter(expected_participants), "participant/roster linkage")
    check(payload["before"] == expected_memory, "frozen initial memory mismatch")
    check(payload["after"] == expected_memory, "memory changed during evaluation")
    usage = repo.list_match_memory_usage(match["id"])
    check(Counter(entry["player_id"] for entry in usage) == Counter(targets + baselines), "initial memory usage coverage")
    by_player = {entry["player_id"]: entry for entry in roster}
    for entry in usage:
        player = by_player.get(entry["player_id"])
        if player:
            content = expected_memory[player["slot"]]["long_term"]
            check(entry["content"] == content and entry["content_hash"] == hashlib.sha256(content.encode()).hexdigest(),
                  f"initial memory usage content: {player['slot']}")
            check(entry["match_id"] is None, f"initial memory usage references a learned version: {player['slot']}")
    check(not rows(repo, "SELECT id FROM memory_versions WHERE match_id=?", (match["id"],)), "unexpected learned memory version")
    check(not rows(repo, "SELECT id FROM agent_memories WHERE match_id=?", (match["id"],)), "unexpected learning memory")
    check(not rows(repo, "SELECT id FROM memory_failures WHERE match_id=?", (match["id"],)), "unexpected learning failure")
    return by_player


def _audit_baseline(decisions, roster, check):
    agents = {player_id: RandomAgent(player_id, seat=entry["seat"], seed=entry["baseline_seed"])
              for player_id, entry in roster.items() if entry["kind"] == "baseline"}
    # Each instance persists across hands. AB interleaving is irrelevant because
    # separate players have separate RNGs; per-player decision order is retained.
    for decision in decisions:
        agent = agents.get(decision["player_id"])
        if agent is None:
            continue
        ctx = context_from_observation(json.loads(decision["observation_json"]))
        if decision["phase"] == "bidding":
            options = [bid for bid in (0, 1, 2, 3) if bid == 0 or bid > ctx.current_high_bid]
            action = {"bid": agent._rng.choice(options)}
        else:
            cards = (agent._random_leader_play(ctx) if ctx.current_trick is None else agent._random_follow_play(ctx))
            action = {"type": "play" if cards else "pass", "cards": [str(card) for card in cards]}
        check(decision["resolution"] == "system_autoplay" and decision["reason"] is None,
              f"baseline failed or changed policy: {decision['decision_id']}")
        check(json.loads(decision["actual_action"] or "null") == action,
              f"seeded baseline action mismatch: {decision['decision_id']}")


def audit_memory_matches(repo, run_id, *, root=None):
    """Audit a dedicated study database; never repair or mutate its evidence."""
    try:
        return _audit_memory_matches(repo, run_id, root=root)
    except Exception as exc:
        # Malformed JSON, broken schemas and missing mandatory fields must not
        # bypass the integrity label or turn into a successful empty report.
        plan = {}
        try:
            run = repo.get_evaluation_run(run_id)
            candidate = json.loads(run["manifest_json"])
            plan = candidate if isinstance(candidate, dict) else {}
        except Exception:
            pass
        return {"complete": False, "issues": [f"unreadable match evidence: {type(exc).__name__}: {exc}"],
                "run_id": run_id, "plan_sha256": plan.get("sha256"), "physical_calls": None,
                "accounted_usd": None, "arm_metrics": {}}, [], plan


def _audit_memory_matches(repo, run_id, *, root=None):
    issues = []
    def check(condition, message):
        if not condition:
            issues.append(message)
    run = repo.get_evaluation_run(run_id)
    plan = json.loads(run["manifest_json"])
    try:
        verify_match_plan(plan, root)
    except (ValueError, KeyError, TypeError) as exc:
        issues.append(f"frozen plan: {exc}")
    check(run["manifest_sha256"] == plan["sha256"], "stored manifest linkage")
    check(run["status"] == "finished", f"run status: {run['status']}")
    check([entry["id"] for entry in rows(repo, "SELECT id FROM evaluation_runs")] == [run_id],
          "dedicated study database requires exactly one evaluation run")
    check(repo.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
    check(not list(repo.conn.execute("PRAGMA foreign_key_check")), "SQLite foreign keys")
    protocol, model = plan["protocol"], plan["model"]
    tasks = repo.get_evaluation_tasks(run_id)
    expected_order = [(item["seed"], item["arm"], 0 if item["side"] == "red" else 1) for item in schedule(plan)]
    check(Counter((task["seed"], task["variant_id"], task["seat_rotation"]) for task in tasks) == Counter(expected_order),
          "task coverage: every seed/arm requires both sides exactly once")
    check(len(tasks) == len(plan["test_seeds"]) * len(ARMS) * 2, "declared full-match count")
    attempts = rows(repo, """SELECT a.*,t.seed,t.variant_id,t.seat_rotation FROM task_attempts a
        JOIN evaluation_tasks t ON t.id=a.task_id WHERE t.run_id=? ORDER BY a.started_at,a.rowid""", (run_id,))
    check(Counter(attempt["task_id"] for attempt in attempts) == Counter(task["id"] for task in tasks),
          "one attempt per planned task")
    check([(attempt["seed"], attempt["variant_id"], attempt["seat_rotation"]) for attempt in attempts] == expected_order,
          "frozen match execution order")
    for previous, following in zip(attempts, attempts[1:]):
        check(previous["finished_at"] is not None and previous["finished_at"] <= following["started_at"],
              "match tasks overlapped or preceding attempt never finished")
    evidence = rows(repo, """SELECT e.* FROM memory_match_evidence e JOIN evaluation_tasks t
        ON t.id=e.task_id WHERE t.run_id=?""", (run_id,))
    check(Counter(entry["task_id"] for entry in evidence) == Counter(task["id"] for task in tasks), "match evidence coverage")
    evidence_by_task = {entry["task_id"]: entry for entry in evidence}
    attempts_by_task = defaultdict(list)
    for attempt in attempts:
        attempts_by_task[attempt["task_id"]].append(attempt)
    match_ids = [attempt["match_id"] for attempt in attempts if attempt["match_id"]]
    check(len(set(match_ids)) == len(tasks), "distinct match per task")
    check(Counter(entry["id"] for entry in rows(repo, "SELECT id FROM matches")) == Counter(match_ids),
          "dedicated study database contains missing or unaccounted matches")
    calls = rows(repo, """SELECT l.*,e.system_prompt,e.user_prompt,e.raw_output,e.provider_status,
        e.call_id AS evidence_id,e.transport_attempt FROM llm_call_logs l LEFT JOIN call_evidence e
        ON e.call_id=l.id WHERE l.run_id=? ORDER BY l.rowid""", (run_id,))
    check(Counter(entry["id"] for entry in rows(repo, "SELECT id FROM llm_call_logs"))
          == Counter(call["id"] for call in calls), "dedicated study database contains unaccounted calls")
    ledgers = rows(repo, "SELECT * FROM budget_ledger WHERE run_id=?", (run_id,))
    check(Counter(call["id"] for call in calls) == Counter(entry["call_id"] for entry in ledgers), "call/budget one-to-one linkage")
    issues.extend(budget_issues(repo, run_id, {"models": [model]}))
    reserved = _cost(model, protocol["max_input_tokens"], model["parameters"]["max_tokens"])
    accounted = Decimal(0)
    for entry in ledgers:
        check(abs(Decimal(str(entry["reserved_usd"])) - reserved) <= Decimal("1e-12"), "frozen reservation amount")
        accounted += Decimal(str(entry["actual_usd"] if entry["actual_usd"] is not None else entry["reserved_usd"]))
    check(len(calls) <= protocol["max_calls"] and len(ledgers) <= protocol["max_calls"], "physical call cap")
    check(accounted.is_finite() and 0 <= accounted <= Decimal(str(protocol["budget_limit_usd"])), "monetary cap")
    for call in calls:
        check(call["match_id"] in match_ids, f"call outside planned matches: {call['id']}")
        check(call["phase"] in ("bidding", "playing"), "unexpected reflection/summary/other call")
        check(call["evidence_id"] == call["id"] and call["transport_attempt"] == 1, "physical call evidence coverage")
        check(call["provider_status"] in ("returned", "error", "cancelled") and
              bool(call["success"]) == (call["provider_status"] == "returned"), f"provider return flag: {call['id']}")
        check((call["provider"], call["model"]) == (model["provider"], model["model"]), "frozen requested model")
        check(call["latency_ms"] is not None and math.isfinite(call["latency_ms"]) and call["latency_ms"] >= 0,
              f"provider latency: {call['id']}")
        for field, limit in (("prompt_tokens", protocol["max_input_tokens"]), ("completion_tokens", model["parameters"]["max_tokens"])):
            check(call[field] is None or (type(call[field]) is int and 0 <= call[field] <= limit), f"provider {field} allowance")
        if call["prompt_tokens"] is not None and call["completion_tokens"] is not None and call["total_tokens"] is not None:
            check(call["total_tokens"] == call["prompt_tokens"] + call["completion_tokens"], "provider total token consistency")
    run_decisions = rows(repo, "SELECT * FROM decisions WHERE run_id=?", (run_id,))
    run_events = repo.get_decision_events(run_id=run_id)
    all_evaluated, all_events, match_rows, consumed_calls, consumed_decisions, consumed_events = [], [], [], [], [], []
    participant_ids = []
    for task in tasks:
        prefix = f"{task['seed']}/{task['variant_id']}/{task['seat_rotation']}"
        def task_check(condition, message):
            check(condition, f"{prefix}: {message}")
        task_check(task["status"] == "finished", f"task status: {task['status']}")
        chain = attempts_by_task[task["id"]]
        if len(chain) != 1 or task["id"] not in evidence_by_task:
            continue
        attempt = chain[0]
        task_check(attempt["status"] == "finished" and attempt["attempt_index"] == 1, "attempt status/index")
        task_check(attempt["finished_at"] is not None and attempt["finished_at"] >= attempt["started_at"], "attempt timestamps")
        task_check(task["match_id"] == attempt["match_id"], "task/attempt match linkage")
        match = repo.get_match(attempt["match_id"])
        if not match:
            task_check(False, "missing match")
            continue
        task_check(len(rows(repo, "SELECT id FROM task_attempts WHERE match_id=?", (match["id"],))) == 1,
                   "match reused by another attempt")
        payload = json.loads(evidence_by_task[task["id"]]["payload_json"])
        task_check(payload["sha256"] == canonical_hash({key: value for key, value in payload.items() if key != "sha256"}), "match evidence hash")
        task_check(isinstance(payload["elapsed_ms"], (int, float)) and math.isfinite(payload["elapsed_ms"])
                   and payload["elapsed_ms"] >= 0, "match elapsed time")
        expected_config = MatchConfig(total_hands=protocol["total_hands"], max_tiebreaker_hands=0, ko_enabled=False,
            seed=task["seed"], enable_reflection=False, enable_summary=False, persist_long_term_memory=False,
            timeout_config=TimeoutConfig(bidding_seconds=protocol["call_timeout_seconds"],
                individual_play_seconds=protocol["call_timeout_seconds"], team_pool_seconds=protocol["task_timeout_seconds"],
                exhausted_individual_seconds=protocol["call_timeout_seconds"]))
        task_check(match["config"] == asdict(expected_config), "frozen match configuration")
        task_check(match["seed"] == task["seed"] and match["ko_result"] is None, "match seed/KO control")
        issues.extend(f"{prefix}: {issue}" for issue in audit_match(repo, match["id"], SimpleNamespace(
            total_hands=protocol["total_hands"], max_tiebreaker_hands=0)))
        hands = repo.get_hands_for_match(match["id"])
        task_check([hand["hand_num"] for hand in hands] == list(range(1, protocol["total_hands"] + 1)), "hand number coverage")
        for hand in hands:
            task_check(hand["seed"] == f"{task['seed']}/hand-{hand['hand_num']}" and
                       (hand["dealer"], hand["idle_seat"]) == get_dealer_idle(hand["hand_num"])
                       and not hand["is_tiebreaker"], "frozen deal seed/rotation")
        roster = _audit_roster(repo, plan, task, match, payload, task_check)
        participant_ids.extend(roster)
        # SQL ordering preserves each player's RNG stream through all hands.
        decisions = rows(repo, """SELECT d.*,th.\"table\" AS table_name,h.hand_num FROM decisions d
            LEFT JOIN table_hands th ON th.id=d.table_hand_id LEFT JOIN hands h ON h.id=th.hand_id
            WHERE d.match_id=? ORDER BY h.hand_num,CASE d.phase WHEN 'bidding' THEN 0 ELSE 1 END,d.action_seq,d.rowid""", (match["id"],))
        match_calls = rows(repo, "SELECT * FROM llm_call_logs WHERE match_id=?", (match["id"],))
        selected_calls = [call for call in calls if call["match_id"] == match["id"]]
        task_check(Counter(call["id"] for call in match_calls) == Counter(call["id"] for call in selected_calls), "match/run call linkage")
        evaluated = []
        calls_by_decision = defaultdict(list)
        for call in selected_calls:
            calls_by_decision[call["decision_id"]].append(call)
        for decision in decisions:
            entry = roster.get(decision["player_id"])
            task_check(entry is not None, "decision player outside roster")
            if entry is None:
                continue
            task_check((decision["seat"], decision["table_name"]) == (entry["seat"], entry["table"]), "decision seating linkage")
            task_check(decision["rules_version"] == RULES_VERSION, "decision rule version")
            task_check(decision["phase"] in ("bidding", "playing"), "unexpected decision phase")
            observation = json.loads(decision["observation_json"])
            task_check(observation["seat"] == entry["seat"], "observation seat linkage")
            task_check(decision["hand_num"] is not None, "decision table/hand linkage")
            task_check(all(observation.get(field) is None for field in ("remaining_hands", "initial_hand", "match_summary")),
                       "decision observation contains private learning/replay context")
            chain_calls = sorted(calls_by_decision[decision["decision_id"]], key=lambda call: call["attempt"])
            if entry["kind"] == "baseline":
                task_check(not chain_calls and decision["run_id"] is None and decision["variant_id"] is None
                           and decision["task_attempt_id"] is None, "baseline acquired evaluated model context")
                continue
            evaluated.append(decision)
            consumed_decisions.append(decision["decision_id"])
            task_check((decision["run_id"], decision["variant_id"], decision["task_attempt_id"])
                       == (run_id, task["variant_id"], attempt["id"]), "evaluated decision context")
            task_check(len(chain_calls) <= protocol["retry_limit"] + 1, "decision retry cap")
            for call in chain_calls:
                consumed_calls.append(call["id"])
                task_check((call["player_id"], call["variant_id"], call["phase"], call["table_hand_id"])
                           == (decision["player_id"], task["variant_id"], decision["phase"], decision["table_hand_id"]), "call/decision context")
                task_check((call["system_prompt"], call["user_prompt"]) == expected_prompt(
                    plan, task["variant_id"], entry["slot"], decision["phase"], observation, entry["table"], call["attempt"]),
                    f"frozen prompt/memory mismatch: {call['id']}")
                if isinstance(call["system_prompt"], str) and isinstance(call["user_prompt"], str):
                    task_check(len((call["system_prompt"] + call["user_prompt"]).encode()) + 1024 <= protocol["max_input_tokens"], "prompt input allowance")
        _audit_baseline(decisions, roster, task_check)
        ids = {decision["decision_id"] for decision in evaluated}
        events = repo.get_decision_events(match_id=match["id"])
        selected_events = [event for event in events if event["decision_id"] in ids]
        for event in selected_events:
            task_check(event["run_id"] == run_id and event["variant_id"] == task["variant_id"], "validation event run/arm linkage")
        consumed_events.extend(event["id"] for event in selected_events)
        all_evaluated.extend(evaluated)
        all_events.extend(selected_events)
        winner = "red" if match["score_red"] > match["score_blue"] else "blue" if match["score_blue"] > match["score_red"] else "tie"
        task_check(match["score_red"] >= 0 and match["score_blue"] >= 0, "duplicate team points must be nonnegative")
        settlement = rows(repo, "SELECT * FROM match_settlements WHERE match_id=?", (match["id"],))
        task_check(len(settlement) == 1 and settlement[0]["winner_team"] == winner and settlement[0]["source"] == "match", "match winner settlement")
        endings = [json.loads(event["event_json"]) for event in rows(repo, "SELECT event_json FROM match_events WHERE match_id=?", (match["id"],))]
        endings = [event["payload"] for event in endings if event.get("type") == "match_ended"]
        task_check(len(endings) == 1 and endings[0]["winner_team"] == winner
                   and endings[0]["final_score"] == {"red": match["score_red"], "blue": match["score_blue"]}
                   and endings[0]["total_hands_played"] == protocol["total_hands"]
                   and not endings[0]["ko_triggered"] and endings[0]["tiebreaker_hands"] == 0, "match ended event/score linkage")
        side = "red" if task["seat_rotation"] == 0 else "blue"
        score = match[f"score_{side}"]
        opponent_score = match["score_blue" if side == "red" else "score_red"]
        match_rows.append({"seed": task["seed"], "arm": task["variant_id"], "side": side, "match_id": match["id"],
                           "task_id": task["id"], "evaluated_score": score, "diff_score": score - opponent_score,
                           "opponent_score": opponent_score,
                           "winner": winner, "outcome": "tie" if winner == "tie" else "win" if winner == side else "loss",
                           "elapsed_ms": payload["elapsed_ms"], "hands": len(hands),
                           "metrics": _phase_metrics(evaluated, selected_calls, selected_events, ledgers)})
    check(len(participant_ids) == len(set(participant_ids)), "fresh independent player instances per match")
    check(Counter(consumed_calls) == Counter(call["id"] for call in calls), "every physical call consumed exactly once")
    check(Counter(consumed_decisions) == Counter(decision["decision_id"] for decision in run_decisions), "evaluated decision coverage")
    check(Counter(consumed_events) == Counter(event["id"] for event in run_events), "evaluated validation event coverage")
    arm_metrics = {arm: _phase_metrics([decision for decision in all_evaluated if decision["variant_id"] == arm],
                                     [call for call in calls if call["variant_id"] == arm],
                                     [event for event in all_events if event["variant_id"] == arm], ledgers) for arm in ARMS}
    return {"complete": not issues, "issues": sorted(set(issues)), "run_id": run_id, "plan_sha256": plan["sha256"],
            "physical_calls": len(calls), "accounted_usd": str(accounted), "planned_matches": len(expected_order),
            "finished_matches": len(match_rows), "arm_metrics": arm_metrics,
            "returned_models": sorted({call["response_model"] for call in calls if call["response_model"]}),
            "system_fingerprints": sorted({call["system_fingerprint"] for call in calls if call["system_fingerprint"]})}, match_rows, plan


def _outcomes(match_rows):
    counts = Counter(row["outcome"] for row in match_rows)
    return {"matches": len(match_rows), "wins": counts["win"], "ties": counts["tie"], "losses": counts["loss"],
            "win_rate": counts["win"] / len(match_rows) if match_rows else None,
            "mean_diff_score": mean(row["diff_score"] for row in match_rows) if match_rows else None,
            "known_cost_usd": str(sum((Decimal(row["metrics"]["all"]["known_cost_usd"]) for row in match_rows), Decimal(0))),
            "accounted_cost_usd": str(sum((Decimal(row["metrics"]["all"]["accounted_cost_usd"]) for row in match_rows), Decimal(0)))}


def export_memory_match_report(repo, run_id, output, *, root=None):
    audit, match_rows, plan = audit_memory_matches(repo, run_id, root=root)
    paired = []
    for seed in plan.get("test_seeds", []):
        per_arm = {arm: _outcomes([row for row in match_rows if row["seed"] == seed and row["arm"] == arm]) for arm in ARMS}
        entry = {"seed": seed, "arms": per_arm}
        if all(per_arm[arm]["matches"] == 2 for arm in ARMS):
            for control, label in (("fixed_initial", "initial"), ("no_memory", "none")):
                entry[f"frozen_minus_{label}_mean_diff_score"] = per_arm["frozen_memory"]["mean_diff_score"] - per_arm[control]["mean_diff_score"]
                entry[f"frozen_minus_{label}_win_rate"] = per_arm["frozen_memory"]["win_rate"] - per_arm[control]["win_rate"]
                entry[f"frozen_minus_{label}_accounted_cost_usd"] = str(Decimal(per_arm["frozen_memory"]["accounted_cost_usd"]) - Decimal(per_arm[control]["accounted_cost_usd"]))
        paired.append(entry)
    summary = {**{key: value for key, value in audit.items() if key != "arm_metrics"},
        "kind": "memory-full-matches-report-v1", "provenance": "mock" if plan.get("mock") else "real" if plan.get("mock") is False else "unknown",
        "memory_slot": plan.get("protocol", {}).get("memory_slot"),
        "memory_artifact_sha256": plan.get("memory_artifact", {}).get("artifact_sha256"),
        "arms": {arm: {**_outcomes([row for row in match_rows if row["arm"] == arm]),
                        "metrics": audit["arm_metrics"].get(arm, {})} for arm in ARMS},
        "paired_by_seed": paired,
        "limitations": [
            "This audit requires a dedicated experiment database containing exactly one run and no unaccounted matches or model calls.",
            "Fixed seeded RandomAgent baseline only; results do not establish strength against human or competitive agents.",
            "Small-sample descriptive results; seeds are paired clusters and decisions are not independent samples.",
            "Mock results verify the pipeline only; they do not show learning gains." if plan.get("mock") else
            "Real provider results remain descriptive; a gain is not a causal or statistical-significance claim.",
            "Each arm uses a fresh four-player team and plays both colors; all four evaluated players receive the same predeclared memory slot.",
            "Memory is frozen during test matches; reflection, summary and persistent learning are disabled.",
            "Team scores accumulate nonnegative duplicate points; diff_score is evaluated_score minus opponent_score after color rotation.",
            "Win rate counts wins divided by all matches, with draws retained in the denominator.",
            "Reliability, retries, fallback/autoplay, usage, latency and cost cover evaluated players only; baseline actions are excluded.",
            "Unknown usage retains reserved cost; known token and cost totals are lower bounds when coverage is incomplete.",
            "Source and artifact hashes detect drift; self-declared real provenance is not cryptographic provider attestation.",
            "Incomplete audits invalidate study conclusions; retained rows are diagnostic evidence only.",
        ]}
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("summary.json", summary), ("matches.json", match_rows)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "paired-seed.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        columns = ("matches", "wins", "ties", "losses", "win_rate", "mean_diff_score", "known_cost_usd", "accounted_cost_usd")
        writer.writerow(["seed", "arm", *columns])
        for entry in paired:
            for arm, values in entry["arms"].items():
                writer.writerow([entry["seed"], arm, *[values[column] for column in columns]])
    lines = ["# 完整对局记忆对照评测", "", f"来源：{summary['provenance']}；审计通过：{summary['complete']}；物理调用：{summary['physical_calls']}。", "",
             "每个 seed 的三个记忆组分别执红、执蓝，对手是固定随机基线。小样本结果仅作描述性比较；mock 不证明学习收益。", "",
             "| 组别 | 比赛数 | 胜 / 平 / 负 | 胜率 | 平均差分分 | 预算占用 USD |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        values = summary["arms"][arm]
        if values["matches"]:
            lines.append(f"| {arm} | {values['matches']} | {values['wins']} / {values['ties']} / {values['losses']} | {values['win_rate']:.1%} | {values['mean_diff_score']:.3f} | {values['accounted_cost_usd']} |")
    lines += ["", "胜率分母包含平局；差分分为被评测队伍累计差分积分减去对手累计差分积分，已按执红/执蓝方向统一。", "",
              "各组合法率、重试、实际兜底/托管、token、时延、费用详见 summary.json 的 metrics；只统计被评测玩家。", "",
              "逐 seed 配对结果见 paired-seed.csv，差值见 summary.json 的 paired_by_seed。", "",
              *summary["limitations"], "", "审计问题：", *(summary["issues"] or ["无。"])]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
