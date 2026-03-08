from __future__ import annotations

import random

from .actions import get_action_energy_cost
from .config import BUILD_ENERGY_COSTS, STRUCTURE_CATALOG
from .energy import compute_player_available_energy
from .enums import ActionStatus, ActionType, ResourceType, SectorType, StructureType
from .models import Action, GameState, SectorState, next_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_player_controlled_sectors(state: GameState, player_id: str) -> list[str]:
    return sorted(
        sid for sid, sec in state.world.sectors.items()
        if sec.controller_player_id == player_id
    )


def _get_player_safe_sectors(state: GameState, player_id: str) -> list[str]:
    return sorted(
        sid for sid, sec in state.world.sectors.items()
        if sec.safe_owner_player_id == player_id
    )


def _get_frontier_sectors(state: GameState) -> list[str]:
    return sorted(
        sid for sid, sec in state.world.sectors.items()
        if sec.sector_type == SectorType.FRONTIER
    )


def _get_buildable_sectors(state: GameState, player_id: str) -> list[str]:
    controlled = set(_get_player_controlled_sectors(state, player_id))
    safe = set(_get_player_safe_sectors(state, player_id))
    owned = controlled | safe

    adjacent_uncontrolled: set[str] = set()
    for sid in owned:
        sec = state.world.sectors[sid]
        for adj_id in sec.adjacent_sector_ids:
            adj = state.world.sectors.get(adj_id)
            if adj is None:
                continue
            if adj.sector_type == SectorType.FRONTIER and adj.controller_player_id is None:
                adjacent_uncontrolled.add(adj_id)

    frontier_controlled = {s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER}
    return sorted(frontier_controlled | adjacent_uncontrolled)


def _get_sectors_with_metal(state: GameState, sector_ids: list[str]) -> list[str]:
    return sorted(
        sid for sid in sector_ids
        if any(
            n.resource_type == ResourceType.METAL and not n.depleted
            for n in state.world.sectors[sid].resource_nodes
        )
    )


def _get_structures_in_sector(state: GameState, sector_id: str) -> list[str]:
    sec = state.world.sectors.get(sector_id)
    if sec is None:
        return []
    return list(sec.structure_ids)


def _player_structures_of_type(
    state: GameState, player_id: str, structure_type: StructureType
) -> list[str]:
    return sorted(
        st_id for st_id, st in state.structures.items()
        if st.owner_player_id == player_id and st.structure_type == structure_type and st.active
    )


def _enemy_structures_in_reach(state: GameState, player_id: str) -> list[str]:
    controlled = set(_get_player_controlled_sectors(state, player_id))
    attack_node_sectors: set[str] = set()
    for st in state.structures.values():
        if st.owner_player_id == player_id and st.structure_type == StructureType.ATTACK_NODE and st.active:
            attack_node_sectors.add(st.sector_id)

    reachable_sectors: set[str] = set(attack_node_sectors)
    for sid in attack_node_sectors:
        sec = state.world.sectors.get(sid)
        if sec is not None:
            for adj_id in sec.adjacent_sector_ids:
                adj = state.world.sectors.get(adj_id)
                if adj is not None and adj.sector_type == SectorType.FRONTIER:
                    reachable_sectors.add(adj_id)
    for sid in controlled:
        sec = state.world.sectors.get(sid)
        if sec is not None:
            for adj_id in sec.adjacent_sector_ids:
                if adj_id in attack_node_sectors:
                    reachable_sectors.add(sid)

    targets = []
    for sid in sorted(reachable_sectors):
        for st_id in _get_structures_in_sector(state, sid):
            st = state.structures.get(st_id)
            if st is not None and st.owner_player_id != player_id:
                targets.append(st_id)
    return targets


def _can_afford_build(state: GameState, player_id: str, structure_type: StructureType, energy_budget: int) -> bool:
    player = state.players[player_id]
    catalog = STRUCTURE_CATALOG[structure_type]
    if player.metal < catalog["metal_cost"]:
        return False
    if player.data < catalog["data_cost"]:
        return False
    if player.biomass < catalog["biomass_cost"]:
        return False
    energy_cost = BUILD_ENERGY_COSTS.get(structure_type, 0)
    return energy_budget >= energy_cost


def _remaining_energy(state: GameState, player_id: str) -> int:
    player = state.players[player_id]
    available = compute_player_available_energy(state, player_id)
    return available - player.energy_spent_this_heartbeat


