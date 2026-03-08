"""Phase OW-2: Dynamic Join/Leave for Open World."""

from __future__ import annotations

from .config import GameConfig, STRUCTURE_CATALOG
from .enums import SectorType, StructureType
from .models import GameState, PlayerState, StructureState, next_id
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


def apply_open_world_decay(state: GameState) -> list:
    """Called each heartbeat. Returns list of decay events.

    - If player has no actions for 30 consecutive heartbeats = inactive
    - Inactive structures: -2 HP/heartbeat
    - Structures at 0 HP are destroyed
    """
    decay_events: list[dict] = []
    inactive_threshold = 30

    # Find inactive players
    inactive_players = set()
    for pid, player in state.players.items():
        if state.heartbeat - player.last_active_heartbeat >= inactive_threshold:
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
