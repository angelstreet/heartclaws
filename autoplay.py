#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.actions import get_action_energy_cost
from engine.config import STRUCTURE_CATALOG, BUILD_ENERGY_COSTS
from engine.control import get_player_controlled_sectors
from engine.energy import compute_player_available_energy, compute_player_income, compute_player_upkeep
from engine.engine import init_game, run_heartbeat, submit_action
from engine.enums import ActionType, SectorType, StructureType
from engine.models import Action, GameState, HeartbeatResult, next_id

try:
    from engine.strategies import get_strategy
except ImportError:
    get_strategy = None

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
GREEN = "\033[32m"
RED = "\033[31m"
GRAY = "\033[90m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
RESET = "\033[0m"

P1 = "p1"
P2 = "p2"

MAP_ROWS = [
    ["S1", "F1", "F2", "F3"],
    ["S2", "F4", "F5", "F6"],
    ["S3", "F7", "F8", "F9"],
]

BUILDABLE_TYPES = [
    StructureType.EXTRACTOR,
    StructureType.REACTOR,
    StructureType.BATTERY,
    StructureType.RELAY,
    StructureType.TOWER,
    StructureType.FACTORY,
    StructureType.ATTACK_NODE,
]


# ---------------------------------------------------------------------------
# Stats / Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PlayerStats:
    player_id: str
    strategy_name: str
    sectors_controlled: int = 0
    structures_built: int = 0
    structures_lost: int = 0
    attacks_made: int = 0
    total_energy_earned: int = 0
    final_metal: int = 0


@dataclass
class MatchResult:
    winner: str | None
    reason: str
    heartbeats: int
    final_state: GameState
    p1_stats: PlayerStats
    p2_stats: PlayerStats


# ---------------------------------------------------------------------------
# Built-in fallback strategies (used when engine.strategies is missing)
# ---------------------------------------------------------------------------

def _get_buildable_sectors(state: GameState, player_id: str) -> list[str]:
    controlled = set(get_player_controlled_sectors(state, player_id))
    buildable: set[str] = set()
    for sid in controlled:
        sector = state.world.sectors[sid]
        if sector.sector_type == SectorType.FRONTIER:
            buildable.add(sid)
        for adj_id in sector.adjacent_sector_ids:
            adj = state.world.sectors[adj_id]
            if adj.sector_type == SectorType.FRONTIER and adj.controller_player_id is None:
                buildable.add(adj_id)
    return sorted(buildable)


def _make_build_action(state: GameState, player_id: str, sector_id: str, stype: StructureType) -> Action:
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=player_id,
        issuer_subagent_id=None,
        action_type=ActionType.BUILD_STRUCTURE,
        payload={"sector_id": sector_id, "structure_type": stype.value},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def _make_attack_action(state: GameState, player_id: str, target_id: str) -> Action:
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=player_id,
        issuer_subagent_id=None,
        action_type=ActionType.ATTACK_STRUCTURE,
        payload={"target_structure_id": target_id},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def _can_afford_build(state: GameState, player_id: str, stype: StructureType) -> bool:
    p = state.players[player_id]
    cat = STRUCTURE_CATALOG[stype]
    if p.metal < cat["metal_cost"] or p.data < cat["data_cost"] or p.biomass < cat["biomass_cost"]:
        return False
    e_cost = BUILD_ENERGY_COSTS.get(stype, 0)
    avail = compute_player_available_energy(state, player_id) - p.energy_spent_this_heartbeat
    return avail >= e_cost


def _enemy_structures_in_frontier(state: GameState, player_id: str) -> list[str]:
    targets = []
    for st_id, st in state.structures.items():
        if st.owner_player_id != player_id:
            sector = state.world.sectors.get(st.sector_id)
            if sector and sector.sector_type == SectorType.FRONTIER:
                targets.append(st_id)
    return targets


