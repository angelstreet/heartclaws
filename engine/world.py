from __future__ import annotations

from .config import GameConfig, STRUCTURE_CATALOG
from .enums import ResourceType, SectorType, StructureType
from .models import (
    PlayerState,
    ResourceNode,
    SectorState,
    StructureState,
    WorldState,
)

GRID_ADJACENCY: dict[str, list[str]] = {
    "S1": ["F1", "S2"],
    "F1": ["S1", "F2", "F4"],
    "F2": ["F1", "F3", "F5"],
    "F3": ["F2", "F6"],
    "S2": ["S1", "F4", "S3"],
    "F4": ["F1", "S2", "F5", "F7"],
    "F5": ["F2", "F4", "F6", "F8"],
    "F6": ["F3", "F5", "F9"],
    "S3": ["S2", "F7"],
    "F7": ["F4", "S3", "F8"],
    "F8": ["F7", "F5", "F9"],
    "F9": ["F6", "F8"],
}

METAL_NODE_SECTORS = {"F1", "F3", "F5", "F7", "F9"}


def create_default_world(
    config: GameConfig, players: list[str]
) -> tuple[WorldState, dict[str, PlayerState], dict[str, StructureState]]:
    safe_ids = ["S1", "S2", "S3"]
    frontier_ids = [f"F{i}" for i in range(1, 10)]

    world = WorldState(planet_id="planet_001")

    node_counter = 0
    for sid in safe_ids:
        owner = players[safe_ids.index(sid)] if safe_ids.index(sid) < len(players) else None
        sector = SectorState(
            sector_id=sid,
            name=sid,
            sector_type=SectorType.SAFE,
            adjacent_sector_ids=list(GRID_ADJACENCY[sid]),
            controller_player_id=owner,
            safe_owner_player_id=owner,
        )
        world.sectors[sid] = sector

    for sid in frontier_ids:
        nodes: list[ResourceNode] = []
        if sid in METAL_NODE_SECTORS:
            node_counter += 1
            nodes.append(
                ResourceNode(
                    node_id=f"node_{node_counter:03d}",
                    resource_type=ResourceType.METAL,
                    richness=5,
                )
            )
        sector = SectorState(
            sector_id=sid,
            name=sid,
            sector_type=SectorType.FRONTIER,
            adjacent_sector_ids=list(GRID_ADJACENCY[sid]),
            resource_nodes=nodes,
            controller_player_id=None,
        )
        world.sectors[sid] = sector

    player_states: dict[str, PlayerState] = {}
    structure_states: dict[str, StructureState] = {}
    struct_counter = 0

    catalog = STRUCTURE_CATALOG[StructureType.SANCTUARY_CORE]

    for i, pid in enumerate(players):
        safe_sid = safe_ids[i]
        struct_counter += 1
        st_id = f"st_{struct_counter:03d}"

        structure = StructureState(
            structure_id=st_id,
            owner_player_id=pid,
            sector_id=safe_sid,
            structure_type=StructureType.SANCTUARY_CORE,
            hp=catalog["hp"],
            max_hp=catalog["hp"],
            active=True,
            activation_heartbeat=0,
            influence=catalog["influence"],
            energy_income_bonus=catalog["energy_income_bonus"],
            reserve_cap_bonus=catalog["reserve_cap_bonus"],
            throughput_cap_bonus=catalog["throughput_cap_bonus"],
            upkeep_cost=catalog["upkeep"],
            metal_cost=catalog["metal_cost"],
            data_cost=catalog["data_cost"],
            biomass_cost=catalog["biomass_cost"],
        )
        structure_states[st_id] = structure
        world.sectors[safe_sid].structure_ids.append(st_id)

        player = PlayerState(
            player_id=pid,
            name=pid,
            alive=True,
            sanctuary_sector_id=safe_sid,
            sanctuary_core_structure_id=st_id,
            energy_reserve=0,
            energy_spent_this_heartbeat=0,
            metal=config.default_player_metal,
            data=config.default_player_data,
            biomass=config.default_player_biomass,
        )
        player_states[pid] = player

    return world, player_states, structure_states


def get_sector(world: WorldState, sector_id: str) -> SectorState:
    if sector_id not in world.sectors:
        raise KeyError(f"Sector '{sector_id}' not found")
    return world.sectors[sector_id]


def are_adjacent(world: WorldState, a: str, b: str) -> bool:
    sector = get_sector(world, a)
    return b in sector.adjacent_sector_ids


def get_adjacent_sectors(world: WorldState, sector_id: str) -> list[str]:
    sector = get_sector(world, sector_id)
    return list(sector.adjacent_sector_ids)
