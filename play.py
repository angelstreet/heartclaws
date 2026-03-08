#!/usr/bin/env python3
"""HeartClaws — CLI interactive game runner.

Play as P1 against a simple AI opponent (P2) in the terminal.
Run with: python3 play.py
"""

from __future__ import annotations

import os
import random
import sys

# Ensure the engine package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.actions import get_action_energy_cost
from engine.config import (
    ACTION_ENERGY_COSTS,
    BUILD_ENERGY_COSTS,
    STRUCTURE_CATALOG,
)
from engine.control import get_player_controlled_sectors
from engine.energy import compute_player_available_energy, compute_player_income, compute_player_upkeep
from engine.engine import init_game, run_heartbeat, save_game, submit_action
from engine.enums import ActionType, SectorType, StructureType
from engine.models import Action, next_id

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
GREEN = "\033[32m"
RED = "\033[31m"
GRAY = "\033[90m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"

P1 = "p1"
P2 = "p2"

SAVE_PATH = "heartclaws_save.json"

# Map layout (3 rows x 4 cols):
#   S1 -- F1 -- F2 -- F3
#   S2 -- F4 -- F5 -- F6
#   S3 -- F7 -- F8 -- F9
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
# Display helpers
# ---------------------------------------------------------------------------

def colour_tag(controller: str | None) -> str:
    if controller == P1:
        return f"{GREEN}P1{RESET}"
    elif controller == P2:
        return f"{RED}P2{RESET}"
    return f"{GRAY}--{RESET}"


def render_map(state) -> str:
    lines: list[str] = []
    lines.append(f"\n{BOLD}=== Map (Heartbeat {state.heartbeat}) ==={RESET}\n")

    for r, row in enumerate(MAP_ROWS):
        # Sector cells
        cell_strs: list[str] = []
        for sid in row:
            sector = state.world.sectors[sid]
            tag = colour_tag(sector.controller_player_id)
            # Count structures
            structs = [state.structures[s] for s in sector.structure_ids if s in state.structures]
            struct_summary = ""
            if structs:
                counts: dict[str, int] = {}
                for s in structs:
                    short = s.structure_type.value[:3]
                    counts[short] = counts.get(short, 0) + 1
                struct_summary = " " + ",".join(f"{v}{k}" for k, v in counts.items())
            # Resource indicator
            res = ""
            for node in sector.resource_nodes:
                if not node.depleted:
                    res = f"{YELLOW}*{RESET}"
            cell_strs.append(f"{sid}[{tag}]{res}{struct_summary}")

        lines.append("   " + " --- ".join(cell_strs))

        # Vertical connectors (except after last row)
        if r < len(MAP_ROWS) - 1:
            connectors = "   " + "       ".join(["|"] * len(row))
            lines.append(connectors)

    lines.append("")
    return "\n".join(lines)


def show_resources(state, player_id: str) -> str:
    p = state.players[player_id]
    income = compute_player_income(state, player_id)
    upkeep = compute_player_upkeep(state, player_id)
    available = compute_player_available_energy(state, player_id) - p.energy_spent_this_heartbeat
    controlled = get_player_controlled_sectors(state, player_id)
    label = f"{GREEN}You (P1){RESET}" if player_id == P1 else f"{RED}AI (P2){RESET}"
    return (
        f"  {label}: "
        f"Energy={p.energy_reserve} (income {income}, upkeep {upkeep}, avail {available})  "
        f"Metal={p.metal}  Data={p.data}  Biomass={p.biomass}  "
        f"Sectors={len(controlled)}"
    )


def show_status(state):
    print(render_map(state))
    print(f"{BOLD}--- Resources ---{RESET}")
    print(show_resources(state, P1))
    print(show_resources(state, P2))
    print()


def show_detailed_status(state):
    show_status(state)
    print(f"{BOLD}--- Structures ---{RESET}")
    for sid, sector in state.world.sectors.items():
        for st_id in sector.structure_ids:
            st = state.structures.get(st_id)
            if st is None:
                continue
            owner = f"{GREEN}P1{RESET}" if st.owner_player_id == P1 else f"{RED}P2{RESET}"
            active = "ON" if st.active else "OFF"
            print(
                f"  [{owner}] {st.structure_type.value:16s} in {sid:3s}  "
                f"HP={st.hp}/{st.max_hp}  {active}  id={st.structure_id}"
            )
    print()