def _has_attack_node_near(state: GameState, player_id: str, target_sector_id: str) -> bool:
    target_sector = state.world.sectors[target_sector_id]
    check = {target_sector_id}
    for adj_id in target_sector.adjacent_sector_ids:
        adj = state.world.sectors.get(adj_id)
        if adj and adj.controller_player_id == player_id:
            check.add(adj_id)
    for sid in check:
        sec = state.world.sectors[sid]
        for st_id in sec.structure_ids:
            s = state.structures.get(st_id)
            if s and s.owner_player_id == player_id and s.structure_type == StructureType.ATTACK_NODE and s.active:
                return True
    return False


# -- Strategy: random --
def strategy_random(state: GameState, player_id: str, rng: random.Random) -> list[Action]:
    actions: list[Action] = []
    p = state.players[player_id]
    if not p.alive:
        return actions
    buildable = _get_buildable_sectors(state, player_id)
    if buildable and p.metal >= 5:
        sector_id = rng.choice(buildable)
        affordable = [st for st in BUILDABLE_TYPES if _can_afford_build(state, player_id, st)]
        if affordable:
            stype = rng.choice(affordable)
            actions.append(_make_build_action(state, player_id, sector_id, stype))
    return actions


# -- Strategy: expansionist --
def strategy_expansionist(state: GameState, player_id: str, rng: random.Random) -> list[Action]:
    actions: list[Action] = []
    p = state.players[player_id]
    if not p.alive:
        return actions

    buildable = _get_buildable_sectors(state, player_id)
    uncontrolled = [s for s in buildable if state.world.sectors[s].controller_player_id is None]
    controlled = [s for s in buildable if state.world.sectors[s].controller_player_id == player_id]

    # Priority 1: expand with towers into uncontrolled sectors
    if uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER):
        sector_id = rng.choice(uncontrolled)
        actions.append(_make_build_action(state, player_id, sector_id, StructureType.TOWER))
    # Priority 2: build extractors on metal nodes
    elif controlled and _can_afford_build(state, player_id, StructureType.EXTRACTOR):
        metal_sectors = [
            s for s in controlled
            if any(not n.depleted for n in state.world.sectors[s].resource_nodes)
            and not any(
                state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                for st_id in state.world.sectors[s].structure_ids
            )
        ]
        if metal_sectors:
            actions.append(_make_build_action(state, player_id, rng.choice(metal_sectors), StructureType.EXTRACTOR))
        elif _can_afford_build(state, player_id, StructureType.REACTOR):
            actions.append(_make_build_action(state, player_id, rng.choice(controlled), StructureType.REACTOR))
    return actions


# -- Strategy: economist --
def strategy_economist(state: GameState, player_id: str, rng: random.Random) -> list[Action]:
    actions: list[Action] = []
    p = state.players[player_id]
    if not p.alive:
        return actions

    buildable = _get_buildable_sectors(state, player_id)
    uncontrolled = [s for s in buildable if state.world.sectors[s].controller_player_id is None]
    controlled = [s for s in buildable if state.world.sectors[s].controller_player_id == player_id]

    # Priority 1: grab one adjacent sector to get metal nodes
    if uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER):
        metal_adj = [s for s in uncontrolled if any(not n.depleted for n in state.world.sectors[s].resource_nodes)]
        if metal_adj:
            actions.append(_make_build_action(state, player_id, rng.choice(metal_adj), StructureType.TOWER))
            return actions

    # Priority 2: extractors on metal nodes
    if controlled and _can_afford_build(state, player_id, StructureType.EXTRACTOR):
        metal_sectors = [
            s for s in controlled
            if any(not n.depleted for n in state.world.sectors[s].resource_nodes)
            and not any(
                state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                for st_id in state.world.sectors[s].structure_ids
            )
        ]
        if metal_sectors:
            actions.append(_make_build_action(state, player_id, rng.choice(metal_sectors), StructureType.EXTRACTOR))
            return actions

    # Priority 3: reactors for energy
    if controlled and _can_afford_build(state, player_id, StructureType.REACTOR):
        actions.append(_make_build_action(state, player_id, rng.choice(controlled), StructureType.REACTOR))
        return actions

    # Priority 4: batteries for reserves
    if controlled and _can_afford_build(state, player_id, StructureType.BATTERY):
        actions.append(_make_build_action(state, player_id, rng.choice(controlled), StructureType.BATTERY))

    return actions