def _make_build_action(
    state: GameState, player_id: str, sector_id: str, structure_type: StructureType
) -> Action:
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=player_id,
        issuer_subagent_id=None,
        action_type=ActionType.BUILD_STRUCTURE,
        payload={
            "sector_id": sector_id,
            "structure_type": structure_type.value,
        },
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def _make_attack_action(state: GameState, player_id: str, target_structure_id: str) -> Action:
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=player_id,
        issuer_subagent_id=None,
        action_type=ActionType.ATTACK_STRUCTURE,
        payload={"target_structure_id": target_structure_id},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def _make_scan_action(state: GameState, player_id: str, sector_id: str) -> Action:
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=player_id,
        issuer_subagent_id=None,
        action_type=ActionType.SCAN_SECTOR,
        payload={"sector_id": sector_id},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def _filter_affordable(state: GameState, player_id: str, actions: list[Action]) -> list[Action]:
    budget = _remaining_energy(state, player_id)
    result: list[Action] = []
    for action in actions:
        if action.energy_cost <= budget:
            result.append(action)
            budget -= action.energy_cost
    return result


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Strategy:
    name: str = "base"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. RandomStrategy
# ---------------------------------------------------------------------------

class RandomStrategy(Strategy):
    name = "random"

    def __init__(self, seed: int = 0):
        self._base_seed = seed

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        rng = random.Random(self._base_seed + state.heartbeat * 7919)
        actions: list[Action] = []

        buildable = _get_buildable_sectors(state, player_id)
        if not buildable:
            return []

        buildable_types = [
            StructureType.EXTRACTOR, StructureType.REACTOR, StructureType.BATTERY,
            StructureType.RELAY, StructureType.TOWER, StructureType.ATTACK_NODE,
        ]

        num_actions = rng.randint(0, 3)
        for _ in range(num_actions):
            roll = rng.random()
            if roll < 0.15:
                controlled = _get_player_controlled_sectors(state, player_id)
                safe = _get_player_safe_sectors(state, player_id)
                scannable = sorted(set(controlled) | set(safe))
                if scannable:
                    sector_id = rng.choice(scannable)
                    actions.append(_make_scan_action(state, player_id, sector_id))
            elif roll < 0.9 and buildable:
                sector_id = rng.choice(buildable)
                st_type = rng.choice(buildable_types)
                if _can_afford_build(state, player_id, st_type, _remaining_energy(state, player_id)):
                    actions.append(_make_build_action(state, player_id, sector_id, st_type))

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 2. ExpansionistStrategy
# ---------------------------------------------------------------------------

class ExpansionistStrategy(Strategy):
    name = "expansionist"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = _remaining_energy(state, player_id)

        controlled = set(_get_player_controlled_sectors(state, player_id))
        safe = set(_get_player_safe_sectors(state, player_id))
        owned = controlled | safe

        frontier_uncontrolled: list[str] = []
        for sid in sorted(owned):
            sec = state.world.sectors[sid]
            for adj_id in sorted(sec.adjacent_sector_ids):
                adj = state.world.sectors.get(adj_id)
                if (
                    adj is not None
                    and adj.sector_type == SectorType.FRONTIER
                    and adj.controller_player_id is None
                    and adj_id not in frontier_uncontrolled
                ):
                    frontier_uncontrolled.append(adj_id)

        # First tower to expand
        for sid in frontier_uncontrolled:
            if len(actions) >= 3:
                break
            if _can_afford_build(state, player_id, StructureType.TOWER, budget):
                act = _make_build_action(state, player_id, sid, StructureType.TOWER)
                actions.append(act)
                budget -= act.energy_cost
                break

        # Prioritize extractor on metal nodes early — metal is the limiting resource
        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))
        if extractor_count == 0:
            # Check all owned sectors (safe + controlled) for metal
            all_owned_sectors = sorted(owned)
            metal_owned = _get_sectors_with_metal(state, all_owned_sectors)
            for sid in metal_owned:
                if len(actions) >= 3:
                    break
                existing_extractors = [
                    st_id for st_id in _get_structures_in_sector(state, sid)
                    if state.structures.get(st_id) is not None
                    and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                    and state.structures[st_id].owner_player_id == player_id
                ]
                if not existing_extractors and _can_afford_build(state, player_id, StructureType.EXTRACTOR, budget):
                    act = _make_build_action(state, player_id, sid, StructureType.EXTRACTOR)
                    actions.append(act)
                    budget -= act.energy_cost
                    break

        # Then more extractors on controlled frontier metal sectors
        metal_sectors = _get_sectors_with_metal(state, sorted(controlled))
        for sid in metal_sectors:
            if len(actions) >= 3:
                break
            existing_extractors = [
                st_id for st_id in _get_structures_in_sector(state, sid)
                if state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                and state.structures[st_id].owner_player_id == player_id
            ]
            if not existing_extractors and _can_afford_build(state, player_id, StructureType.EXTRACTOR, budget):
                act = _make_build_action(state, player_id, sid, StructureType.EXTRACTOR)
                actions.append(act)
                budget -= act.energy_cost

        if len(actions) < 3:
            relay_count = len(_player_structures_of_type(state, player_id, StructureType.RELAY))
            tower_count = len(_player_structures_of_type(state, player_id, StructureType.TOWER))
            if tower_count > relay_count * 3 and len(controlled) > 0:
                target_sector = sorted(controlled)[0]
                if _can_afford_build(state, player_id, StructureType.RELAY, budget):
                    act = _make_build_action(state, player_id, target_sector, StructureType.RELAY)
                    actions.append(act)
                    budget -= act.energy_cost

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 3. EconomistStrategy
# ---------------------------------------------------------------------------

