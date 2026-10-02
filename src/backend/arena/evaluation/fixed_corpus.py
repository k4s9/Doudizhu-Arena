"""Deterministic, model-independent legal trajectories for a held-out benchmark.

The sampling distribution is a rule-policy fixture, not human play or a model
ranking benchmark. No provider output is used to select an observation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
import random

from ..agent.memory import MemoryManager
from ..agent.prompts.bidding import build_bidding_prompt
from ..agent.prompts.playing import build_playing_prompt
from ..engine.card import SEATS, Card
from ..engine.deck import Deck
from ..engine.projection import RULES_VERSION, digest, project
from ..engine.rules import InvalidPlayError, can_beat, recognize
from ..engine.state import GameEngine, TablePhase
from ..tournament.table import TableRunner
from .observations import validate_output

VERSION = "independent-rule-trajectories-v1"
TEAMS = {"S": "red", "N": "red", "E": "blue", "W": "blue"}
QUOTAS = {"bidding": 1, "lead": 3, "follow": 4}


def candidates(cards, target=None):
    """A finite rule policy including compound plays; not a complete solver."""
    groups = {}
    for card in cards:
        groups.setdefault(card.rank.value, []).append(card)
    plays = [[c] for c in cards]
    for group in groups.values():
        plays.extend(group[:n] for n in (2, 3, 4) if len(group) >= n)
        if len(group) >= 3:
            for other in groups.values():
                if other[0].rank != group[0].rank:
                    plays.append(group[:3] + other[:1])
                    if len(other) >= 2:
                        plays.append(group[:3] + other[:2])
    for multiplicity, minimum in ((1, 5), (2, 3), (3, 2)):
        for start in range(3, 15):
            run = []
            for end in range(start, 15):
                if len(groups.get(end, [])) < multiplicity:
                    break
                run += groups[end][:multiplicity]
                if end - start + 1 >= minimum:
                    plays.append(list(run))
    jokers = [c for c in cards if c.rank.value >= 16]
    if len(jokers) == 2:
        plays.append(jokers)
    valid = {}
    for play in plays:
        try:
            trick = recognize(play)
        except InvalidPlayError:
            continue
        if target is None or can_beat(trick, target):
            key = tuple(sorted(str(c) for c in play))
            valid[key] = play
    return [valid[key] for key in sorted(valid)]


def snapshot(ctx, seed, phase, index):
    observation = json.loads(json.dumps(asdict(ctx), default=str, ensure_ascii=False))
    observation["hand_cards"] = [str(c) for c in ctx.hand_cards]
    memory = MemoryManager("benchmark-player")
    if phase == "bidding":
        system, user = build_bidding_prompt(ctx, memory, "benchmark-player")
    else:
        system, user = build_playing_prompt(ctx, memory, "benchmark-player", seat_teams=TEAMS)
    return {
        "observation_id": digest({"seed": seed, "phase": phase, "index": index}),
        "source_seed": seed, "source_match": f"rule-policy/{seed}",
        "source_index": index, "phase": phase,
        "category": "bidding" if phase == "bidding" else ("lead" if ctx.current_trick is None else "follow"),
        "observation": observation, "system_prompt": system, "user_prompt": user,
        "target_pattern": ctx.current_trick.pattern.value if ctx.current_trick else None,
    }


def trajectory(seed):
    """Use production context builders and engine; archive legal source actions."""
    rng = random.Random(f"{VERSION}/policy/{seed}")
    table = TableRunner("A", {}, TEAMS, enable_reflection=False)
    state = table._state
    state.seat_teams = dict(TEAMS)
    GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W", deal_result=Deck(seed).deal())
    GameEngine.start_bidding(state)
    observations, actions = [], []
    while not state.landlord:
        seat = state.bidding_order[state.current_bidder_idx]
        ctx = table._build_bidding_context(seat)
        observations.append(snapshot(ctx, seed, "bidding", len(state.bidding_history)))
        options = [n for n in (0, 1, 2) if n == 0 or n > state.current_high_bid]
        # Guarantee a playing trajectory, independent of model outcomes.
        if state.current_bidder_idx == 2 and state.current_high_bid == 0:
            options = [1, 2, 3]
        bid = rng.choice(options)
        GameEngine.submit_bid(state, seat, bid, 0)
        actions.append({"phase": "bidding", "seat": seat, "bid": bid})
    GameEngine.finalize_bidding(state)
    GameEngine.start_playing(state)
    while state.phase == TablePhase.PLAYING:
        if state.global_seq >= 1000:
            raise ValueError("rule trajectory exceeded its action bound")
        seat = state.current_player
        ctx = table._build_play_context(seat)
        observations.append(snapshot(ctx, seed, "playing", state.global_seq))
        choices = candidates(ctx.hand_cards, ctx.current_trick)
        if ctx.current_trick is not None:
            choices.append([])
        play = rng.choice(choices)
        raw = json.dumps({"action": {"type": "play", "cards": [str(c) for c in play]}} if play
                         else {"action": {"type": "pass"}})
        validate_output(observations[-1], raw)
        GameEngine.submit_play(state, seat, play, 0)
        actions.append({"phase": "playing", "seat": seat, "cards": [str(c) for c in play]})
    return observations, {"source_seed": seed, "actions": actions, "terminal_state_sha256": digest(project(state))}


def build_corpus(development_seeds, test_seeds):
    if not development_seeds or not test_seeds or set(development_seeds) & set(test_seeds):
        raise ValueError("nonempty disjoint development/test seeds required")
    if len(set(development_seeds + test_seeds)) != len(development_seeds + test_seeds):
        raise ValueError("duplicate seed")
    items, sources = [], []
    for split, seeds in (("development", development_seeds), ("test", test_seeds)):
        for seed in seeds:
            pool, source = trajectory(seed)
            source["split"] = split
            sources.append(source)
            rng = random.Random(f"{VERSION}/sample/{seed}")
            for category, count in QUOTAS.items():
                eligible = [item for item in pool if item["category"] == category]
                if len(eligible) < count:
                    raise ValueError(f"{seed}: insufficient {category} states; do not silently replace a seed")
                # One sample per temporal bin: cover early through late play.
                for index in range(count):
                    lo, hi = index * len(eligible) // count, (index + 1) * len(eligible) // count
                    item = dict(rng.choice(eligible[lo:hi]))
                    item["split"] = split
                    item["temporal_bin"] = index
                    items.append(item)
    corpus = {
        "version": VERSION, "rules_version": RULES_VERSION,
        "provenance": "deterministic legal rule-policy trajectories; no LLM sampling or error injection",
        "sampling": {"quotas_per_seed": QUOTAS, "method": "uniform within temporal bins per category",
                     "policy": "seeded uniform legal candidate, forced nonvoid bidding; finite compound-play candidates"},
        "development_seeds": development_seeds, "test_seeds": test_seeds,
        "observations": items, "sources": sources,
    }
    corpus["sha256"] = digest(corpus)
    return corpus


def verify_corpus(corpus, *, replay=True):
    if corpus.get("sha256") != digest({k: v for k, v in corpus.items() if k != "sha256"}):
        raise ValueError("corpus hash mismatch")
    if corpus.get("version") != VERSION or corpus.get("rules_version") != RULES_VERSION:
        raise ValueError("unsupported corpus/rules version")
    ids = [item["observation_id"] for item in corpus["observations"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate observation")
    dev, test = corpus["development_seeds"], corpus["test_seeds"]
    if set(dev) & set(test):
        raise ValueError("source leakage")
    for seed in dev + test:
        items = [item for item in corpus["observations"] if item["source_seed"] == seed]
        if Counter(item["category"] for item in items) != Counter(QUOTAS):
            raise ValueError("category quota mismatch")
        expected = "development" if seed in dev else "test"
        if any(item["split"] != expected for item in items):
            raise ValueError("source split mismatch")
        if any(item["observation"][key] is not None for item in items
               for key in ("remaining_hands", "initial_hand", "match_summary")):
            raise ValueError("private/post-game state in observation")
    if replay:
        rebuilt = build_corpus(dev, test)
        if rebuilt != corpus:
            raise ValueError("source replay or frozen prompt mismatch")
    return {"complete": True, "sources": len(dev) + len(test), "observations": len(ids),
            "test_observations": sum(item["split"] == "test" for item in corpus["observations"])}