# -- Strategy: aggressor --
def strategy_aggressor(state: GameState, player_id: str, rng: random.Random) -> list[Action]:
    actions: list[Action] = []
    p = state.players[player_id]
    if not p.alive:
        return actions

    buildable = _get_buildable_sectors(state, player_id)
    uncontrolled = [s for s in buildable if state.world.sectors[s].controller_player_id is None]
    controlled = [s for s in buildable if state.world.sectors[s].controller_player_id == player_id]

    # Check if we can attack anything
    enemy_targets = _enemy_structures_in_frontier(state, player_id)
    attackable = [t for t in enemy_targets if _has_attack_node_near(state, player_id, state.structures[t].sector_id)]
    if attackable:
        avail = compute_player_available_energy(state, player_id) - p.energy_spent_this_heartbeat
        if avail >= 6:
            target = rng.choice(attackable)
            actions.append(_make_attack_action(state, player_id, target))
            return actions

    # Build attack nodes toward enemy
    if controlled and _can_afford_build(state, player_id, StructureType.ATTACK_NODE):
        # Prefer sectors adjacent to enemy structures
        enemy_sectors = {state.structures[t].sector_id for t in enemy_targets}
        adj_to_enemy = [
            s for s in controlled
            if any(adj in enemy_sectors for adj in state.world.sectors[s].adjacent_sector_ids)
            and not any(
                state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.ATTACK_NODE
                for st_id in state.world.sectors[s].structure_ids
            )
        ]
        if adj_to_enemy:
            actions.append(_make_build_action(state, player_id, rng.choice(adj_to_enemy), StructureType.ATTACK_NODE))
            return actions

    # Expand toward enemy with towers
    if uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER):
        actions.append(_make_build_action(state, player_id, rng.choice(uncontrolled), StructureType.TOWER))
    elif controlled and _can_afford_build(state, player_id, StructureType.EXTRACTOR):
        metal_sectors = [
            s for s in controlled
            if any(not n.depleted for n in state.world.sectors[s].resource_nodes)
            and not any(
                state.structures.get(st_id) is not None
                and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                for st_id in state.world.sectors[s].structure_ids
            )
        ]
        if metal_sectors:
            actions.append(_make_build_action(state, player_id, rng.choice(metal_sectors), StructureType.EXTRACTOR))

    return actions


# -- Strategy: turtle --
def strategy_turtle(state: GameState, player_id: str, rng: random.Random) -> list[Action]:
    actions: list[Action] = []
    p = state.players[player_id]
    if not p.alive:
        return actions

    buildable = _get_buildable_sectors(state, player_id)
    uncontrolled = [s for s in buildable if state.world.sectors[s].controller_player_id is None]
    controlled = [s for s in buildable if state.world.sectors[s].controller_player_id == player_id]

    # Grab one adjacent sector for resources, then fortify
    if not controlled and uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER):
        actions.append(_make_build_action(state, player_id, rng.choice(uncontrolled), StructureType.TOWER))
        return actions

    # Prioritize: extractor -> reactor -> battery -> relay -> tower (fortify controlled)
    build_order = [
        StructureType.EXTRACTOR,
        StructureType.REACTOR,
        StructureType.BATTERY,
        StructureType.RELAY,
    ]

    for stype in build_order:
        if controlled and _can_afford_build(state, player_id, stype):
            if stype == StructureType.EXTRACTOR:
                metal_sectors = [
                    s for s in controlled
                    if any(not n.depleted for n in state.world.sectors[s].resource_nodes)
                    and not any(
                        state.structures.get(st_id) is not None
                        and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                        for st_id in state.world.sectors[s].structure_ids
                    )
                ]
                if metal_sectors:
                    actions.append(_make_build_action(state, player_id, rng.choice(metal_sectors), stype))
                    return actions
                continue
            actions.append(_make_build_action(state, player_id, rng.choice(controlled), stype))
            return actions

    # Slowly expand if nothing else to do
    if uncontrolled and _can_afford_build(state, player_id, StructureType.TOWER):
        actions.append(_make_build_action(state, player_id, rng.choice(uncontrolled), StructureType.TOWER))

    return actions


