"""AI strategies for HeartClaws agents.

Each strategy implements decide(state, player_id) -> list[Action].
Strategies should ALWAYS produce actions when possible — an idle agent is a dead agent.
"""

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


def _get_expandable_frontier(state: GameState, player_id: str) -> list[str]:
    """Frontier sectors adjacent to owned territory that are uncontrolled or enemy-controlled."""
    controlled = set(_get_player_controlled_sectors(state, player_id))
    safe = set(_get_player_safe_sectors(state, player_id))
    owned = controlled | safe

    result: list[str] = []
    for sid in sorted(owned):
        sec = state.world.sectors[sid]
        for adj_id in sorted(sec.adjacent_sector_ids):
            adj = state.world.sectors.get(adj_id)
            if adj is None or adj.sector_type != SectorType.FRONTIER:
                continue
            if adj.controller_player_id != player_id and adj_id not in result:
                result.append(adj_id)
    return result


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


def _count_player_structures_in_sector(
    state: GameState, player_id: str, sector_id: str, structure_type: StructureType
) -> int:
    """Count how many structures of a type the player has in a sector."""
    count = 0
    for st_id in _get_structures_in_sector(state, sector_id):
        st = state.structures.get(st_id)
        if st and st.owner_player_id == player_id and st.structure_type == structure_type:
            count += 1
    return count


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


def _try_build(state: GameState, player_id: str, sector_id: str, stype: StructureType,
               actions: list[Action], budget: list[int], max_actions: int = 3) -> bool:
    """Try to add a build action. Returns True if successful. budget is a 1-element list for mutability."""
    if len(actions) >= max_actions:
        return False
    if not _can_afford_build(state, player_id, stype, budget[0]):
        return False
    act = _make_build_action(state, player_id, sector_id, stype)
    actions.append(act)
    budget[0] -= act.energy_cost
    return True


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Strategy:
    name: str = "base"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. RandomStrategy — random valid moves (chaos baseline)
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

        num_actions = rng.randint(1, 3)
        for _ in range(num_actions):
            roll = rng.random()
            if roll < 0.1:
                # Scan
                controlled = _get_player_controlled_sectors(state, player_id)
                safe = _get_player_safe_sectors(state, player_id)
                scannable = sorted(set(controlled) | set(safe))
                if scannable:
                    sector_id = rng.choice(scannable)
                    actions.append(_make_scan_action(state, player_id, sector_id))
            elif roll < 0.2:
                # Attack
                targets = _enemy_structures_in_reach(state, player_id)
                if targets:
                    actions.append(_make_attack_action(state, player_id, rng.choice(targets)))
            else:
                # Build
                sector_id = rng.choice(buildable)
                st_type = rng.choice(buildable_types)
                if _can_afford_build(state, player_id, st_type, _remaining_energy(state, player_id)):
                    actions.append(_make_build_action(state, player_id, sector_id, st_type))

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 2. ExpansionistStrategy — claim territory aggressively, build everywhere
# ---------------------------------------------------------------------------

