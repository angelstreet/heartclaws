"""Phase OW-2/OW-4: Dynamic Join/Leave and Diplomacy for Open World."""

from __future__ import annotations

from .config import GameConfig, STRUCTURE_CATALOG
from .enums import DiplomaticStance, SectorType, StructureType
from .events import emit_event
from .models import GameState, Message, PlayerState, StructureState, next_id
from .world import create_open_world, _hex_distance, _sector_id, GRID_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sector_coords(sector_id: str) -> tuple[int, int]:
    """Extract (q, r) coordinates from a sector_id like 'H_3_5'."""
    parts = sector_id.split("_")
    return int(parts[1]), int(parts[2])


def _find_spawn_sector(state: GameState) -> str:
    """Find the best unoccupied spawn sector.

    Priority:
    1. Unoccupied HAVEN furthest from all existing players' sanctuaries
    2. If all 8 HAVENs occupied: SETTLED sector furthest from any sanctuary core
    """
    world = state.world

    # Collect existing sanctuary coords
    sanctuary_coords: list[tuple[int, int]] = []
    for player in state.players.values():
        sid = player.sanctuary_sector_id
        sanctuary_coords.append(_sector_coords(sid))

    # Find all HAVEN sectors
    haven_sectors = [
        sid for sid, sec in world.sectors.items()
        if sec.sector_type == SectorType.HAVEN
    ]

    # Occupied HAVENs = sectors where a player has their sanctuary
    occupied_sanctuaries = {p.sanctuary_sector_id for p in state.players.values()}

    unoccupied_havens = [sid for sid in haven_sectors if sid not in occupied_sanctuaries]

    if unoccupied_havens:
        if not sanctuary_coords:
            # No players yet, just pick the first haven
            return unoccupied_havens[0]

        # Pick HAVEN furthest from all existing sanctuaries
        best_sid = None
        best_min_dist = -1
        for sid in unoccupied_havens:
            q, r = _sector_coords(sid)
            min_dist = min(
                _hex_distance(q, r, sq, sr) for sq, sr in sanctuary_coords
            )
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_sid = sid
        return best_sid  # type: ignore[return-value]

    # All HAVENs taken — fallback to SETTLED
    settled_sectors = [
        sid for sid, sec in world.sectors.items()
        if sec.sector_type == SectorType.SETTLED and sid not in occupied_sanctuaries
    ]

    if not settled_sectors:
        # Extreme fallback: any unoccupied sector
        settled_sectors = [
            sid for sid in world.sectors
            if sid not in occupied_sanctuaries
        ]

    best_sid = None
    best_min_dist = -1
    for sid in settled_sectors:
        q, r = _sector_coords(sid)
        if sanctuary_coords:
            min_dist = min(
                _hex_distance(q, r, sq, sr) for sq, sr in sanctuary_coords
            )
        else:
            min_dist = 0
        if min_dist > best_min_dist:
            best_min_dist = min_dist
            best_sid = sid

    return best_sid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_open_world(seed: int) -> GameState:
    """Create an open world GameState. No players yet -- they join dynamically."""
    world = create_open_world(seed)
    return GameState(
        game_id=f"openworld_{seed}",
        heartbeat=0,
        seed=seed,
        config=GameConfig(),
        world=world,
        players={},
        subagents={},
        structures={},
        actions_pending=[],
        event_log=[],
        id_counter=0,
        open_world=True,
        player_counter=0,
    )