BUILTIN_STRATEGIES: dict[str, callable] = {
    "random": strategy_random,
    "expansionist": strategy_expansionist,
    "economist": strategy_economist,
    "aggressor": strategy_aggressor,
    "turtle": strategy_turtle,
}


def resolve_strategy(name: str):
    if get_strategy is not None:
        try:
            return get_strategy(name)
        except (KeyError, ValueError):
            pass
    if name in BUILTIN_STRATEGIES:
        return BUILTIN_STRATEGIES[name]
    raise ValueError(f"Unknown strategy: '{name}'. Available: {', '.join(BUILTIN_STRATEGIES)}")


# ---------------------------------------------------------------------------
# Display helpers (reused from play.py style)
# ---------------------------------------------------------------------------

def colour_tag(controller: str | None) -> str:
    if controller == P1:
        return f"{GREEN}P1{RESET}"
    elif controller == P2:
        return f"{RED}P2{RESET}"
    return f"{GRAY}--{RESET}"


def render_map(state: GameState) -> str:
    lines: list[str] = []
    lines.append(f"\n{BOLD}=== Map (Heartbeat {state.heartbeat}) ==={RESET}\n")

    for r, row in enumerate(MAP_ROWS):
        cell_strs: list[str] = []
        for sid in row:
            sector = state.world.sectors[sid]
            tag = colour_tag(sector.controller_player_id)
            structs = [state.structures[s] for s in sector.structure_ids if s in state.structures]
            struct_summary = ""
            if structs:
                counts: dict[str, int] = {}
                for s in structs:
                    short = s.structure_type.value[:3]
                    counts[short] = counts.get(short, 0) + 1
                struct_summary = " " + ",".join(f"{v}{k}" for k, v in counts.items())
            res = ""
            for node in sector.resource_nodes:
                if not node.depleted:
                    res = f"{YELLOW}*{RESET}"
            cell_strs.append(f"{sid}[{tag}]{res}{struct_summary}")

        lines.append("   " + " --- ".join(cell_strs))
        if r < len(MAP_ROWS) - 1:
            connectors = "   " + "       ".join(["|"] * len(row))
            lines.append(connectors)

    lines.append("")
    return "\n".join(lines)


def show_resources(state: GameState, player_id: str, strategy_name: str) -> str:
    p = state.players[player_id]
    income = compute_player_income(state, player_id)
    upkeep = compute_player_upkeep(state, player_id)
    available = compute_player_available_energy(state, player_id) - p.energy_spent_this_heartbeat
    controlled = get_player_controlled_sectors(state, player_id)
    if player_id == P1:
        label = f"{GREEN}{strategy_name} (P1){RESET}"
    else:
        label = f"{RED}{strategy_name} (P2){RESET}"
    return (
        f"  {label}: "
        f"Energy={p.energy_reserve} (inc {income}, upk {upkeep}, avl {available})  "
        f"Metal={p.metal}  Data={p.data}  "
        f"Sectors={len(controlled)}"
    )


