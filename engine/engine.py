from __future__ import annotations

import math

from .config import GameConfig
from .enums import ActionType
from .heartbeat import run_heartbeat as _run_heartbeat
from .models import (
    Action,
    GameState,
    HeartbeatResult,
    ValidationResult,
)
from .actions import get_action_energy_cost, validate_action
from .energy import compute_player_available_energy
from .persistence import load_game as _load_game, save_game as _save_game
from .world import create_default_world

# Escalating cost multipliers per action number within a single heartbeat.
# The Nth action submitted by a player this heartbeat costs base × ESCALATING[N-1].
# This rewards quality decisions over quantity spam.
ESCALATING_MULTIPLIERS = [1.0, 1.5, 2.0, 3.0, 5.0]


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

    # Validate against current game state immediately — give the caller a real reason
    # instead of silently queuing and failing at heartbeat resolution.
    player = state.players.get(action.issuer_player_id)
    if player is None:
        return ValidationResult(
            accepted=False,
            action_id=action.action_id,
            reason=f"Player '{action.issuer_player_id}' not found",
        )

    # Escalating cost: the Nth action this heartbeat costs base × ESCALATING[N-1].
    # Only count pending actions from the current heartbeat period.
    current_pending = [
        a for a in state.actions_pending
        if a.issuer_player_id == action.issuer_player_id
        and a.submitted_heartbeat == state.heartbeat
    ]
    action_number = len(current_pending)  # 0-indexed → action_number=0 means first action
    multiplier = ESCALATING_MULTIPLIERS[min(action_number, len(ESCALATING_MULTIPLIERS) - 1)]
    base_cost = get_action_energy_cost(action, state)
    scaled_cost = math.ceil(base_cost * multiplier)

    # Energy already committed by queued actions this heartbeat (already scaled).
    pending_energy = sum(a.energy_cost for a in current_pending)
    available = compute_player_available_energy(state, action.issuer_player_id)
    remaining = available - pending_energy

    if scaled_cost > 0 and remaining < scaled_cost:
        suffix = f" (action #{action_number + 1} costs {multiplier:.1f}x)" if multiplier > 1.0 else ""
        return ValidationResult(
            accepted=False,
            action_id=action.action_id,
            reason=f"Not enough energy (need {scaled_cost}{suffix}, have {remaining})",
        )

    # Run full validation (sector, resources, etc.) using base energy so it doesn't
    # double-count; we already handled the scaled energy check above.
    original_spent = player.energy_spent_this_heartbeat
    player.energy_spent_this_heartbeat = pending_energy
    result = validate_action(state, action)
    player.energy_spent_this_heartbeat = original_spent
    if not result.accepted:
        return result

    # Store the scaled cost so the heartbeat debits the correct amount.
    action.energy_cost = scaled_cost
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

    # Include resource node info for controlled sectors + adjacent uncontrolled sectors
    sector_details = {}
    adjacent_uncontrolled: set[str] = set()
    for sid in controlled_sectors:
        sector = state.world.sectors[sid]
        nodes = [
            {"type": n.resource_type.value, "richness": n.richness, "depleted": n.depleted}
            for n in sector.resource_nodes
        ]
        adj_ids = [a for a in sector.adjacent_sector_ids if a in state.world.sectors]
        sector_details[sid] = {
            "resource_nodes": nodes,
            "adjacent_sectors": adj_ids,
            "sector_type": sector.sector_type.value,
        }
        for adj_id in adj_ids:
            adj_sector = state.world.sectors[adj_id]
            if adj_sector.controller_player_id != player_id:
                adjacent_uncontrolled.add(adj_id)

    # Expose adjacent uncontrolled sectors so agents can build TOWERs to expand
    for sid in adjacent_uncontrolled:
        sector = state.world.sectors[sid]
        nodes = [
            {"type": n.resource_type.value, "richness": n.richness, "depleted": n.depleted}
            for n in sector.resource_nodes
        ]
        controller = sector.controller_player_id or "uncontrolled"
        sector_details[sid] = {
            "resource_nodes": nodes,
            "sector_type": sector.sector_type.value,
            "controller": controller,
            "can_build_tower": controller == "uncontrolled",
        }

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

    # Espionage intel: include target player resources and structures if reveal is active
    espionage_intel = {}
    for target_pid, expires_at in player.espionage_reveals.items():
        if expires_at > state.heartbeat:
            target_player = state.players.get(target_pid)
            if target_player is not None:
                target_structures = [
                    {
                        "structure_id": st.structure_id,
                        "owner_player_id": st.owner_player_id,
                        "sector_id": st.sector_id,
                        "structure_type": st.structure_type.value,
                        "hp": st.hp,
                        "max_hp": st.max_hp,
                        "active": st.active,
                    }
                    for st in state.structures.values()
                    if st.owner_player_id == target_pid
                ]
                espionage_intel[target_pid] = {
                    "resources": {
                        "metal": target_player.metal,
                        "data": target_player.data,
                        "biomass": target_player.biomass,
                    },
                    "structures": target_structures,
                    "expires_at": expires_at,
                }

    result = {
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
        "sector_details": sector_details,
        "structures": visible_structures,
        "energy": {
            "reserve": player.energy_reserve,
            "available": compute_player_available_energy(state, player_id),
            "spent_this_heartbeat": player.energy_spent_this_heartbeat,
        },
        "action_cost_multiplier": ESCALATING_MULTIPLIERS[
            min(
                sum(
                    1 for a in state.actions_pending
                    if a.issuer_player_id == player_id
                    and a.submitted_heartbeat == state.heartbeat
                ),
                len(ESCALATING_MULTIPLIERS) - 1,
            )
        ],
    }

    if espionage_intel:
        result["espionage_intel"] = espionage_intel

    return result