# ---------------------------------------------------------------------------
# Player input helpers
# ---------------------------------------------------------------------------

def pick_number(prompt: str, lo: int, hi: int) -> int | None:
    """Return an int in [lo, hi] or None on cancel."""
    while True:
        raw = input(prompt).strip()
        if raw.lower() in ("q", "c", "cancel", ""):
            return None
        try:
            n = int(raw)
            if lo <= n <= hi:
                return n
        except ValueError:
            pass
        print(f"  Enter a number {lo}-{hi}, or press Enter to cancel.")


def get_buildable_sectors(state, player_id: str) -> list[str]:
    """Sectors where the player can build: controlled + uncontrolled frontier adjacent to controlled."""
    controlled = set(get_player_controlled_sectors(state, player_id))
    buildable: set[str] = set()
    for sid in controlled:
        sector = state.world.sectors[sid]
        # Can build in controlled frontier sectors
        if sector.sector_type == SectorType.FRONTIER:
            buildable.add(sid)
        # Adjacent uncontrolled frontier sectors
        for adj_id in sector.adjacent_sector_ids:
            adj = state.world.sectors[adj_id]
            if adj.sector_type == SectorType.FRONTIER and adj.controller_player_id is None:
                buildable.add(adj_id)
    return sorted(buildable)


def handle_build(state) -> Action | None:
    player = state.players[P1]
    sectors = get_buildable_sectors(state, P1)
    if not sectors:
        print("  No sectors available to build in.")
        return None

    print(f"\n  {BOLD}Buildable sectors:{RESET}")
    for i, sid in enumerate(sectors, 1):
        sec = state.world.sectors[sid]
        ctrl = colour_tag(sec.controller_player_id)
        res_str = ""
        for node in sec.resource_nodes:
            if not node.depleted:
                res_str = f" {YELLOW}(metal node){RESET}"
        print(f"    {i}) {sid} [{ctrl}]{res_str}")

    choice = pick_number("  Pick sector: ", 1, len(sectors))
    if choice is None:
        return None
    sector_id = sectors[choice - 1]

    print(f"\n  {BOLD}Structure types:{RESET}")
    affordable: list[tuple[int, StructureType]] = []
    for i, st in enumerate(BUILDABLE_TYPES, 1):
        cat = STRUCTURE_CATALOG[st]
        e_cost = BUILD_ENERGY_COSTS.get(st, 0)
        can = (
            player.metal >= cat["metal_cost"]
            and player.data >= cat["data_cost"]
            and player.biomass >= cat["biomass_cost"]
        )
        mark = "" if can else f" {GRAY}(insufficient){RESET}"
        print(
            f"    {i}) {st.value:16s}  "
            f"metal={cat['metal_cost']} data={cat['data_cost']} energy={e_cost} "
            f"HP={cat['hp']} inf={cat['influence']}{mark}"
        )
        affordable.append((i, st))

    choice2 = pick_number("  Pick structure type: ", 1, len(BUILDABLE_TYPES))
    if choice2 is None:
        return None
    structure_type = BUILDABLE_TYPES[choice2 - 1]

    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=P1,
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