def summarize_events(events, p1_name: str, p2_name: str) -> list[str]:
    lines: list[str] = []
    interesting = [
        e for e in events
        if e.event_type not in ("HEARTBEAT_STARTED", "HEARTBEAT_COMPLETED", "ENERGY_COMPUTED")
    ]
    if not interesting:
        lines.append(f"  {GRAY}(quiet heartbeat){RESET}")
        return lines

    for ev in interesting:
        actor = ev.actor_player_id or "?"
        if actor == P1:
            actor_label = f"{GREEN}{p1_name}{RESET}"
        elif actor == P2:
            actor_label = f"{RED}{p2_name}{RESET}"
        else:
            actor_label = f"{GRAY}{actor}{RESET}"

        details = ev.details or {}

        if ev.event_type == "ACTION_RESOLVED":
            atype = details.get("action_type", "?")
            lines.append(f"  {actor_label} resolved {CYAN}{atype}{RESET}")

        elif ev.event_type == "ACTION_FAILED":
            atype = details.get("action_type", "?")
            reason = details.get("failure_reason") or details.get("reason", "unknown")
            lines.append(f"  {DIM}{actor_label} {atype} failed: {reason}{RESET}")

        elif ev.event_type == "STRUCTURE_BUILT":
            stype = details.get("structure_type", "?")
            sector = details.get("sector_id", "?")
            lines.append(f"  {actor_label} built {CYAN}{stype}{RESET} in {sector}")

        elif ev.event_type == "STRUCTURE_ATTACKED":
            dmg = details.get("damage", "?")
            hp = details.get("remaining_hp", "?")
            lines.append(f"  {actor_label} {MAGENTA}attacked{RESET} {ev.target_id} ({dmg} dmg, {hp} HP left)")

        elif ev.event_type == "STRUCTURE_DESTROYED":
            sector = details.get("sector_id", "?")
            lines.append(f"  {MAGENTA}DESTROYED{RESET} {ev.target_id} in {sector}")

        elif ev.event_type == "STRUCTURE_REMOVED":
            lines.append(f"  {actor_label} removed {ev.target_id}")

        elif ev.event_type == "SECTOR_CONTROL_CHANGED":
            old = details.get("old_controller") or "none"
            new = details.get("new_controller") or "none"
            lines.append(f"  {YELLOW}Sector {ev.target_id}{RESET} control: {old} -> {new}")

        elif ev.event_type == "UPKEEP_DEACTIVATION":
            ttype = details.get("target_type", "?")
            lines.append(f"  {DIM}{actor_label} deactivated {ttype} {ev.target_id} (upkeep){RESET}")

        else:
            pass  # skip noise

    return lines


# ---------------------------------------------------------------------------
# Stats collection
# ---------------------------------------------------------------------------

def _collect_stats(state: GameState, player_id: str, strategy_name: str, all_events: list) -> PlayerStats:
    stats = PlayerStats(player_id=player_id, strategy_name=strategy_name)
    stats.sectors_controlled = len(get_player_controlled_sectors(state, player_id))
    stats.final_metal = state.players[player_id].metal

    for ev in all_events:
        if ev.actor_player_id != player_id:
            # Check if this player lost a structure
            if ev.event_type == "STRUCTURE_DESTROYED":
                details = ev.details or {}
                owner = details.get("owner_player_id")
                if owner == player_id:
                    stats.structures_lost += 1
            continue

        if ev.event_type == "ACTION_RESOLVED":
            details = ev.details or {}
            atype = details.get("action_type", "")
            if atype == "BUILD_STRUCTURE":
                stats.structures_built += 1
            elif atype == "ATTACK_STRUCTURE":
                stats.attacks_made += 1
        elif ev.event_type == "ENERGY_COMPUTED":
            details = ev.details or {}
            stats.total_energy_earned += details.get("income", 0)

    # Count lost structures from attack events by enemy
    for ev in all_events:
        if ev.event_type == "ACTION_RESOLVED" and ev.actor_player_id != player_id:
            details = ev.details or {}
            if details.get("action_type") == "ATTACK_STRUCTURE":
                target_id = (details.get("payload") or {}).get("target_structure_id")
                if target_id and target_id not in state.structures:
                    # structure was destroyed -- may have been this player's
                    pass  # already counted above if STRUCTURE_DESTROYED event exists

    return stats


