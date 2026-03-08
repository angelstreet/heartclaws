from __future__ import annotations

import math
import random

from .config import GameConfig, STRUCTURE_CATALOG
from .enums import BiomeType, ResourceType, SectorType, StructureType
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


# ---------------------------------------------------------------------------
# Open World — 8x8 hex grid map generator (Phase OW-1)
# ---------------------------------------------------------------------------

GRID_SIZE = 8

# Biome resource definitions: (primary_type, primary_richness, secondary_type, secondary_richness)
BIOME_RESOURCES: dict[BiomeType, list[tuple[ResourceType, int]]] = {
    BiomeType.IRONLANDS: [(ResourceType.METAL, 8)],
    BiomeType.DATAFIELDS: [(ResourceType.DATA, 5), (ResourceType.METAL, 2)],
    BiomeType.GROVELANDS: [(ResourceType.BIOMASS, 5), (ResourceType.DATA, 2)],
    BiomeType.BARRENS: [(ResourceType.METAL, 3)],
    BiomeType.NEXUS: [(ResourceType.METAL, 3), (ResourceType.DATA, 3), (ResourceType.BIOMASS, 3)],
}


def _hex_neighbors(q: int, r: int) -> list[tuple[int, int]]:
    """Return the 6 hex neighbors of (q, r) in offset coordinates."""
    return [(q + 1, r), (q - 1, r), (q, r + 1), (q, r - 1), (q + 1, r - 1), (q - 1, r + 1)]


def _hex_distance(q1: int, r1: int, q2: int, r2: int) -> int:
    """Cube distance between two hex coordinates."""
    # Convert offset to cube
    x1, z1 = q1, r1
    y1 = -x1 - z1
    x2, z2 = q2, r2
    y2 = -x2 - z2
    return max(abs(x1 - x2), abs(y1 - y2), abs(z1 - z2))


def _sector_id(q: int, r: int) -> str:
    return f"H_{q}_{r}"


def _is_edge(q: int, r: int) -> bool:
    return q == 0 or q == GRID_SIZE - 1 or r == 0 or r == GRID_SIZE - 1


def _value_noise_2d(rng: random.Random, grid_points: dict[tuple[int, int], float],
                     q: int, r: int, scale: float = 3.0) -> float:
    """Simple seeded value noise on a 2D grid. Returns 0.0-1.0."""
    # Map hex coord to noise space
    x = q / scale
    y = r / scale
    # Bilinear interpolation between grid points
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = x0 + 1
    y1 = y0 + 1
    fx = x - x0
    fy = y - y0
    # Smoothstep
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)

    def _gp(gx: int, gy: int) -> float:
        if (gx, gy) not in grid_points:
            grid_points[(gx, gy)] = rng.random()
        return grid_points[(gx, gy)]

    v00 = _gp(x0, y0)
    v10 = _gp(x1, y0)
    v01 = _gp(x0, y1)
    v11 = _gp(x1, y1)
    top = v00 * (1 - fx) + v10 * fx
    bot = v01 * (1 - fx) + v11 * fx
    return top * (1 - fy) + bot * fy


def _assign_biomes(rng: random.Random) -> dict[tuple[int, int], BiomeType]:
    """Assign biomes using Voronoi regions with noise jitter for organic shapes."""
    biomes_list = list(BiomeType)
    num_biomes = len(biomes_list)

    # Place biome seed points spread across the grid
    # Use greedy farthest-point sampling for even distribution
    all_coords = [(q, r) for q in range(GRID_SIZE) for r in range(GRID_SIZE)]
    rng.shuffle(all_coords)

    seeds: list[tuple[int, int]] = [all_coords[0]]
    for _ in range(num_biomes - 1):
        # Pick coord farthest from all existing seeds
        best_coord = None
        best_min_dist = -1
        for coord in all_coords:
            if coord in seeds:
                continue
            min_d = min(_hex_distance(coord[0], coord[1], s[0], s[1]) for s in seeds)
            if min_d > best_min_dist:
                best_min_dist = min_d
                best_coord = coord
        if best_coord:
            seeds.append(best_coord)

    # Shuffle biome assignment to seeds
    biome_order = list(biomes_list)
    rng.shuffle(biome_order)

    # Assign each hex to nearest seed with noise perturbation
    noise_grid: dict[tuple[int, int], float] = {}
    result: dict[tuple[int, int], BiomeType] = {}

    for q in range(GRID_SIZE):
        for r in range(GRID_SIZE):
            jitter = _value_noise_2d(rng, noise_grid, q, r, 2.5) * 1.8
            best_biome = biome_order[0]
            best_dist = float("inf")

            for i, (sq, sr) in enumerate(seeds):
                d = _hex_distance(q, r, sq, sr) + jitter * (0.5 + rng.random())
                if d < best_dist:
                    best_dist = d
                    best_biome = biome_order[i]

            result[(q, r)] = best_biome

    return result


