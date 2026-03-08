from __future__ import annotations

from .config import GameConfig
from .enums import ActionType
from .heartbeat import run_heartbeat as _run_heartbeat
from .models import (
    Action,
    GameState,
    HeartbeatResult,
    ValidationResult,
)
from .persistence import load_game as _load_game, save_game as _save_game
from .world import create_default_world


def init_game(
    config: GameConfig | None, players: list[str], seed: int
) -> GameState:
    if config is None:
        config = GameConfig()

    world, player_states, initial_structures = create_default_world(config, players)

    state = GameState(
        game_id=f"game_{seed}",
        heartbeat=0,
        seed=seed,
        config=config,
        world=world,
        players=player_states,
        subagents={},
        structures=initial_structures,
        actions_pending=[],
        event_log=[],
        id_counter=len(initial_structures),
    )
    return state


def submit_action(state: GameState, action: Action) -> ValidationResult:
    if not isinstance(action.action_type, ActionType):
        try:
            ActionType(action.action_type)
        except (ValueError, KeyError):
            return ValidationResult(
                accepted=False,
                action_id=action.action_id,
                reason=f"Invalid action_type '{action.action_type}'",
            )

    if not action.issuer_player_id:
        return ValidationResult(
            accepted=False,
            action_id=action.action_id,
            reason="Missing issuer_player_id",
        )

    state.actions_pending.append(action)
    return ValidationResult(accepted=True, action_id=action.action_id, reason=None)


def run_heartbeat(state: GameState) -> HeartbeatResult:
    return _run_heartbeat(state)


def get_state(state: GameState) -> GameState:
    return state


def save_game(state: GameState, path: str) -> None:
    _save_game(state, path)


def load_game(path: str) -> GameState:
    return _load_game(path)


def get_player_view(state: GameState, player_id: str) -> dict:
    player = state.players.get(player_id)
    if player is None:
        return {"error": f"Player '{player_id}' not found"}

    controlled_sectors = [
        sid
        for sid, sector in state.world.sectors.items()
        if sector.controller_player_id == player_id
    ]

    visible_structures = {
        st_id: {
            "structure_id": st.structure_id,
            "owner_player_id": st.owner_player_id,
            "sector_id": st.sector_id,
            "structure_type": st.structure_type.value,
            "hp": st.hp,
            "max_hp": st.max_hp,
            "active": st.active,
        }
        for st_id, st in state.structures.items()
    }

    return {
        "player": {
            "player_id": player.player_id,
            "name": player.name,
            "alive": player.alive,
            "sanctuary_sector_id": player.sanctuary_sector_id,
            "sanctuary_core_structure_id": player.sanctuary_core_structure_id,
            "energy_reserve": player.energy_reserve,
            "metal": player.metal,
            "data": player.data,
            "biomass": player.biomass,
        },
        "controlled_sectors": controlled_sectors,
        "structures": visible_structures,
        "energy": {
            "reserve": player.energy_reserve,
            "spent_this_heartbeat": player.energy_spent_this_heartbeat,
        },
    }
