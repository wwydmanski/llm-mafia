from __future__ import annotations

from dataclasses import dataclass
import os
import random
from typing import Dict, List


ROLES = ("mafia", "detective", "doctor", "villager")


@dataclass
class Player:
    name: str
    role: str
    alive: bool = True
    is_human: bool = False


@dataclass
class GameState:
    round: int
    phase: str  # "day" or "night"
    players: Dict[str, Player]  # key by name


def assign_roles(agent_names: List[str], human_name: str | None = None, seed: int | None = None) -> Dict[str, Player]:
    """Assign roles randomly with an optional seed for reproducibility.

    Counts: 2 mafia, 1 detective, 1 doctor, rest villagers. The human, if present,
    is included in this randomization and may be assigned any role.
    If GAME_ROLE_SEED env var is set and seed is None, it will be used.
    """
    if seed is None:
        env_seed = os.getenv("GAME_ROLE_SEED")
        if env_seed is not None:
            try:
                seed = int(env_seed)
            except Exception:
                seed = None

    rng = random.Random(seed)
    pool = list(agent_names)
    if human_name and human_name not in pool:
        pool.append(human_name)
    rng.shuffle(pool)

    mafia = pool[:2]
    detective = pool[2:3]
    doctor = pool[3:4]
    villagers = pool[4:]

    players: Dict[str, Player] = {}
    for n in mafia:
        players[n] = Player(name=n, role="mafia", is_human=(n == human_name))
    for n in detective:
        players[n] = Player(name=n, role="detective", is_human=(n == human_name))
    for n in doctor:
        players[n] = Player(name=n, role="doctor", is_human=(n == human_name))
    for n in villagers:
        players[n] = Player(name=n, role="villager", is_human=(n == human_name))

    return players
