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
OBSERVATION_FIELDS = {
    "seat", "role", "hand_cards", "hand_size", "dizhu_cards", "bidding_history", "play_history",
    "current_trick", "trick_leader", "landlord", "active_players", "player_hand_sizes",
    "current_high_bid", "current_high_bidder", "hand_score", "winner_team", "winner_role",
    "hand_num", "match_id", "initial_hand", "remaining_hands", "agent_role", "is_idle_observer",
    "match_summary",
}
POST_GAME_DEFAULTS = {
    "hand_score": None, "winner_team": "", "winner_role": "", "hand_num": 0, "match_id": "",
    "initial_hand": None, "remaining_hands": None, "agent_role": "", "is_idle_observer": False,
    "match_summary": None,
}


def _validate_seeds(development_seeds, test_seeds):
    if any(not isinstance(seeds, list) or not seeds or
           any(not isinstance(seed, str) or not seed for seed in seeds)
           for seeds in (development_seeds, test_seeds)):
        raise ValueError("nonempty development/test seed lists required")
    if set(development_seeds) & set(test_seeds):
        raise ValueError("source leakage: development/test seeds overlap")
    if len(set(development_seeds + test_seeds)) != len(development_seeds + test_seeds):
        raise ValueError("duplicate seed")


def _verify_observation(item):
    """Reject hidden/future fields even when callers skip expensive source replay.

    The v1 archive contains engine card identities for already played cards.
    These are replay evidence; providers receive only the frozen prompt strings.
    This check does not silently change that historical visibility convention.
    """
    obs = item["observation"]
    if set(obs) != OBSERVATION_FIELDS:
        raise ValueError("unexpected/missing observation fields")
    if any(obs[key] != default or type(obs[key]) is not type(default)
           for key, default in POST_GAME_DEFAULTS.items()):
        raise ValueError("private/post-game state in observation")
    if obs["seat"] not in SEATS or type(obs["hand_size"]) is not int:
        raise ValueError("invalid observation seat/hand size")
    cards = [Card.from_string(card) for card in obs["hand_cards"]]
    if len(cards) != obs["hand_size"] or not 1 <= len(cards) <= 20 or len(set(cards)) != len(cards):
        raise ValueError("invalid observation hand")
    phase, index = item["phase"], item["source_index"]
    if phase not in ("bidding", "playing") or type(index) is not int or index < 0:
        raise ValueError("invalid observation phase/source index")
    category = "bidding" if phase == "bidding" else ("lead" if obs["current_trick"] is None else "follow")
    if item["category"] != category:
        raise ValueError("category does not match observation")
    expected_id = digest({"seed": item["source_seed"], "phase": phase, "index": index})
    if item["observation_id"] != expected_id:
        raise ValueError("observation/source identity mismatch")
    if any(not isinstance(item[key], str) or not item[key] for key in ("system_prompt", "user_prompt")):
        raise ValueError("missing frozen prompt")
    bids, plays = obs["bidding_history"], obs["play_history"]
    if not isinstance(bids, list) or not isinstance(plays, list):
        raise ValueError("invalid public history")
    high_bid = 0
    for bid in bids:
        if set(bid) != {"seat", "bid"} or bid["seat"] not in SEATS or type(bid["bid"]) is not int:
            raise ValueError("unexpected/invalid bidding history fields")
        if bid["bid"] not in range(4) or (bid["bid"] and bid["bid"] <= high_bid):
            raise ValueError("invalid public bid history")
        high_bid = max(high_bid, bid["bid"])
    for seq, play in enumerate(plays, 1):
        if set(play) != {"round", "sub_round", "seq", "seat", "action_type", "cards", "trick_display"}:
            raise ValueError("unexpected/missing play history fields")
        if play["seq"] != seq or play["seat"] not in SEATS or play["action_type"] not in ("play", "pass"):
            raise ValueError("invalid public play history")
    if phase == "bidding":
        if (index != len(bids) or index >= 3 or plays or obs["dizhu_cards"] is not None
                or obs["current_trick"] is not None or obs["landlord"] or obs["role"] != "bidding"
                or obs["current_high_bid"] != high_bid):
            raise ValueError("future/invalid state in bidding observation")
    elif index != len(plays) or obs["role"] not in ("landlord", "farmer"):
        raise ValueError("future/invalid state in playing observation")
    pattern = obs["current_trick"]["pattern"] if obs["current_trick"] else None
    if item["target_pattern"] != pattern:
        raise ValueError("target pattern mismatch")


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
    _validate_seeds(development_seeds, test_seeds)
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
    """Check membership/visibility before optional deterministic source replay."""
    try:
        return _verify_corpus(corpus, replay=replay)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"malformed fixed corpus: {exc}") from exc


def _verify_corpus(corpus, *, replay):
    if corpus.get("sha256") != digest({k: v for k, v in corpus.items() if k != "sha256"}):
        raise ValueError("corpus hash mismatch")
    if corpus.get("version") != VERSION or corpus.get("rules_version") != RULES_VERSION:
        raise ValueError("unsupported corpus/rules version")
    ids = [item["observation_id"] for item in corpus["observations"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate observation")
    dev, test = corpus["development_seeds"], corpus["test_seeds"]
    _validate_seeds(dev, test)
    expected_splits = {seed: split for split, seeds in (("development", dev), ("test", test)) for seed in seeds}
    if Counter(source["source_seed"] for source in corpus["sources"]) != Counter(expected_splits.keys()):
        raise ValueError("source trajectory coverage mismatch")
    for source in corpus["sources"]:
        if source["split"] != expected_splits[source["source_seed"]]:
            raise ValueError("source trajectory split mismatch")
    for item in corpus["observations"]:
        seed = item["source_seed"]
        if seed not in expected_splits or item["split"] != expected_splits[seed]:
            raise ValueError("unexpected source/split in observation")
        if item["source_match"] != f"rule-policy/{seed}":
            raise ValueError("source match identity/leakage mismatch")
        _verify_observation(item)
    for seed in dev + test:
        items = [item for item in corpus["observations"] if item["source_seed"] == seed]
        if Counter(item["category"] for item in items) != Counter(QUOTAS):
            raise ValueError("category quota mismatch")
        expected = "development" if seed in dev else "test"
        if any(item["split"] != expected for item in items):
            raise ValueError("source split mismatch")
        for category, count in QUOTAS.items():
            bins = [item["temporal_bin"] for item in items if item["category"] == category]
            if any(type(index) is not int for index in bins) or sorted(bins) != list(range(count)):
                raise ValueError("temporal bin coverage mismatch")
    if replay:
        rebuilt = build_corpus(dev, test)
        if rebuilt != corpus:
            raise ValueError("source replay or frozen prompt mismatch")
    return {"complete": True, "sources": len(dev) + len(test), "observations": len(ids),
            "test_observations": sum(item["split"] == "test" for item in corpus["observations"])}