class EconomistStrategy(Strategy):
    name = "economist"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = _remaining_energy(state, player_id)

        controlled = sorted(_get_player_controlled_sectors(state, player_id))
        safe = sorted(_get_player_safe_sectors(state, player_id))
        owned = sorted(set(controlled) | set(safe))
        buildable = _get_buildable_sectors(state, player_id)

        reactor_count = len(_player_structures_of_type(state, player_id, StructureType.REACTOR))
        battery_count = len(_player_structures_of_type(state, player_id, StructureType.BATTERY))
        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))

        frontier_controlled = [s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER]

        # FIRST priority: expand into frontier if no frontier sectors controlled
        # (Without frontier territory, reactors/batteries can't be built)
        if not frontier_controlled:
            frontier_uncontrolled: list[str] = []
            for sid in owned:
                sec = state.world.sectors[sid]
                for adj_id in sorted(sec.adjacent_sector_ids):
                    adj = state.world.sectors.get(adj_id)
                    if (
                        adj is not None
                        and adj.sector_type == SectorType.FRONTIER
                        and adj.controller_player_id is None
                        and adj_id not in frontier_uncontrolled
                    ):
                        frontier_uncontrolled.append(adj_id)
            if frontier_uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER, budget):
                act = _make_build_action(state, player_id, frontier_uncontrolled[0], StructureType.TOWER)
                actions.append(act)
                budget -= act.energy_cost
                return _filter_affordable(state, player_id, actions)

        # SECOND priority: build extractor on metal nodes — metal is the limiting resource
        if extractor_count == 0:
            # Check all owned sectors for metal first
            metal_owned = _get_sectors_with_metal(state, owned)
            for sid in metal_owned:
                if len(actions) >= 2:
                    break
                existing = [
                    st_id for st_id in _get_structures_in_sector(state, sid)
                    if state.structures.get(st_id) is not None
                    and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                    and state.structures[st_id].owner_player_id == player_id
                ]
                if not existing and _can_afford_build(state, player_id, StructureType.EXTRACTOR, budget):
                    act = _make_build_action(state, player_id, sid, StructureType.EXTRACTOR)
                    actions.append(act)
                    budget -= act.energy_cost
                    break

        # THIRD: build reactors in controlled frontier sectors
        if reactor_count < 2 and frontier_controlled:
            target = frontier_controlled[0]
            if _can_afford_build(state, player_id, StructureType.REACTOR, budget) and len(actions) < 3:
                act = _make_build_action(state, player_id, target, StructureType.REACTOR)
                actions.append(act)
                budget -= act.energy_cost

        # FOURTH: more extractors on metal sectors
        metal_sectors = _get_sectors_with_metal(state, buildable)
        for sid in metal_sectors:
            if len(actions) >= 3:
                break
            existing = [
                st_id for st_id in _get_structures_in_sector(state, sid)
                if state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                and state.structures[st_id].owner_player_id == player_id
            ]
            if not existing and _can_afford_build(state, player_id, StructureType.EXTRACTOR, budget):
                act = _make_build_action(state, player_id, sid, StructureType.EXTRACTOR)
                actions.append(act)
                budget -= act.energy_cost

        # FIFTH: batteries
        if battery_count < reactor_count and len(actions) < 3:
            for sid in frontier_controlled:
                if _can_afford_build(state, player_id, StructureType.BATTERY, budget):
                    act = _make_build_action(state, player_id, sid, StructureType.BATTERY)
                    actions.append(act)
                    budget -= act.energy_cost
                    break

        # SIXTH: expand more when we have surplus metal
        player = state.players[player_id]
        if len(actions) < 3 and player.metal > 15:
            frontier_uncontrolled2: list[str] = []
            for sid in owned:
                sec = state.world.sectors[sid]
                for adj_id in sorted(sec.adjacent_sector_ids):
                    adj = state.world.sectors.get(adj_id)
                    if (
                        adj is not None
                        and adj.sector_type == SectorType.FRONTIER
                        and adj.controller_player_id is None
                        and adj_id not in frontier_uncontrolled2
                    ):
                        frontier_uncontrolled2.append(adj_id)
            if frontier_uncontrolled2 and _can_afford_build(state, player_id, StructureType.TOWER, budget):
                act = _make_build_action(state, player_id, frontier_uncontrolled2[0], StructureType.TOWER)
                actions.append(act)
                budget -= act.energy_cost

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 4. AggressorStrategy
# ---------------------------------------------------------------------------