def _place_havens(rng: random.Random, count: int = 8, min_dist: int = 3) -> list[tuple[int, int]]:
    """Place HAVENs at evenly-distributed positions with minimum distance."""
    all_coords = [(q, r) for q in range(GRID_SIZE) for r in range(GRID_SIZE)]
    rng.shuffle(all_coords)

    havens: list[tuple[int, int]] = []
    for q, r in all_coords:
        if len(havens) >= count:
            break
        # Check minimum distance from all existing havens
        too_close = False
        for hq, hr in havens:
            if _hex_distance(q, r, hq, hr) < min_dist:
                too_close = True
                break
        if not too_close:
            havens.append((q, r))

    # If we couldn't place enough with min_dist, relax constraint
    if len(havens) < count:
        for q, r in all_coords:
            if len(havens) >= count:
                break
            if (q, r) not in havens:
                too_close = False
                for hq, hr in havens:
                    if _hex_distance(q, r, hq, hr) < 2:
                        too_close = True
                        break
                if not too_close:
                    havens.append((q, r))

    return havens


def _classify_sectors(
    biomes: dict[tuple[int, int], BiomeType],
    havens: list[tuple[int, int]],
    rng: random.Random,
) -> dict[tuple[int, int], SectorType]:
    """Assign sector types: HAVEN, FRONTIER, WASTELAND, SETTLED."""
    noise_grid: dict[tuple[int, int], float] = {}
    types: dict[tuple[int, int], SectorType] = {}

    haven_set = set(havens)

    for q in range(GRID_SIZE):
        for r in range(GRID_SIZE):
            if (q, r) in haven_set:
                types[(q, r)] = SectorType.HAVEN
                continue

            # FRONTIER = adjacent to 2+ different biomes
            neighbor_biomes = set()
            neighbor_biomes.add(biomes[(q, r)])
            for nq, nr in _hex_neighbors(q, r):
                if 0 <= nq < GRID_SIZE and 0 <= nr < GRID_SIZE:
                    neighbor_biomes.add(biomes[(nq, nr)])

            if len(neighbor_biomes) >= 3:
                types[(q, r)] = SectorType.FRONTIER
            elif _is_edge(q, r):
                noise_val = _value_noise_2d(rng, noise_grid, q, r, 2.0)
                if noise_val < 0.4:
                    types[(q, r)] = SectorType.WASTELAND
                else:
                    types[(q, r)] = SectorType.SETTLED
            elif len(neighbor_biomes) >= 2:
                types[(q, r)] = SectorType.FRONTIER
            else:
                types[(q, r)] = SectorType.SETTLED

    return types


def create_open_world(seed: int) -> WorldState:
    """Generate a deterministic 8x8 hex grid open world.

    Returns a WorldState with 64 sectors, biomes, resource nodes, and sector types.
    No players or structures — those are added via join.
    """
    rng = random.Random(seed)

    # Step 1: Assign biomes via noise
    biomes = _assign_biomes(rng)

    # Step 2: Place HAVENs
    havens = _place_havens(rng)

    # Step 3: Classify sector types
    sector_types = _classify_sectors(biomes, havens, rng)

    # Step 4: Build adjacency and sectors
    world = WorldState(planet_id=f"world_{seed}")
    node_counter = 0

    for q in range(GRID_SIZE):
        for r in range(GRID_SIZE):
            sid = _sector_id(q, r)
            biome = biomes[(q, r)]
            stype = sector_types[(q, r)]

            # Compute adjacency
            adj_ids = []
            for nq, nr in _hex_neighbors(q, r):
                if 0 <= nq < GRID_SIZE and 0 <= nr < GRID_SIZE:
                    adj_ids.append(_sector_id(nq, nr))

            # Place resource nodes based on biome
            nodes: list[ResourceNode] = []
            for res_type, richness in BIOME_RESOURCES.get(biome, []):
                node_counter += 1
                nodes.append(ResourceNode(
                    node_id=f"node_{node_counter:03d}",
                    resource_type=res_type,
                    richness=richness,
                ))

            # WASTELAND sectors: no primary resource (harsh), but keep secondary if any
            if stype == SectorType.WASTELAND and biome != BiomeType.NEXUS:
                # Wasteland keeps only rare resources (secondary)
                if len(nodes) > 1:
                    nodes = nodes[1:]  # drop primary, keep secondary
                elif nodes:
                    # Single resource — reduce richness
                    nodes[0] = ResourceNode(
                        node_id=nodes[0].node_id,
                        resource_type=nodes[0].resource_type,
                        richness=max(1, nodes[0].richness // 2),
                    )

            sector = SectorState(
                sector_id=sid,
                name=f"{biome.value.title()} {sid}",
                sector_type=stype,
                adjacent_sector_ids=adj_ids,
                resource_nodes=nodes,
                biome=biome,
            )
            world.sectors[sid] = sector

    return world


def get_open_world_stats(world: WorldState) -> dict:
    """Return summary statistics for an open world map."""
    type_counts: dict[str, int] = {}
    biome_counts: dict[str, int] = {}
    resource_counts: dict[str, int] = {}

    for sector in world.sectors.values():
        st = sector.sector_type.value
        type_counts[st] = type_counts.get(st, 0) + 1

        if sector.biome:
            b = sector.biome.value
            biome_counts[b] = biome_counts.get(b, 0) + 1

        for node in sector.resource_nodes:
            rt = node.resource_type.value
            resource_counts[rt] = resource_counts.get(rt, 0) + 1

    return {
        "total_sectors": len(world.sectors),
        "sector_types": type_counts,
        "biomes": biome_counts,
        "resource_nodes": resource_counts,
    }