def handle_attack(state) -> Action | None:
    # Find enemy structures in frontier sectors
    targets: list[tuple[str, str]] = []  # (structure_id, description)
    for st_id, st in state.structures.items():
        if st.owner_player_id != P1:
            sector = state.world.sectors.get(st.sector_id)
            if sector and sector.sector_type == SectorType.FRONTIER:
                targets.append((
                    st_id,
                    f"{st.structure_type.value} in {st.sector_id} HP={st.hp}/{st.max_hp}"
                ))

    if not targets:
        print("  No enemy structures to attack.")
        return None

    print(f"\n  {BOLD}Enemy structures:{RESET}")
    for i, (sid, desc) in enumerate(targets, 1):
        print(f"    {i}) {desc}  (id={sid})")

    cost = ACTION_ENERGY_COSTS[ActionType.ATTACK_STRUCTURE]
    print(f"  Energy cost: {cost}")

    choice = pick_number("  Pick target: ", 1, len(targets))
    if choice is None:
        return None

    target_id = targets[choice - 1][0]
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=P1,
        issuer_subagent_id=None,
        action_type=ActionType.ATTACK_STRUCTURE,
        payload={"target_structure_id": target_id},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def handle_scan(state) -> Action | None:
    controlled = set(get_player_controlled_sectors(state, P1))
    scannable: set[str] = set(controlled)
    for sid in controlled:
        for adj_id in state.world.sectors[sid].adjacent_sector_ids:
            scannable.add(adj_id)

    scannable_sorted = sorted(scannable)
    print(f"\n  {BOLD}Scannable sectors:{RESET}")
    for i, sid in enumerate(scannable_sorted, 1):
        sec = state.world.sectors[sid]
        ctrl = colour_tag(sec.controller_player_id)
        print(f"    {i}) {sid} [{ctrl}]")

    cost = ACTION_ENERGY_COSTS[ActionType.SCAN_SECTOR]
    print(f"  Energy cost: {cost}")

    choice = pick_number("  Pick sector: ", 1, len(scannable_sorted))
    if choice is None:
        return None

    sector_id = scannable_sorted[choice - 1]
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=P1,
        issuer_subagent_id=None,
        action_type=ActionType.SCAN_SECTOR,
        payload={"sector_id": sector_id},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def handle_create_subagent(state) -> Action | None:
    cost = ACTION_ENERGY_COSTS[ActionType.CREATE_SUBAGENT]
    print(f"  Energy cost: {cost}, Data cost: 2")
    name = input("  Subagent name (or Enter to cancel): ").strip()
    if not name:
        return None

    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=P1,
        issuer_subagent_id=None,
        action_type=ActionType.CREATE_SUBAGENT,
        payload={"name": name},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


def handle_remove(state) -> Action | None:
    own: list[tuple[str, str]] = []
    for st_id, st in state.structures.items():
        if st.owner_player_id == P1 and st.structure_type != StructureType.SANCTUARY_CORE:
            own.append((
                st_id,
                f"{st.structure_type.value} in {st.sector_id} HP={st.hp}/{st.max_hp}"
            ))

    if not own:
        print("  No removable structures.")
        return None

    print(f"\n  {BOLD}Your structures:{RESET}")
    for i, (sid, desc) in enumerate(own, 1):
        print(f"    {i}) {desc}  (id={sid})")

    cost = ACTION_ENERGY_COSTS[ActionType.REMOVE_STRUCTURE]
    print(f"  Energy cost: {cost}  (50% metal refund)")

    choice = pick_number("  Pick structure to remove: ", 1, len(own))
    if choice is None:
        return None

    structure_id = own[choice - 1][0]
    action = Action(
        action_id=next_id(state, "act"),
        issuer_player_id=P1,
        issuer_subagent_id=None,
        action_type=ActionType.REMOVE_STRUCTURE,
        payload={"structure_id": structure_id},
        submitted_heartbeat=state.heartbeat,
    )
    action.energy_cost = get_action_energy_cost(action)
    return action


# ---------------------------------------------------------------------------
# AI opponent (P2) — simple random strategy
# ---------------------------------------------------------------------------