class AggressorStrategy(Strategy):
    name = "aggressor"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = _remaining_energy(state, player_id)

        controlled = set(_get_player_controlled_sectors(state, player_id))
        safe = set(_get_player_safe_sectors(state, player_id))
        owned = controlled | safe

        attack_nodes = _player_structures_of_type(state, player_id, StructureType.ATTACK_NODE)

        enemy_targets = _enemy_structures_in_reach(state, player_id)
        priority_targets = sorted(
            enemy_targets,
            key=lambda st_id: (
                0 if state.structures[st_id].structure_type == StructureType.REACTOR else
                1 if state.structures[st_id].structure_type == StructureType.BATTERY else
                2,
                st_id,
            ),
        )

        for target_id in priority_targets:
            if len(actions) >= 2:
                break
            if budget >= 6:
                act = _make_attack_action(state, player_id, target_id)
                actions.append(act)
                budget -= act.energy_cost

        if not attack_nodes or len(actions) < 2:
            enemy_adjacent: list[str] = []
            for sid in sorted(owned):
                sec = state.world.sectors[sid]
                for adj_id in sorted(sec.adjacent_sector_ids):
                    adj = state.world.sectors.get(adj_id)
                    if adj is None or adj.sector_type != SectorType.FRONTIER:
                        continue
                    has_enemy = any(
                        state.structures.get(st_id) is not None
                        and state.structures[st_id].owner_player_id != player_id
                        for st_id in adj.structure_ids
                    )
                    if has_enemy or (adj.controller_player_id is not None and adj.controller_player_id != player_id):
                        if adj_id not in enemy_adjacent:
                            enemy_adjacent.append(adj_id)

            need_tower = False
            for sid in enemy_adjacent:
                sec = state.world.sectors[sid]
                if sec.controller_player_id != player_id and sec.controller_player_id is not None:
                    need_tower = True
                    break
                if sec.controller_player_id is None:
                    need_tower = True
                    break

            buildable = _get_buildable_sectors(state, player_id)

            if need_tower and not attack_nodes and buildable:
                target_sector = buildable[0]
                if enemy_adjacent:
                    for ea in enemy_adjacent:
                        if ea in buildable:
                            target_sector = ea
                            break
                if _can_afford_build(state, player_id, StructureType.TOWER, budget) and len(actions) < 3:
                    act = _make_build_action(state, player_id, target_sector, StructureType.TOWER)
                    actions.append(act)
                    budget -= act.energy_cost

            if len(actions) < 3 and buildable:
                node_sector = None
                for sid in sorted(controlled):
                    if sid in buildable:
                        node_sector = sid
                        break
                if node_sector is None and buildable:
                    node_sector = buildable[0]
                if node_sector and _can_afford_build(state, player_id, StructureType.ATTACK_NODE, budget):
                    act = _make_build_action(state, player_id, node_sector, StructureType.ATTACK_NODE)
                    actions.append(act)
                    budget -= act.energy_cost

        # Build extractor early so aggressor doesn't run out of metal
        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))
        if extractor_count == 0 and len(actions) < 4:
            all_owned = sorted(controlled | safe)
            metal_owned = _get_sectors_with_metal(state, all_owned)
            for sid in metal_owned:
                existing = [
                    st_id for st_id in _get_structures_in_sector(state, sid)
                    if state.structures.get(st_id) is not None
                    and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                    and state.structures[st_id].owner_player_id == player_id
                ]
                if not existing and _can_afford_build(state, player_id, StructureType.EXTRACTOR, budget):
                    act = _make_build_action(state, player_id, sid, StructureType.EXTRACTOR)
                    actions.append(act)
                    budget -= act.energy_cost
                    break

        if len(actions) < 4:
            buildable = _get_buildable_sectors(state, player_id)
            frontier_uncontrolled = [
                sid for sid in buildable if state.world.sectors[sid].controller_player_id is None
            ]
            if frontier_uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER, budget):
                act = _make_build_action(state, player_id, frontier_uncontrolled[0], StructureType.TOWER)
                actions.append(act)
                budget -= act.energy_cost

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 5. TurtleStrategy
# ---------------------------------------------------------------------------