class ExpansionistStrategy(Strategy):
    name = "expansionist"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = [_remaining_energy(state, player_id)]

        controlled = set(_get_player_controlled_sectors(state, player_id))
        safe = set(_get_player_safe_sectors(state, player_id))
        owned = controlled | safe
        buildable = _get_buildable_sectors(state, player_id)
        expandable = _get_expandable_frontier(state, player_id)
        frontier_controlled = sorted(s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER)

        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))

        # 1. CRITICAL: Build extractors on metal FIRST — without metal income, everything stalls
        if extractor_count < max(1, len(frontier_controlled) // 2):
            metal_buildable = _get_sectors_with_metal(state, buildable)
            for sid in metal_buildable:
                if len(actions) >= 2:
                    break
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.EXTRACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.EXTRACTOR, actions, budget)

        # 2. Expand — claim new territory with towers
        for sid in expandable:
            if len(actions) >= 3:
                break
            _try_build(state, player_id, sid, StructureType.TOWER, actions, budget)

        # 3. Reinforce controlled sectors — add towers for influence
        for sid in frontier_controlled:
            if len(actions) >= 3:
                break
            tower_count = _count_player_structures_in_sector(state, player_id, sid, StructureType.TOWER)
            if tower_count < 2:
                _try_build(state, player_id, sid, StructureType.TOWER, actions, budget)

        # 4. Build relays for throughput (1 per 3 sectors)
        relay_count = len(_player_structures_of_type(state, player_id, StructureType.RELAY))
        if relay_count * 3 < len(frontier_controlled) and frontier_controlled and len(actions) < 3:
            _try_build(state, player_id, frontier_controlled[0], StructureType.RELAY, actions, budget)

        # 5. Build reactors for energy (1 per 4 sectors)
        reactor_count = len(_player_structures_of_type(state, player_id, StructureType.REACTOR))
        if reactor_count * 4 < len(frontier_controlled) and frontier_controlled and len(actions) < 3:
            _try_build(state, player_id, frontier_controlled[-1], StructureType.REACTOR, actions, budget)

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 3. EconomistStrategy — maximize income, then expand with surplus
# ---------------------------------------------------------------------------

class EconomistStrategy(Strategy):
    name = "economist"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = [_remaining_energy(state, player_id)]
        player = state.players[player_id]

        controlled = sorted(_get_player_controlled_sectors(state, player_id))
        safe = sorted(_get_player_safe_sectors(state, player_id))
        owned = sorted(set(controlled) | set(safe))
        buildable = _get_buildable_sectors(state, player_id)
        expandable = _get_expandable_frontier(state, player_id)
        frontier_controlled = [s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER]

        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))
        reactor_count = len(_player_structures_of_type(state, player_id, StructureType.REACTOR))
        battery_count = len(_player_structures_of_type(state, player_id, StructureType.BATTERY))

        # 1. CRITICAL: Build extractors on metal nodes FIRST — economy is everything
        metal_buildable = _get_sectors_with_metal(state, buildable)
        target_extractors = max(1, len(metal_buildable))  # economist wants ALL metal nodes
        if extractor_count < target_extractors:
            for sid in metal_buildable:
                if len(actions) >= 2:
                    break
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.EXTRACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.EXTRACTOR, actions, budget)

        # 2. Expand to reach more metal nodes and territory
        if len(actions) < 3:
            # Prefer sectors with metal
            metal_expandable = _get_sectors_with_metal(state, expandable)
            other_expandable = [s for s in expandable if s not in metal_expandable]
            for sid in metal_expandable + other_expandable:
                if len(actions) >= 3:
                    break
                _try_build(state, player_id, sid, StructureType.TOWER, actions, budget)

        # 3. Reactors — build up energy income (target: 1 per 2 sectors)
        target_reactors = max(1, len(frontier_controlled) // 2)
        if reactor_count < target_reactors and frontier_controlled and len(actions) < 3:
            for sid in frontier_controlled:
                if len(actions) >= 3:
                    break
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.REACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.REACTOR, actions, budget)
                    break

        # 4. Batteries — store surplus energy (target: match reactor count)
        if battery_count < reactor_count and frontier_controlled and len(actions) < 3:
            for sid in frontier_controlled:
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.BATTERY) == 0:
                    _try_build(state, player_id, sid, StructureType.BATTERY, actions, budget)
                    break

        # 5. Relays for throughput
        relay_count = len(_player_structures_of_type(state, player_id, StructureType.RELAY))
        if relay_count < reactor_count and frontier_controlled and len(actions) < 3:
            for sid in frontier_controlled:
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.RELAY) == 0:
                    _try_build(state, player_id, sid, StructureType.RELAY, actions, budget)
                    break

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 4. AggressorStrategy — attack enemies, build attack nodes, expand toward enemy
# ---------------------------------------------------------------------------