# ---------------------------------------------------------------------------
# Win condition checks
# ---------------------------------------------------------------------------

def _check_elimination(state: GameState) -> str | None:
    for pid, player in state.players.items():
        core_id = player.sanctuary_core_structure_id
        if core_id is None:
            continue
        core = state.structures.get(core_id)
        if core is None or core.hp <= 0:
            return pid
    return None


def _count_frontier_sectors(state: GameState, player_id: str) -> int:
    count = 0
    for sector in state.world.sectors.values():
        if sector.sector_type == SectorType.FRONTIER and sector.controller_player_id == player_id:
            count += 1
    return count


def _check_domination(state: GameState) -> str | None:
    for pid in state.players:
        if _count_frontier_sectors(state, pid) >= 7:
            return pid
    return None


def _check_timeout_winner(state: GameState) -> str | None:
    players = list(state.players.keys())
    if len(players) != 2:
        return None
    p1_sectors = _count_frontier_sectors(state, players[0])
    p2_sectors = _count_frontier_sectors(state, players[1])
    if p1_sectors > p2_sectors:
        return players[0]
    elif p2_sectors > p1_sectors:
        return players[1]
    # Tiebreak: most structures
    p1_structs = sum(1 for s in state.structures.values() if s.owner_player_id == players[0])
    p2_structs = sum(1 for s in state.structures.values() if s.owner_player_id == players[1])
    if p1_structs > p2_structs:
        return players[0]
    elif p2_structs > p1_structs:
        return players[1]
    return None  # true draw


# ---------------------------------------------------------------------------
# MatchRunner
# ---------------------------------------------------------------------------

class MatchRunner:
    def __init__(
        self,
        strategy_p1,
        strategy_p2,
        seed: int = 42,
        max_heartbeats: int = 30,
        p1_name: str = "p1_strat",
        p2_name: str = "p2_strat",
    ):
        self.strategy_p1 = strategy_p1
        self.strategy_p2 = strategy_p2
        self.seed = seed
        self.max_heartbeats = max_heartbeats
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.rng = random.Random(seed)
        self.state = init_game(config=None, players=[P1, P2], seed=seed)
        self.all_events: list = []
        self._finished = False
        self._result: MatchResult | None = None

    def step(self) -> HeartbeatResult:
        if self._finished:
            raise RuntimeError("Match already finished")

        # Both strategies submit actions
        p1_actions = self.strategy_p1(self.state, P1, self.rng)
        p2_actions = self.strategy_p2(self.state, P2, self.rng)

        for a in p1_actions:
            submit_action(self.state, a)
        for a in p2_actions:
            submit_action(self.state, a)

        result = run_heartbeat(self.state)
        self.all_events.extend(result.events)
        return result

    def _check_win(self) -> tuple[str | None, str] | None:
        eliminated = _check_elimination(self.state)
        if eliminated is not None:
            other = P2 if eliminated == P1 else P1
            return other, "elimination"

        dominator = _check_domination(self.state)
        if dominator is not None:
            return dominator, "domination"

        return None

    def run(self, verbose: bool = True) -> MatchResult:
        if verbose:
            print(f"\n{BOLD}{CYAN}{'=' * 56}{RESET}")
            print(f"{BOLD}{CYAN}  HeartClaws AutoPlay  --  {GREEN}{self.p1_name}{CYAN} vs {RED}{self.p2_name}{RESET}")
            print(f"{BOLD}{CYAN}  Seed: {self.seed}  |  Max heartbeats: {self.max_heartbeats}{RESET}")
            print(f"{BOLD}{CYAN}{'=' * 56}{RESET}")

        for hb in range(1, self.max_heartbeats + 1):
            hb_result = self.step()

            if verbose:
                print(render_map(self.state))
                print(f"{BOLD}--- Resources ---{RESET}")
                print(show_resources(self.state, P1, self.p1_name))
                print(show_resources(self.state, P2, self.p2_name))
                print(f"\n{BOLD}--- Events (HB {hb_result.heartbeat}) ---{RESET}")
                for line in summarize_events(hb_result.events, self.p1_name, self.p2_name):
                    print(line)
                print(f"\n{DIM}{'- ' * 28}{RESET}\n")

            win = self._check_win()
            if win is not None:
                winner, reason = win
                self._finished = True
                self._result = MatchResult(
                    winner=winner,
                    reason=reason,
                    heartbeats=hb,
                    final_state=self.state,
                    p1_stats=_collect_stats(self.state, P1, self.p1_name, self.all_events),
                    p2_stats=_collect_stats(self.state, P2, self.p2_name, self.all_events),
                )
                return self._result

        # Timeout
        self._finished = True
        winner = _check_timeout_winner(self.state)
        self._result = MatchResult(
            winner=winner,
            reason="timeout",
            heartbeats=self.max_heartbeats,
            final_state=self.state,
            p1_stats=_collect_stats(self.state, P1, self.p1_name, self.all_events),
            p2_stats=_collect_stats(self.state, P2, self.p2_name, self.all_events),
        )
        return self._result