def join_open_world(state: GameState, name: str, gateway_id: str | None = None) -> dict:
    """Player joins the open world.

    1. Find unoccupied HAVEN (or SETTLED fallback)
    2. Assign player_id (p1, p2, ...)
    3. Create Sanctuary Core in chosen sector
    4. Set spawn_heartbeat, starting resources
    5. HAVEN gets 10-heartbeat grace period
    """
    # Increment player counter and assign ID
    state.player_counter += 1
    player_id = f"p{state.player_counter}"

    # Find spawn sector
    sector_id = _find_spawn_sector(state)
    sector = state.world.sectors[sector_id]

    # Create Sanctuary Core structure
    catalog = STRUCTURE_CATALOG[StructureType.SANCTUARY_CORE]
    st_id = next_id(state, "st")
    structure = StructureState(
        structure_id=st_id,
        owner_player_id=player_id,
        sector_id=sector_id,
        structure_type=StructureType.SANCTUARY_CORE,
        hp=catalog["hp"],
        max_hp=catalog["hp"],
        active=True,
        activation_heartbeat=state.heartbeat,
        influence=catalog["influence"],
        energy_income_bonus=catalog["energy_income_bonus"],
        reserve_cap_bonus=catalog["reserve_cap_bonus"],
        throughput_cap_bonus=catalog["throughput_cap_bonus"],
        upkeep_cost=catalog["upkeep"],
        metal_cost=catalog["metal_cost"],
        data_cost=catalog["data_cost"],
        biomass_cost=catalog["biomass_cost"],
    )
    state.structures[st_id] = structure
    sector.structure_ids.append(st_id)
    sector.controller_player_id = player_id

    # Starting resources
    starting_metal = 20
    starting_data = 5
    starting_biomass = 5

    # Create player state
    player = PlayerState(
        player_id=player_id,
        name=name,
        alive=True,
        sanctuary_sector_id=sector_id,
        sanctuary_core_structure_id=st_id,
        metal=starting_metal,
        data=starting_data,
        biomass=starting_biomass,
        spawn_heartbeat=state.heartbeat,
        last_active_heartbeat=state.heartbeat,
        gateway_id=gateway_id,
    )
    state.players[player_id] = player

    # Grace period: 10 heartbeats (only for HAVEN spawns)
    grace_expires = state.heartbeat + 10 if sector.sector_type == SectorType.HAVEN else state.heartbeat

    return {
        "player_id": player_id,
        "sector_id": sector_id,
        "spawn_heartbeat": state.heartbeat,
        "grace_expires": grace_expires,
        "resources": {"metal": starting_metal, "data": starting_data, "biomass": starting_biomass},
    }


def leave_open_world(state: GameState, player_id: str) -> dict:
    """Graceful leave.

    - Set all player structures to 50% HP
    - Set owner to None (structures become neutral ruins)
    - Remove player from state.players
    - Return summary of what was abandoned
    """
    player = state.players.get(player_id)
    if player is None:
        return {"error": "Player not found"}

    abandoned_structures = []
    for st_id, structure in list(state.structures.items()):
        if structure.owner_player_id == player_id:
            structure.hp = structure.max_hp // 2
            structure.owner_player_id = None  # type: ignore[assignment]
            abandoned_structures.append({
                "structure_id": st_id,
                "sector_id": structure.sector_id,
                "structure_type": structure.structure_type.value,
                "hp": structure.hp,
            })

    # Clear sector control for sectors this player controlled
    for sector in state.world.sectors.values():
        if sector.controller_player_id == player_id:
            sector.controller_player_id = None

    del state.players[player_id]

    return {
        "player_id": player_id,
        "abandoned_structures": abandoned_structures,
    }


INACTIVE_DECAY_THRESHOLD = 30  # heartbeats before structures start decaying
INACTIVE_CLEANUP_THRESHOLD = 25920  # heartbeats before full removal (~3 days at 10s)
# At 300s production heartbeats: use 864 instead


def apply_open_world_decay(state: GameState) -> list:
    """Called each heartbeat. Returns list of decay events.

    - If player has no actions for 30 consecutive heartbeats = inactive
    - Inactive structures: -2 HP/heartbeat
    - Structures at 0 HP are destroyed
    """
    decay_events: list[dict] = []

    # Find inactive players
    inactive_players = set()
    for pid, player in state.players.items():
        if state.heartbeat - player.last_active_heartbeat >= INACTIVE_DECAY_THRESHOLD:
            inactive_players.add(pid)

    # Apply decay to inactive players' structures
    destroyed_ids: list[str] = []
    for st_id, structure in list(state.structures.items()):
        owner = structure.owner_player_id
        if owner in inactive_players or owner is None:
            structure.hp -= 2
            decay_events.append({
                "type": "decay",
                "structure_id": st_id,
                "owner_player_id": owner,
                "sector_id": structure.sector_id,
                "new_hp": structure.hp,
            })
            if structure.hp <= 0:
                destroyed_ids.append(st_id)
                decay_events.append({
                    "type": "destroyed",
                    "structure_id": st_id,
                    "owner_player_id": owner,
                    "sector_id": structure.sector_id,
                })

    # Remove destroyed structures
    for st_id in destroyed_ids:
        structure = state.structures[st_id]
        sector = state.world.sectors.get(structure.sector_id)
        if sector and st_id in sector.structure_ids:
            sector.structure_ids.remove(st_id)
        del state.structures[st_id]

    return decay_events