class AggressorStrategy(Strategy):
    name = "aggressor"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = [_remaining_energy(state, player_id)]

        controlled = set(_get_player_controlled_sectors(state, player_id))
        safe = set(_get_player_safe_sectors(state, player_id))
        owned = controlled | safe
        buildable = _get_buildable_sectors(state, player_id)
        expandable = _get_expandable_frontier(state, player_id)
        frontier_controlled = sorted(s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER)

        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))
        attack_node_count = len(_player_structures_of_type(state, player_id, StructureType.ATTACK_NODE))

        # 1. CRITICAL: Need at least 1 extractor for metal income
        if extractor_count == 0:
            metal_buildable = _get_sectors_with_metal(state, buildable)
            for sid in metal_buildable:
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.EXTRACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.EXTRACTOR, actions, budget)
                    break

        # 2. Attack any enemy structures in reach (priority: reactors > batteries > rest)
        enemy_targets = _enemy_structures_in_reach(state, player_id)
        priority_targets = sorted(
            enemy_targets,
            key=lambda st_id: (
                0 if state.structures[st_id].structure_type == StructureType.REACTOR else
                1 if state.structures[st_id].structure_type == StructureType.BATTERY else
                2 if state.structures[st_id].structure_type == StructureType.ATTACK_NODE else
                3,
                state.structures[st_id].hp,  # lowest HP first
                st_id,
            ),
        )

        for target_id in priority_targets:
            if len(actions) >= 2:
                break
            if budget[0] >= 6:
                act = _make_attack_action(state, player_id, target_id)
                actions.append(act)
                budget[0] -= act.energy_cost

        # 3. Build attack nodes (1 per 2 sectors, at least 1)
        target_nodes = max(1, len(frontier_controlled) // 2)
        if attack_node_count < target_nodes and len(actions) < 3:
            for sid in frontier_controlled:
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.ATTACK_NODE) == 0:
                    _try_build(state, player_id, sid, StructureType.ATTACK_NODE, actions, budget)
                    break

        # 4. Expand toward enemy territory
        enemy_adjacent_expand = []
        neutral_expand = []
        for sid in expandable:
            sec = state.world.sectors[sid]
            if sec.controller_player_id and sec.controller_player_id != player_id:
                enemy_adjacent_expand.append(sid)
            else:
                neutral_expand.append(sid)

        for sid in enemy_adjacent_expand + neutral_expand:
            if len(actions) >= 3:
                break
            _try_build(state, player_id, sid, StructureType.TOWER, actions, budget)

        # 5. More extractors for sustained war economy
        if extractor_count >= 1 and len(actions) < 3:
            metal_buildable = _get_sectors_with_metal(state, buildable)
            for sid in metal_buildable:
                if len(actions) >= 3:
                    break
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.EXTRACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.EXTRACTOR, actions, budget)

        # 6. Build a reactor if energy is low
        reactor_count = len(_player_structures_of_type(state, player_id, StructureType.REACTOR))
        if reactor_count < 1 and frontier_controlled and len(actions) < 3:
            _try_build(state, player_id, frontier_controlled[0], StructureType.REACTOR, actions, budget)

        return _filter_affordable(state, player_id, actions)


# ---------------------------------------------------------------------------
# 5. TurtleStrategy — defend few sectors heavily, build tall not wide
# ---------------------------------------------------------------------------