def ai_turn(state) -> list[Action]:
    """Generate simple AI actions for P2."""
    actions: list[Action] = []
    player = state.players[P2]

    if not player.alive:
        return actions

    buildable = get_buildable_sectors(state, P2)
    p2_controlled = set(get_player_controlled_sectors(state, P2))

    # Sort buildable sectors: prefer ones adjacent to P2 sanctuary (closer first)
    # Simple heuristic: sectors adjacent to controlled ones that are uncontrolled
    frontier_expand = [
        s for s in buildable
        if state.world.sectors[s].controller_player_id is None
    ]
    frontier_owned = [
        s for s in buildable
        if state.world.sectors[s].controller_player_id == P2
    ]

    # Try to build if we have enough metal
    built = False
    if player.metal >= 5:
        # Prefer expanding with towers to uncontrolled sectors
        if frontier_expand and player.metal >= 5:
            sector_id = random.choice(frontier_expand)
            action = Action(
                action_id=next_id(state, "act"),
                issuer_player_id=P2,
                issuer_subagent_id=None,
                action_type=ActionType.BUILD_STRUCTURE,
                payload={
                    "sector_id": sector_id,
                    "structure_type": StructureType.TOWER.value,
                },
                submitted_heartbeat=state.heartbeat,
            )
            action.energy_cost = get_action_energy_cost(action)
            actions.append(action)
            built = True

        # If metal allows, also build an extractor in a sector with a metal node
        elif frontier_owned and player.metal >= 6:
            metal_sectors = [
                s for s in frontier_owned
                if any(
                    not n.depleted and n.resource_type.value == "METAL"
                    for n in state.world.sectors[s].resource_nodes
                )
                and not any(
                    state.structures.get(st_id) is not None
                    and state.structures[st_id].structure_type == StructureType.EXTRACTOR
                    for st_id in state.world.sectors[s].structure_ids
                )
            ]
            if metal_sectors:
                sector_id = random.choice(metal_sectors)
                action = Action(
                    action_id=next_id(state, "act"),
                    issuer_player_id=P2,
                    issuer_subagent_id=None,
                    action_type=ActionType.BUILD_STRUCTURE,
                    payload={
                        "sector_id": sector_id,
                        "structure_type": StructureType.EXTRACTOR.value,
                    },
                    submitted_heartbeat=state.heartbeat,
                )
                action.energy_cost = get_action_energy_cost(action)
                actions.append(action)
                built = True

    # If didn't build, scan a random reachable sector
    if not built:
        scannable: set[str] = set(p2_controlled)
        for sid in p2_controlled:
            for adj_id in state.world.sectors[sid].adjacent_sector_ids:
                scannable.add(adj_id)
        if scannable:
            sector_id = random.choice(sorted(scannable))
            action = Action(
                action_id=next_id(state, "act"),
                issuer_player_id=P2,
                issuer_subagent_id=None,
                action_type=ActionType.SCAN_SECTOR,
                payload={"sector_id": sector_id},
                submitted_heartbeat=state.heartbeat,
            )
            action.energy_cost = get_action_energy_cost(action)
            actions.append(action)

    return actions


# ---------------------------------------------------------------------------
# Event display
# ---------------------------------------------------------------------------

def summarize_events(result) -> None:
    """Print a human-readable summary of heartbeat events."""
    interesting = [
        e for e in result.events
        if e.event_type not in ("HEARTBEAT_STARTED", "HEARTBEAT_COMPLETED", "ENERGY_COMPUTED")
    ]

    if not interesting:
        print(f"  {GRAY}(no notable events){RESET}")
        return

    for ev in interesting:
        actor = ev.actor_player_id or "?"
        actor_col = GREEN if actor == P1 else RED if actor == P2 else ""
        actor_label = f"{actor_col}{actor}{RESET}"

        if ev.event_type == "ACTION_RESOLVED":
            details = ev.details
            atype = details.get("action_type", "?")
            payload = details.get("payload", {})

            if atype == "BUILD_STRUCTURE":
                st = payload.get("structure_type", "?")
                sec = payload.get("sector_id", "?")
                print(f"  {actor_label} built {CYAN}{st}{RESET} in {sec}")
            elif atype == "ATTACK_STRUCTURE":
                tid = payload.get("target_structure_id", "?")
                print(f"  {actor_label} attacked structure {tid}")
            elif atype == "SCAN_SECTOR":
                sec = payload.get("sector_id", "?")
                print(f"  {actor_label} scanned {sec}")
            elif atype == "REMOVE_STRUCTURE":
                sid = payload.get("structure_id", "?")
                print(f"  {actor_label} removed structure {sid}")
            elif atype == "CREATE_SUBAGENT":
                name = payload.get("name", "?")
                print(f"  {actor_label} created subagent '{name}'")
            else:
                print(f"  {actor_label} performed {atype}")

        elif ev.event_type == "ACTION_FAILED":
            details = ev.details
            reason = details.get("reason", "unknown")
            atype = details.get("action_type", "?")
            print(f"  {GRAY}{actor_label} action {atype} FAILED: {reason}{RESET}")
        else:
            print(f"  {GRAY}[{ev.event_type}] {ev.details}{RESET}")


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------