def apply_inactive_cleanup(state: GameState) -> list[dict]:
    """Remove players inactive for 3+ days. Called each heartbeat.

    Triggers leave_open_world (structures become ruins at 50% HP) and emits
    cleanup event. Returns list of cleanup events.
    """
    cleanup_events: list[dict] = []
    players_to_remove: list[str] = []

    for pid, player in state.players.items():
        inactive_heartbeats = state.heartbeat - player.last_active_heartbeat
        if inactive_heartbeats >= INACTIVE_CLEANUP_THRESHOLD:
            players_to_remove.append(pid)

    for pid in players_to_remove:
        player = state.players[pid]
        result = leave_open_world(state, pid)
        cleanup_events.append({
            "type": "inactive_cleanup",
            "player_id": pid,
            "player_name": player.name,
            "inactive_heartbeats": state.heartbeat - player.last_active_heartbeat,
            "abandoned_structures": result.get("abandoned_structures", []),
        })

    return cleanup_events


# ---------------------------------------------------------------------------
# KPIs / World Stats
# ---------------------------------------------------------------------------


def compute_world_kpis(state: GameState) -> dict:
    """Compute key performance indicators for the open world."""
    total_players = state.player_counter  # all-time (includes removed)
    alive_players = {pid: p for pid, p in state.players.items() if p.alive}
    active_players = {
        pid: p for pid, p in alive_players.items()
        if state.heartbeat - p.last_active_heartbeat < INACTIVE_DECAY_THRESHOLD
    }
    inactive_players = {
        pid: p for pid, p in alive_players.items()
        if state.heartbeat - p.last_active_heartbeat >= INACTIVE_DECAY_THRESHOLD
    }

    # Structures
    total_structures = len(state.structures)
    structures_by_type: dict[str, int] = {}
    for st in state.structures.values():
        t = st.structure_type.value
        structures_by_type[t] = structures_by_type.get(t, 0) + 1

    # Sectors
    total_sectors = len(state.world.sectors)
    controlled_sectors = sum(
        1 for s in state.world.sectors.values() if s.controller_player_id is not None
    )
    unclaimed_sectors = total_sectors - controlled_sectors

    # Actions from event log
    total_actions = sum(
        1 for e in state.event_log if e.event_type in ("ACTION_RESOLVED", "ACTION_FAILED")
    )
    total_actions_resolved = sum(
        1 for e in state.event_log if e.event_type == "ACTION_RESOLVED"
    )
    total_actions_failed = sum(
        1 for e in state.event_log if e.event_type == "ACTION_FAILED"
    )

    # Messages
    total_messages = len(state.messages)

    # Per-player summary
    player_summaries = []
    for pid, p in alive_players.items():
        sectors_owned = sum(
            1 for s in state.world.sectors.values() if s.controller_player_id == pid
        )
        structures_owned = sum(
            1 for st in state.structures.values() if st.owner_player_id == pid
        )
        inactive_hb = state.heartbeat - p.last_active_heartbeat
        player_summaries.append({
            "player_id": pid,
            "name": p.name,
            "active": inactive_hb < INACTIVE_DECAY_THRESHOLD,
            "inactive_heartbeats": inactive_hb,
            "sectors": sectors_owned,
            "structures": structures_owned,
            "metal": p.metal,
            "data": p.data,
            "biomass": p.biomass,
            "spawn_heartbeat": p.spawn_heartbeat,
            "elo": state.player_elo.get(pid, 1200),
        })

    return {
        "heartbeat": state.heartbeat,
        "world_age_heartbeats": state.heartbeat,
        "total_players_alltime": total_players,
        "alive_players": len(alive_players),
        "active_players": len(active_players),
        "inactive_players": len(inactive_players),
        "total_structures": total_structures,
        "structures_by_type": structures_by_type,
        "total_sectors": total_sectors,
        "controlled_sectors": controlled_sectors,
        "unclaimed_sectors": unclaimed_sectors,
        "total_actions": total_actions,
        "actions_resolved": total_actions_resolved,
        "actions_failed": total_actions_failed,
        "total_messages": total_messages,
        "season": (state.heartbeat // 2000) + 1,
        "world_events_active": len(state.world_events_active),
        "players": player_summaries,
    }


# ---------------------------------------------------------------------------
# Phase OW-4: Diplomacy
# ---------------------------------------------------------------------------

def set_diplomatic_stance(
    state: GameState, player_id: str, target_player_id: str, stance: DiplomaticStance
) -> dict:
    """Set diplomatic stance toward another player.

    Returns event dict with old_stance and new_stance.
    If breaking ALLY stance, generates ALLIANCE_BROKEN event visible to all.
    """
    player = state.players[player_id]
    old_stance = player.diplomacy_stance.get(target_player_id, DiplomaticStance.NEUTRAL)
    player.diplomacy_stance[target_player_id] = stance

    result = {
        "player_id": player_id,
        "target_player_id": target_player_id,
        "old_stance": old_stance.value,
        "new_stance": stance.value,
    }

    # If breaking an ALLY stance, emit ALLIANCE_BROKEN event
    if old_stance == DiplomaticStance.ALLY and stance != DiplomaticStance.ALLY:
        emit_event(
            state,
            "ALLIANCE_BROKEN",
            actor_player_id=player_id,
            target_id=target_player_id,
            details={
                "old_stance": old_stance.value,
                "new_stance": stance.value,
            },
        )
        result["alliance_broken"] = True

    return result


def get_diplomatic_relations(state: GameState, player_id: str) -> dict:
    """Return all diplomatic stances for a player, plus who considers them ally/hostile."""
    player = state.players[player_id]
    stances = {k: v.value for k, v in player.diplomacy_stance.items()}

    # Find who considers this player ally or hostile
    allied_by: list[str] = []
    hostile_by: list[str] = []
    for pid, p in state.players.items():
        if pid == player_id:
            continue
        stance = p.diplomacy_stance.get(player_id)
        if stance == DiplomaticStance.ALLY:
            allied_by.append(pid)
        elif stance == DiplomaticStance.HOSTILE:
            hostile_by.append(pid)

    return {
        "player_id": player_id,
        "stances": stances,
        "allied_by": allied_by,
        "hostile_by": hostile_by,
    }


def are_mutual_allies(state: GameState, player_a: str, player_b: str) -> bool:
    """Check if two players are mutual allies (both set ALLY toward each other)."""
    pa = state.players.get(player_a)
    pb = state.players.get(player_b)
    if pa is None or pb is None:
        return False
    return (
        pa.diplomacy_stance.get(player_b) == DiplomaticStance.ALLY
        and pb.diplomacy_stance.get(player_a) == DiplomaticStance.ALLY
    )


# ---------------------------------------------------------------------------
# Phase OW-4: Messaging
# ---------------------------------------------------------------------------

def send_message(
    state: GameState, from_player_id: str, to_player_id: str, message: str
) -> dict:
    """Send a diplomatic message. Messages have no game effect -- pure information.

    Store messages in a list on GameState.
    Returns {"message_id": str, "from": str, "to": str, "heartbeat": int}
    """
    msg_id = next_id(state, "msg")
    msg = Message(
        message_id=msg_id,
        from_player_id=from_player_id,
        to_player_id=to_player_id,
        content=message,
        heartbeat=state.heartbeat,
    )
    state.messages.append(msg)
    return {
        "message_id": msg_id,
        "from": from_player_id,
        "to": to_player_id,
        "heartbeat": state.heartbeat,
    }


def get_messages(state: GameState, player_id: str) -> list[dict]:
    """Return all messages for a player (sent to them)."""
    return [
        {
            "message_id": m.message_id,
            "from": m.from_player_id,
            "to": m.to_player_id,
            "content": m.content,
            "heartbeat": m.heartbeat,
        }
        for m in state.messages
        if m.to_player_id == player_id
    ]
