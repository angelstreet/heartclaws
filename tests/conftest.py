"""Shared fixtures and helpers for HeartClaws tests."""
from __future__ import annotations

import sys
import os

# Ensure engine package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.actions import get_action_energy_cost
from engine.config import GameConfig
from engine.engine import init_game, submit_action, run_heartbeat
from engine.enums import ActionType, ActionStatus, StructureType
from engine.models import Action, GameState, next_id


def make_action(
    state: GameState,
    player_id: str,
    action_type: ActionType,
    payload: dict,
    priority: int = 5,
    subagent_id: str | None = None,
) -> Action:
    """Create an Action with correct energy cost, ready for submit_action."""
    action_id = next_id(state, "act")
    # Build a temporary action to compute cost
    tmp = Action(
        action_id=action_id,
        issuer_player_id=player_id,
        issuer_subagent_id=subagent_id,
        action_type=action_type,
        payload=payload,
        energy_cost=0,
        submitted_heartbeat=state.heartbeat,
        priority=priority,
        status=ActionStatus.PENDING,
    )
    cost = get_action_energy_cost(tmp)
    return Action(
        action_id=action_id,
        issuer_player_id=player_id,
        issuer_subagent_id=subagent_id,
        action_type=action_type,
        payload=payload,
        energy_cost=cost,
        submitted_heartbeat=state.heartbeat,
        priority=priority,
        status=ActionStatus.PENDING,
    )


def init_two_player_game(seed: int = 42, config: GameConfig | None = None) -> GameState:
    """Init a standard 2-player game."""
    return init_game(config, ["p1", "p2"], seed=seed)


def build_structure_in_frontier(
    state: GameState,
    player_id: str,
    sector_id: str,
    structure_type: StructureType,
    priority: int = 5,
) -> None:
    """Submit a BUILD_STRUCTURE action and run heartbeat to resolve it."""
    action = make_action(
        state,
        player_id,
        ActionType.BUILD_STRUCTURE,
        {"sector_id": sector_id, "structure_type": structure_type.value},
        priority=priority,
    )
    submit_action(state, action)
    run_heartbeat(state)