def main():
    print(f"\n{BOLD}{CYAN}========================================{RESET}")
    print(f"{BOLD}{CYAN}     HeartClaws — Terminal Edition{RESET}")
    print(f"{BOLD}{CYAN}========================================{RESET}")
    print(f"  You are {GREEN}P1{RESET} (top-left sanctuary)")
    print(f"  AI is   {RED}P2{RESET} (middle-left sanctuary)")
    print(f"  Destroy the enemy Sanctuary Core to win!\n")

    seed = random.randint(1, 999999)
    state = init_game(config=None, players=[P1, P2], seed=seed)

    # Queued actions for the current turn
    queued: list[Action] = []

    while True:
        # Check win/loss
        p1_alive = state.players[P1].alive
        p2_alive = state.players[P2].alive
        p1_core = state.structures.get(state.players[P1].sanctuary_core_structure_id or "")
        p2_core = state.structures.get(state.players[P2].sanctuary_core_structure_id or "")

        if p2_core is None or (p2_core and p2_core.hp <= 0):
            show_status(state)
            print(f"{BOLD}{GREEN}*** YOU WIN! Enemy Sanctuary Core destroyed! ***{RESET}\n")
            break
        if p1_core is None or (p1_core and p1_core.hp <= 0):
            show_status(state)
            print(f"{BOLD}{RED}*** YOU LOSE! Your Sanctuary Core was destroyed! ***{RESET}\n")
            break

        show_status(state)

        if queued:
            print(f"  {YELLOW}Queued actions this turn: {len(queued)}{RESET}")

        print(f"{BOLD}Actions:{RESET}")
        print("  1) Build structure")
        print("  2) Attack structure")
        print("  3) Scan sector")
        print("  4) Create subagent")
        print("  5) Remove structure")
        print(f"  6) End turn (run heartbeat)")
        print("  7) Save & quit")
        print("  8) Show detailed status")
        print()

        choice = pick_number("Choose action [1-8]: ", 1, 8)
        if choice is None:
            continue

        action: Action | None = None

        if choice == 1:
            action = handle_build(state)
        elif choice == 2:
            action = handle_attack(state)
        elif choice == 3:
            action = handle_scan(state)
        elif choice == 4:
            action = handle_create_subagent(state)
        elif choice == 5:
            action = handle_remove(state)
        elif choice == 6:
            # Submit all queued actions
            for a in queued:
                result = submit_action(state, a)
                if not result.accepted:
                    print(f"  {YELLOW}Action rejected: {result.reason}{RESET}")

            # AI turn
            ai_actions = ai_turn(state)
            for a in ai_actions:
                submit_action(state, a)

            # Run heartbeat
            hb_result = run_heartbeat(state)
            print(f"\n{BOLD}--- Heartbeat {hb_result.heartbeat} Results ---{RESET}")
            summarize_events(hb_result)
            print()

            queued.clear()
            continue
        elif choice == 7:
            save_game(state, SAVE_PATH)
            print(f"  Game saved to {SAVE_PATH}")
            break
        elif choice == 8:
            show_detailed_status(state)
            continue

        if action is not None:
            # Validate immediately for quick feedback
            from engine.actions import validate_action
            vr = validate_action(state, action)
            if not vr.accepted:
                print(f"  {RED}Invalid: {vr.reason}{RESET}")
            else:
                queued.append(action)
                print(f"  {GREEN}Action queued.{RESET}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print(f"\n{GRAY}Game interrupted. Goodbye!{RESET}")
