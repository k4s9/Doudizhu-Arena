"""Seat rotation table and random seating assignment for duplicate bridge format.

A table: 南北=red team, 东西=blue team
B table: 南北=blue team, 东西=red team (mirror)

Dealer/idle rotation (cycle of 4):
  Hand 1: dealer=S, idle=W
  Hand 2: dealer=E, idle=S
  Hand 3: dealer=N, idle=E
  Hand 4: dealer=W, idle=N
  Hand 5+: repeats
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..engine.card import SEATS

# Pre-computed rotation table: (dealer, idle) for each hand_num (1-indexed, cycle of 4)
_ROTATION_TABLE: list[tuple[str, str]] = [
    ("S", "W"),  # hand 1
    ("E", "S"),  # hand 2
    ("N", "E"),  # hand 3
    ("W", "N"),  # hand 4
]


def get_dealer_idle(hand_num: int) -> tuple[str, str]:
    """Return (dealer_seat, idle_seat) for the given hand number (1-indexed)."""
    idx = (hand_num - 1) % 4
    return _ROTATION_TABLE[idx]


def get_bidding_order(dealer: str) -> list[str]:
    """Return [dealer, second, third] based on dealer seat, skipping the idle."""
    # idle is implicit — the caller knows which seat is idle from rotation
    # bidding order excludes idle (handled by GameEngine)
    idx = SEATS.index(dealer)
    order = []
    for i in range(4):
        seat = SEATS[(idx + i) % 4]
        order.append(seat)
    # idle is not known here — caller will filter
    return order


@dataclass(frozen=True, slots=True)
class MatchSeating:
    """Pre-computed seating assignment for a full match."""

    table_a: dict[str, str]  # seat → agent_id
    table_b: dict[str, str]  # seat → agent_id
    table_a_teams: dict[str, str]  # seat → 'red' | 'blue'
    table_b_teams: dict[str, str]  # seat → 'red' | 'blue'

    # Reverse lookup: agent_id → (table, seat)
    agent_seats: dict[str, tuple[str, str]]
    # agent_id → team
    agent_teams: dict[str, str]


def assign_seating(
    red_agent_ids: list[str],
    blue_agent_ids: list[str],
    seed: int | None = None,
) -> MatchSeating:
    """Randomly assign 4 red + 4 blue agents to A/B table seats.

    A table: S=red, E=blue, N=red, W=blue
    B table: S=blue, E=red, N=blue, W=red

    Within each team, the 4 agents are shuffled and placed 2 per table.
    """
    rng = random.Random(seed)

    red = list(red_agent_ids)
    blue = list(blue_agent_ids)
    rng.shuffle(red)
    rng.shuffle(blue)

    if len(red) != 4 or len(blue) != 4:
        raise ValueError("Must provide exactly 4 red and 4 blue agent IDs")

    # A table: S=red[0], E=blue[0], N=red[1], W=blue[1]
    table_a = {
        "S": red[0],
        "E": blue[0],
        "N": red[1],
        "W": blue[1],
    }
    table_a_teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

    # B table: S=blue[2], E=red[2], N=blue[3], W=red[3]
    table_b = {
        "S": blue[2],
        "E": red[2],
        "N": blue[3],
        "W": red[3],
    }
    table_b_teams = {"S": "blue", "E": "red", "N": "blue", "W": "red"}

    agent_seats: dict[str, tuple[str, str]] = {}
    agent_teams: dict[str, str] = {}
    for seat, aid in table_a.items():
        agent_seats[aid] = ("A", seat)
        agent_teams[aid] = table_a_teams[seat]
    for seat, aid in table_b.items():
        agent_seats[aid] = ("B", seat)
        agent_teams[aid] = table_b_teams[seat]

    return MatchSeating(
        table_a=table_a,
        table_b=table_b,
        table_a_teams=table_a_teams,
        table_b_teams=table_b_teams,
        agent_seats=agent_seats,
        agent_teams=agent_teams,
    )
