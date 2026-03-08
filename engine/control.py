from __future__ import annotations

from collections import defaultdict

from .enums import SectorType
from .events import emit_sector_control_changed
from .models import GameState


def recompute_sector_control(state: GameState, sector_id: str) -> None:
    sector = state.world.sectors[sector_id]
    if sector.sector_type == SectorType.SAFE:
        return

    old_controller = sector.controller_player_id

    influence_by_player: dict[str, int] = defaultdict(int)
    for st_id in sector.structure_ids:
        structure = state.structures.get(st_id)
        if structure is None or not structure.active:
            continue
        influence_by_player[structure.owner_player_id] += structure.influence

    if not influence_by_player:
        sector.controller_player_id = None
    elif max(influence_by_player.values()) <= 0:
        sector.controller_player_id = None
    else:
        max_influence = max(influence_by_player.values())
        leaders = [pid for pid, inf in influence_by_player.items() if inf == max_influence]
        if len(leaders) == 1:
            sector.controller_player_id = leaders[0]
        else:
            sector.controller_player_id = None

    if sector.controller_player_id != old_controller:
        emit_sector_control_changed(state, sector_id, old_controller, sector.controller_player_id)


def recompute_all_frontier_control(state: GameState) -> None:
    for sector_id, sector in state.world.sectors.items():
        if sector.sector_type == SectorType.FRONTIER:
            recompute_sector_control(state, sector_id)


def get_player_controlled_sectors(state: GameState, player_id: str) -> list[str]:
    return [
        sector_id
        for sector_id, sector in state.world.sectors.items()
        if sector.controller_player_id == player_id
    ]