class TurtleStrategy(Strategy):
    name = "turtle"

    def decide(self, state: GameState, player_id: str) -> list[Action]:
        actions: list[Action] = []
        budget = [_remaining_energy(state, player_id)]

        controlled = sorted(_get_player_controlled_sectors(state, player_id))
        safe = sorted(_get_player_safe_sectors(state, player_id))
        owned = sorted(set(controlled) | set(safe))
        buildable = _get_buildable_sectors(state, player_id)
        expandable = _get_expandable_frontier(state, player_id)
        frontier_controlled = [s for s in controlled if state.world.sectors[s].sector_type == SectorType.FRONTIER]

        extractor_count = len(_player_structures_of_type(state, player_id, StructureType.EXTRACTOR))
        max_frontier_sectors = 4  # Turtle limits expansion

        # 1. CRITICAL: Build extractors on metal nodes FIRST
        if extractor_count < 2:
            metal_buildable = _get_sectors_with_metal(state, buildable)
            for sid in metal_buildable:
                if len(actions) >= 1:
                    break
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.EXTRACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.EXTRACTOR, actions, budget)

        # 2. Expand to a few frontier sectors (up to max)
        if len(frontier_controlled) < max_frontier_sectors:
            for sid in expandable:
                if len(actions) >= 2:
                    break
                sec = state.world.sectors[sid]
                if sec.controller_player_id is None:
                    _try_build(state, player_id, sid, StructureType.TOWER, actions, budget)

        # 3. Fortify — stack towers in controlled sectors (up to 3 per sector)
        for sid in frontier_controlled:
            if len(actions) >= 3:
                break
            tower_count = _count_player_structures_in_sector(state, player_id, sid, StructureType.TOWER)
            if tower_count < 3:
                _try_build(state, player_id, sid, StructureType.TOWER, actions, budget)

        # 4. Build reactors (1 per 2 sectors)
        reactor_count = len(_player_structures_of_type(state, player_id, StructureType.REACTOR))
        target_reactors = max(1, len(frontier_controlled) // 2)
        if reactor_count < target_reactors and frontier_controlled and len(actions) < 3:
            for sid in frontier_controlled:
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.REACTOR) == 0:
                    _try_build(state, player_id, sid, StructureType.REACTOR, actions, budget)
                    break

        # 5. Batteries to store energy
        battery_count = len(_player_structures_of_type(state, player_id, StructureType.BATTERY))
        if battery_count < reactor_count and frontier_controlled and len(actions) < 3:
            for sid in frontier_controlled:
                if _count_player_structures_in_sector(state, player_id, sid, StructureType.BATTERY) == 0:
                    _try_build(state, player_id, sid, StructureType.BATTERY, actions, budget)
                    break

        # 6. Relays for throughput
        relay_count = len(_player_structures_of_type(state, player_id, StructureType.RELAY))
        if relay_count < 1 and frontier_controlled and len(actions) < 3:
            _try_build(state, player_id, frontier_controlled[0], StructureType.RELAY, actions, budget)

        # 7. Attack nodes for defense (1 per 3 sectors)
        attack_count = len(_player_structures_of_type(state, player_id, StructureType.ATTACK_NODE))
        if attack_count * 3 < len(frontier_controlled) and frontier_controlled and len(actions) < 3:
            _try_build(state, player_id, frontier_controlled[-1], StructureType.ATTACK_NODE, actions, budget)

        return _filter_affordable(state, player_id, actions)


ALL_STRATEGIES: dict[str, type[Strategy]] = {
    "random": RandomStrategy,
    "expansionist": ExpansionistStrategy,
    "economist": EconomistStrategy,
    "aggressor": AggressorStrategy,
    "turtle": TurtleStrategy,
}


def get_strategy(name: str):
    """Return a callable(state, player_id, rng) for the named strategy.

    This is the entry point used by autoplay.py / server.py.
    The rng argument is accepted for API compatibility but ignored
    (engine strategies use deterministic logic based on game state).
    """
    if name not in ALL_STRATEGIES:
        raise ValueError(f"Unknown strategy: '{name}'. Available: {', '.join(ALL_STRATEGIES)}")
    instance = ALL_STRATEGIES[name](seed=0) if name == "random" else ALL_STRATEGIES[name]()

    def _call(state: GameState, player_id: str, rng=None) -> list[Action]:
        return instance.decide(state, player_id)

    _call.name = name
    return _call
