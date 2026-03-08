from __future__ import annotations

from collections import defaultdict

from .enums import DiplomaticStance, SectorType
from .events import emit_sector_control_changed
from .models import GameState


def _find_mutual_ally_groups(state: GameState, player_ids: set[str]) -> list[set[str]]:
    """Find groups of mutual allies among the given player IDs.

    Two players are mutual allies if both set ALLY toward each other.
    Groups are formed transitively: if A<->B and B<->C are mutual allies,
    then {A, B, C} is one group.
    """
    # Build adjacency for mutual allies
    parent: dict[str, str] = {pid: pid for pid in player_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    pids = list(player_ids)
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            pa = state.players.get(pids[i])
            pb = state.players.get(pids[j])
            if pa is None or pb is None:
                continue
            if (
                pa.diplomacy_stance.get(pids[j]) == DiplomaticStance.ALLY
                and pb.diplomacy_stance.get(pids[i]) == DiplomaticStance.ALLY
            ):
                union(pids[i], pids[j])

    groups: dict[str, set[str]] = defaultdict(set)
    for pid in pids:
        groups[find(pid)].add(pid)

    return [g for g in groups.values() if len(g) > 1]


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
        if structure.owner_player_id is not None:
            influence_by_player[structure.owner_player_id] += structure.influence

    if not influence_by_player:
        sector.controller_player_id = None
    elif max(influence_by_player.values()) <= 0:
        sector.controller_player_id = None
    else:
        # Combine influence for mutual ally groups
        ally_groups = _find_mutual_ally_groups(state, set(influence_by_player.keys()))

        # Build effective influence: for each group, sum individual influence
        effective_influence: dict[str, int] = dict(influence_by_player)
        for group in ally_groups:
            combined = sum(influence_by_player.get(pid, 0) for pid in group)
            for pid in group:
                effective_influence[pid] = combined

        max_influence = max(effective_influence.values())
        leaders = [pid for pid, inf in effective_influence.items() if inf == max_influence]
        if len(leaders) == 1:
            sector.controller_player_id = leaders[0]
        elif len(leaders) > 1:
            # If all leaders are in the same mutual ally group, pick the one
            # with the highest individual influence (tie-break: lowest player_id)
            for group in ally_groups:
                if all(l in group for l in leaders):
                    # All tied leaders are mutual allies — pick the one with
                    # highest individual influence, then lowest player_id
                    leaders.sort(key=lambda pid: (-influence_by_player.get(pid, 0), pid))
                    sector.controller_player_id = leaders[0]
                    break
            else:
                sector.controller_player_id = None
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
