"""Reproducible, offline benchmarks of the two pure agent algorithms.

Run from the repository with the project's environment (no LLM calls):
  conda run -n doudizhu-arena python -I -B scripts/benchmark_agent_tools.py \
    --output docs/reviews/20261003-agent-tools/benchmark.json

Only fixture generation uses full simulated hands. The measured functions
receive their normal AgentContext and explicit arguments, never TableState.
Forced unknown cases are reported separately from normal-budget workloads.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import socket
import statistics
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# -I excludes cwd; explicitly add only this repository's backend package.
sys.path.insert(0, str(ROOT / "src/backend"))
SEED = "agent-tools-20261003-v1"
SOURCES = ["scripts/benchmark_agent_tools.py", "src/backend/arena/agent/tools/plays.py",
           "src/backend/arena/agent/tools/plans.py", "src/backend/arena/agent/tools/threats.py",
           "src/backend/arena/engine/rules.py"]


@contextmanager
def offline_guard():
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Network access is disabled in the agent-tool benchmark")

    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket, "create_connection", deny),
        patch.object(socket, "getaddrinfo", deny),
    ):
        yield attempts


@dataclass
class Case:
    context: object
    arguments: dict
    forced_node_limit: int | None = None


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def observable_input(case):
    ctx = case.context
    return {
        "seat": ctx.seat, "role": ctx.role, "hand": [str(c) for c in ctx.hand_cards],
        "hand_size": ctx.hand_size, "landlord": ctx.landlord,
        "active_players": ctx.active_players, "hand_sizes": ctx.player_hand_sizes,
        "bottom": None if ctx.dizhu_cards is None else [str(c) for c in ctx.dizhu_cards],
        "history": ctx.play_history,
        "current_trick": None if ctx.current_trick is None else {
            "ranks": [c.rank_only for c in ctx.current_trick.all_cards],
            "pattern": ctx.current_trick.pattern.value,
        },
        "arguments": case.arguments, "forced_node_limit": case.forced_node_limit,
    }


def distribution(values):
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
        "max": round(ordered[-1], 3),
    }


def comparison_cases(samples):
    from arena.agent.base import AgentContext
    from arena.agent.tools.plays import cards_from_counts
    from arena.engine.card import ALL_CARDS

    result = {}
    for category in ("random_10", "random_17", "random_20", "airplane_pressure",
                     "pairs_pressure", "bombs_pressure", "preserve_bombs"):
        cases = []
        for i in range(samples):
            rng = random.Random(f"{SEED}/{category}/{i}")
            if category.startswith("random_"):
                hand = rng.sample(ALL_CARDS, int(category.split("_")[1]))
            else:
                counts = [0] * 15
                if category == "airplane_pressure":
                    start = rng.randrange(5)
                    counts[start:start + 4] = [3] * 4
                    counts[start + 4:start + 8] = [2] * 4
                elif category == "pairs_pressure":
                    start = rng.randrange(3)
                    counts[start:start + 10] = [2] * 10
                else:
                    start = rng.randrange(8)
                    counts[start:start + 4] = [4] * 4
                    counts[start + 4] = 2
                    counts[13:] = [1, 1]
                hand = cards_from_counts(tuple(counts))
                rng.shuffle(hand)
            own = AgentContext(seat="S", role="landlord", hand_cards=hand,
                               hand_size=len(hand), landlord="S")
            if category == "preserve_bombs":
                ranks = sorted({card.rank for card in hand if not card.is_joker
                                and sum(c.rank == card.rank for c in hand) == 4})
                actions = [{"cards": [str(c) for c in hand if c.rank == rank]}
                           for rank in ranks[:3]]
                args = {"actions": actions, "constraints": {"preserve_bombs": True}}
            else:
                # Different ranks avoid benchmarking the same suit-equivalent
                # remainder three times; every candidate is a legal single.
                unique = list(dict.fromkeys(card.rank for card in hand))
                chosen = rng.sample(unique, 3)
                args = {"actions": [{"cards": [str(next(c for c in hand if c.rank == rank))]}
                                    for rank in chosen]}
            cases.append(Case(own, args))
        result[f"compare/{category}"] = cases
    return result


def snapshot(state):
    from arena.agent.base import AgentContext

    seat = state.current_player
    return AgentContext(
        seat=seat, role=state.role_of(seat),
        hand_cards=list(state.live_hands[seat].cards), hand_size=state.live_hands[seat].size,
        landlord=state.landlord, dizhu_cards=state.dizhu_cards,
        active_players=list(state.active_order),
        player_hand_sizes={s: h.size for s, h in state.live_hands.items()},
        current_trick=state.current_trick, trick_leader=state.trick_leader,
        # Sanitize other players before the benchmark boundary, as well as
        # the tool's own rank-only projection. No reflection-only fields.
        play_history=[{
            "seat": record.seat, "action_type": record.action_type,
            "cards": [str(c) if record.seat == seat else c.rank_only
                      for c in (record.cards or ())],
        } for record in state.play_history],
    )


def initial_state(seed, *, own_rocket=False):
    from arena.engine.deck import Deck
    from arena.engine.state import GameEngine, TableState

    deck = Deck(seed)
    if own_rocket:
        # Keep a valid full deck, but give the future landlord both jokers.
        for destination, rank in ((17, 16), (18, 17)):
            origin = next(i for i, card in enumerate(deck.cards) if card.rank.value == rank)
            deck.cards[destination], deck.cards[origin] = deck.cards[origin], deck.cards[destination]
    state = TableState(table="A", seat_teams={"S": "red", "E": "blue", "N": "red", "W": "blue"})
    GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W", deal_result=deck.deal())
    GameEngine.start_bidding(state)
    GameEngine.submit_bid(state, "S", 0, 0)
    GameEngine.submit_bid(state, "E", 3, 0)
    GameEngine.finalize_bidding(state)
    GameEngine.start_playing(state)
    return state


def threat_cases(samples):
    from arena.engine.rules import can_beat, recognize
    from arena.engine.state import GameEngine, TablePhase

    result = {f"threat/{stage}/{event}": []
              for stage in ("opening", "middle", "late")
              for event in ("can_finish", "can_beat")}
    result.update({f"threat/{stage}/can_finish_free_lead": []
                   for stage in ("opening", "middle", "late")})
    result.update({"threat/rocket_ruled_out": [], "threat/incomplete_history_unknown": [],
                   "threat/zero_budget_unknown": []})
    for i in range(samples):
        state = initial_state(f"{SEED}/game/{i}")
        saved = {}
        played_cards = 0
        latest = None
        latest_free = None
        while state.phase == TablePhase.PLAYING:
            latest = snapshot(state)
            if state.current_trick is None:
                latest_free = latest
            for stage, threshold in (("opening", 1), ("middle", 18), ("late", 34)):
                if stage not in saved and played_cards >= threshold:
                    saved[stage] = latest
                if (f"{stage}_free" not in saved and played_cards >= threshold
                        and state.current_trick is None):
                    saved[f"{stage}_free"] = latest
            if len(saved) == 6:
                break
            # A deterministic legal policy is enough to create representative
            # public histories. Its quality is not measured by this benchmark.
            hand = state.live_hands[state.current_player].cards
            options = [card for card in hand if can_beat(recognize([card]), state.current_trick)]
            selected = [min(options, key=lambda card: card.rank.value)] if options else []
            GameEngine.submit_play(state, state.current_player, selected, 0)
            played_cards += len(selected)
        for stage in ("opening", "middle", "late"):
            ctx = saved.get(stage, latest)
            target = ctx.active_players[(ctx.active_players.index(ctx.seat) + 1) % 3]
            for event in ("can_finish", "can_beat"):
                args = {"event": event, "target_seat": target}
                if event == "can_beat" and ctx.current_trick is None:
                    args["cards"] = [str(ctx.hand_cards[0])]
                result[f"threat/{stage}/{event}"].append(Case(ctx, args))
            free = saved.get(f"{stage}_free", latest_free)
            target = free.active_players[(free.active_players.index(free.seat) + 1) % 3]
            result[f"threat/{stage}/can_finish_free_lead"].append(
                Case(free, {"event": "can_finish", "target_seat": target}))
        ctx = saved.get("middle", latest)
        target = next(seat for seat in ctx.active_players if seat != ctx.seat)
        incomplete = replace(ctx, play_history=ctx.play_history[1:])
        result["threat/incomplete_history_unknown"].append(
            Case(incomplete, {"event": "can_finish", "target_seat": target}))
        # A valid opening sample with a known single target has candidates, so
        # zero nodes reliably exercises the honest unknown fallback.
        ctx = saved["opening"]
        target = next(seat for seat in ctx.active_players if seat != ctx.seat)
        result["threat/zero_budget_unknown"].append(
            Case(ctx, {"event": "can_beat", "target_seat": target}, forced_node_limit=0))
        ctx = snapshot(initial_state(f"{SEED}/rocket/{i}", own_rocket=True))
        result["threat/rocket_ruled_out"].append(Case(ctx, {
            "event": "can_beat", "target_seat": "S",
            "cards": [str(card) for card in ctx.hand_cards if card.is_joker],
        }))
    return result


def measure(category, cases):
    from arena.agent.tools.plans import compare_hand_plans
    from arena.agent.tools import threats

    algorithm = compare_hand_plans if category.startswith("compare/") else threats.analyze_threat

    def invoke(case):
        if case.forced_node_limit is None:
            return algorithm(case.context, case.arguments)
        with patch.object(threats, "MAX_SEARCH_NODES", case.forced_node_limit):
            return algorithm(case.context, case.arguments)

    for case in cases[:2]:
        invoke(case)
    times, nodes = [], []
    outcomes, reasons = Counter(), Counter()
    for case in cases:
        started = perf_counter()
        value = invoke(case)
        times.append((perf_counter() - started) * 1000)
        if category.startswith("compare/"):
            plans = value["plans"]
            if not all(plan["valid"] for plan in plans):
                raise AssertionError("Benchmark produced an invalid comparison candidate")
            outcomes["all_candidates_exact" if all(plan["exact"] for plan in plans)
                     else "some_candidates_bounded"] += 1
            outcomes["exact_candidates"] += sum(plan["exact"] for plan in plans)
            outcomes["bounded_candidates"] += sum(not plan["exact"] for plan in plans)
            stats = value["stats"]
            reasons[stats["stop_reason"] or "completed"] += 1
        else:
            outcomes[value["status"]] += 1
            reasons[value["reason"]] += 1
            stats = value["search"]
            if "incomplete_history" in category and value["status"] != "unknown":
                raise AssertionError("Missing history must return unknown")
            if "zero_budget" in category and value["status"] != "unknown":
                raise AssertionError("Zero budget must return unknown")
            if "rocket_ruled_out" in category and value["status"] != "ruled_out":
                raise AssertionError("A second rocket cannot exist in the unseen pool")
        nodes.append(stats["nodes"])
    inputs = [observable_input(case) for case in cases]
    played_counts = [sum(len(record["cards"]) for record in c.context.play_history)
                     for c in cases]
    return {
        "calls": len(cases), "warmup_calls_excluded": min(2, len(cases)),
        "latency_ms": distribution(times), "nodes": distribution(nodes),
        "outcomes": dict(outcomes), "stop_reasons": dict(reasons),
        "input_sha256": digest(inputs),
        "individual_input_sha256": [digest(value) for value in inputs],
        "own_hand_size": {"min": min(c.context.hand_size for c in cases),
                          "max": max(c.context.hand_size for c in cases)},
        "public_played_card_count": {"min": min(played_counts), "max": max(played_counts)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100, help="at least 50 calls per scenario")
    parser.add_argument("--output", type=Path, help="write JSON here; otherwise print JSON")
    args = parser.parse_args()
    if args.samples < 50:
        parser.error("--samples must be at least 50")
    source_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}
    with offline_guard() as network_attempts:
        categories = comparison_cases(args.samples) | threat_cases(args.samples)
        results = {name: measure(name, cases) for name, cases in categories.items()}
    if network_attempts:
        raise RuntimeError(f"Benchmark attempted {len(network_attempts)} network operations")
    if source_hashes != {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}:
        raise RuntimeError("Measured source files changed during this run; rerun for a coherent report")
    report = {
        "schema_version": 1, "seed": SEED, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "measurement": "Direct pure-algorithm wall time; excludes fixture generation, imports, LLM and registry/provider integration.",
        "notes": [
            "P95 uses the nearest-rank definition; two warmups per scenario are excluded.",
            "Inputs and seed are deterministic; timing and time-limited exactness vary by machine and system load.",
            "Normal budgets are cooperative deadlines, not hard real-time guarantees.",
            "Threat game fixtures use the actual engine and a legal single-card policy; they do not estimate strategic strength.",
            "Opening/middle/late snapshots target 1/18/34 played cards; if the game ends sooner, use the last eligible live snapshot.",
            "Free-lead cases are actual engine states with no current trick, not edited history or removed public targets.",
            "Zero-budget and missing-history unknown cases are intentional fault cases, not normal workloads.",
            "There are no LLM calls or secrets reads, and this is not a same-task comparison against LLM reasoning.",
        ],
        "environment": {"python": sys.version, "python_executable": sys.executable,
                        "implementation": platform.python_implementation(), "platform": platform.platform(),
                        "machine": platform.machine(), "processor": platform.processor(),
                        "logical_cpu_count": os.cpu_count()},
        "network_guard": {"connection_and_dns_attempts": len(network_attempts),
                          "blocked": ["socket.connect", "socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"]},
        "source_sha256": source_hashes,
        "total_measured_calls": sum(result["calls"] for result in results.values()),
        "all_inputs_sha256": digest({name: result["input_sha256"] for name, result in results.items()}),
        "scenarios": results,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(output, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {args.output}; {report['total_measured_calls']} measured calls; 0 network attempts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
