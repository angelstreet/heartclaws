from __future__ import annotations

from .config import GameConfig, STRUCTURE_CATALOG, SUBAGENT_UPKEEP
from .enums import StructureType
from .models import GameState, PlayerState, StructureState, SubagentState


def _sanctuary_core_active(state: GameState, player: PlayerState) -> bool:
    core_id = player.sanctuary_core_structure_id
    if core_id is None:
        return False
    core = state.structures.get(core_id)
    return core is not None and core.active


def _active_structures(state: GameState, player_id: str) -> list[StructureState]:
    return [
        s for s in state.structures.values()
        if s.owner_player_id == player_id and s.active
    ]


def _active_subagents(state: GameState, player_id: str) -> list[SubagentState]:
    return [
        a for a in state.subagents.values()
        if a.owner_player_id == player_id and a.active
    ]


def compute_player_income(state: GameState, player_id: str) -> int:
    player = state.players[player_id]
    cfg = state.config
    sanctuary_income = cfg.sanctuary_income if _sanctuary_core_active(state, player) else 0
    frontier_income = sum(s.energy_income_bonus for s in _active_structures(state, player_id))
    return sanctuary_income + frontier_income


def compute_player_reserve_cap(state: GameState, player_id: str) -> int:
    player = state.players[player_id]
    cfg = state.config
    base = cfg.sanctuary_reserve_cap if _sanctuary_core_active(state, player) else 0
    return base + sum(s.reserve_cap_bonus for s in _active_structures(state, player_id))


def compute_player_throughput_cap(state: GameState, player_id: str) -> int:
    player = state.players[player_id]
    cfg = state.config
    base = cfg.sanctuary_throughput_cap if _sanctuary_core_active(state, player) else 0
    return base + sum(s.throughput_cap_bonus for s in _active_structures(state, player_id))


def compute_player_upkeep(state: GameState, player_id: str) -> int:
    structure_upkeep = sum(s.upkeep_cost for s in _active_structures(state, player_id))
    subagent_upkeep = sum(a.upkeep_cost for a in _active_subagents(state, player_id))
    return structure_upkeep + subagent_upkeep


def apply_upkeep_deactivations(state: GameState, player_id: str) -> None:
    player = state.players[player_id]

    def _affordable() -> bool:
        return player.energy_reserve + compute_player_income(state, player_id) >= compute_player_upkeep(state, player_id)

    if _affordable():
        return

    # Deactivate subagents first: highest upkeep, then subagent_id asc
    subs = sorted(
        _active_subagents(state, player_id),
        key=lambda a: (-a.upkeep_cost, a.subagent_id),
    )
    for sub in subs:
        sub.active = False
        if _affordable():
            return

    # Deactivate non-core frontier structures: highest upkeep, then structure_id asc
    core_id = player.sanctuary_core_structure_id
    structs = sorted(
        [s for s in _active_structures(state, player_id) if s.structure_id != core_id],
        key=lambda s: (-s.upkeep_cost, s.structure_id),
    )
    for struct in structs:
        struct.active = False
        if _affordable():
            return


def compute_player_available_energy(state: GameState, player_id: str) -> int:
    player = state.players[player_id]
    income = compute_player_income(state, player_id)
    upkeep = compute_player_upkeep(state, player_id)
    reserve_before = player.energy_reserve
    throughput_cap = compute_player_throughput_cap(state, player_id)
    gross_energy = reserve_before + income - upkeep
    return min(max(gross_energy, 0), throughput_cap)


def finalize_player_reserve(state: GameState, player_id: str) -> None:
    player = state.players[player_id]
    income = compute_player_income(state, player_id)
    upkeep = compute_player_upkeep(state, player_id)
    reserve_before = player.energy_reserve
    reserve_cap = compute_player_reserve_cap(state, player_id)
    gross_energy = reserve_before + income - upkeep
    end_reserve = max(0, min(gross_energy - player.energy_spent_this_heartbeat, reserve_cap))
    player.energy_reserve = end_reserve