# ---------------------------------------------------------------------------
# Result display
# ---------------------------------------------------------------------------

def print_result(result: MatchResult):
    print(f"\n{BOLD}{CYAN}{'=' * 56}{RESET}")
    print(f"{BOLD}{CYAN}  MATCH RESULT{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 56}{RESET}")

    if result.winner is None:
        print(f"\n  {BOLD}{YELLOW}DRAW{RESET} after {result.heartbeats} heartbeats ({result.reason})")
    else:
        winner_stats = result.p1_stats if result.winner == P1 else result.p2_stats
        colour = GREEN if result.winner == P1 else RED
        print(f"\n  {BOLD}{colour}{winner_stats.strategy_name} ({result.winner}) WINS{RESET}"
              f" by {BOLD}{result.reason}{RESET} at heartbeat {result.heartbeats}")

    print(f"\n  {BOLD}Player Stats:{RESET}")
    for stats, colour in [(result.p1_stats, GREEN), (result.p2_stats, RED)]:
        print(f"\n  {colour}{BOLD}{stats.strategy_name} ({stats.player_id}){RESET}")
        print(f"    Sectors controlled : {stats.sectors_controlled}")
        print(f"    Structures built   : {stats.structures_built}")
        print(f"    Structures lost    : {stats.structures_lost}")
        print(f"    Attacks made       : {stats.attacks_made}")
        print(f"    Total energy earned: {stats.total_energy_earned}")
        print(f"    Final metal        : {stats.final_metal}")

    print(f"\n{BOLD}{CYAN}{'=' * 56}{RESET}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HeartClaws AutoPlay -- pit two AI strategies against each other")
    parser.add_argument("--p1", default="expansionist", help="P1 strategy name (default: expansionist)")
    parser.add_argument("--p2", default="economist", help="P2 strategy name (default: economist)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--heartbeats", type=int, default=30, help="Max heartbeats (default: 30)")
    parser.add_argument("--quiet", action="store_true", help="Only show final result")
    args = parser.parse_args()

    try:
        strat_p1 = resolve_strategy(args.p1)
        strat_p2 = resolve_strategy(args.p2)
    except ValueError as e:
        print(f"{RED}Error: {e}{RESET}")
        sys.exit(1)

    runner = MatchRunner(
        strategy_p1=strat_p1,
        strategy_p2=strat_p2,
        seed=args.seed,
        max_heartbeats=args.heartbeats,
        p1_name=args.p1,
        p2_name=args.p2,
    )

    result = runner.run(verbose=not args.quiet)
    print_result(result)


if __name__ == "__main__":
    main()