class TurtleStrategy(Strategy):
    name = "turtle"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = _remaining_energy(state, player_id)

        controlled = sorted(_get_player_controlled_sectors(state, player_id))
        safe = sorted(_get_player_safe_sectors(state, player_id))
        owned = sorted(set(controlled) | set(safe))
        buildable = _get_buildable_sectors(state, player_id)

        tower_count = len(_player_structures_of_type(state, player_id, StructureType.TOWER))
        battery_count = len(_player_structures_of_type(state, player_id, StructureType.BATTERY))
        relay_count = len(_player_structures_of_type(state, player_id, StructureType.RELAY))
        reactor_count = len(_player_structures_of_type(state, player_id, StructureType.REACTOR))

        max_frontier_sectors = 3
        frontier_controlled = [s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER]

        if len(frontier_controlled) < max_frontier_sectors:
            adjacent: list[str] = []
            for sid in owned:
                sec = state.world.sectors[sid]
                for adj_id in sorted(sec.adjacent_sector_ids):
                    adj = state.world.sectors.get(adj_id)
                    if (
                        adj is not None
                        and adj.sector_type == SectorType.FRONTIER
                        and adj.controller_player_id is None
                        and adj_id not in adjacent
                    ):
                        adjacent.append(adj_id)
            if adjacent and _can_afford_build(state, player_id, StructureType.TOWER, budget):
                act = _make_build_action(state, player_id, adjacent[0], StructureType.TOWER)
                actions.append(act)
                budget -= act.energy_cost

        # Build extractor early in controlled sectors — metal is the limiting resource
        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))
        if extractor_count == 0 and len(actions) < 2:
            all_owned = sorted(set(controlled) | set(safe))
            metal_owned = _get_sectors_with_metal(state, all_owned)
            for sid in metal_owned:
                existing = [
                    st_id for st_id in _get_structures_in_sector(state, sid)
                    if state.structures.get(st_id) is not None
                    and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                    and state.structures[st_id].owner_player_id == player_id
                ]
                if not existing and _can_afford_build(state, player_id, StructureType.EXTRACTOR, budget):
                    act = _make_build_action(state, player_id, sid, StructureType.EXTRACTOR)
                    actions.append(act)
                    budget -= act.energy_cost
                    break

        for sid in frontier_controlled:
            if len(actions) >= 2:
                break
            player_towers_here = [
                st_id for st_id in _get_structures_in_sector(state, sid)
                if state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.TOWER
                and state.structures[st_id].owner_player_id == player_id
            ]
            if len(player_towers_here) < 2 and _can_afford_build(state, player_id, StructureType.TOWER, budget):
                act = _make_build_action(state, player_id, sid, StructureType.TOWER)
                actions.append(act)
                budget -= act.energy_cost

        if len(actions) < 2 and reactor_count < 1 and buildable:
            target = frontier_controlled[0] if frontier_controlled else buildable[0]
            if _can_afford_build(state, player_id, StructureType.REACTOR, budget):
                act = _make_build_action(state, player_id, target, StructureType.REACTOR)
                actions.append(act)
                budget -= act.energy_cost

        if len(actions) < 3 and battery_count < tower_count:
            for sid in frontier_controlled:
                if _can_afford_build(state, player_id, StructureType.BATTERY, budget):
                    act = _make_build_action(state, player_id, sid, StructureType.BATTERY)
                    actions.append(act)
                    budget -= act.energy_cost
                    break

        if len(actions) < 3 and relay_count < 1 and frontier_controlled:
            target = frontier_controlled[0]
            if _can_afford_build(state, player_id, StructureType.RELAY, budget):
                act = _make_build_action(state, player_id, target, StructureType.RELAY)
                actions.append(act)
                budget -= act.energy_cost

        return _filter_affordable(state, player_id, actions)


ALL_STRATEGIES: dict[str, type[Strategy]] = {
    "random": RandomStrategy,
    "expansionist": ExpansionistStrategy,
    "economist": EconomistStrategy,
    "aggressor": AggressorStrategy,
    "turtle": TurtleStrategy,
}
