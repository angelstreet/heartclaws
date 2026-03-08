"""Tests for Phase OW-1: Open World map generator."""

from engine.enums import BiomeType, SectorType
from engine.world import create_open_world, get_open_world_stats, GRID_SIZE, _hex_distance


class TestOpenWorldGeneration:
    def test_generates_64_sectors(self):
        world = create_open_world(42)
        assert len(world.sectors) == 64

    def test_deterministic_with_same_seed(self):
        w1 = create_open_world(99)
        w2 = create_open_world(99)
        for sid in w1.sectors:
            assert w1.sectors[sid].biome == w2.sectors[sid].biome
            assert w1.sectors[sid].sector_type == w2.sectors[sid].sector_type
            assert len(w1.sectors[sid].resource_nodes) == len(w2.sectors[sid].resource_nodes)

    def test_different_seeds_produce_different_maps(self):
        w1 = create_open_world(1)
        w2 = create_open_world(2)
        biomes1 = [w1.sectors[s].biome for s in sorted(w1.sectors)]
        biomes2 = [w2.sectors[s].biome for s in sorted(w2.sectors)]
        assert biomes1 != biomes2

    def test_sector_id_format(self):
        world = create_open_world(42)
        for sid in world.sectors:
            assert sid.startswith("H_")
            parts = sid.split("_")
            assert len(parts) == 3
            q, r = int(parts[1]), int(parts[2])
            assert 0 <= q < GRID_SIZE
            assert 0 <= r < GRID_SIZE


class TestHavens:
    def test_exactly_8_havens(self):
        for seed in [0, 42, 100, 999]:
            world = create_open_world(seed)
            havens = [s for s in world.sectors.values() if s.sector_type == SectorType.HAVEN]
            assert len(havens) == 8, f"Seed {seed}: got {len(havens)} havens"

    def test_havens_minimum_distance(self):
        world = create_open_world(42)
        havens = []
        for sid, s in world.sectors.items():
            if s.sector_type == SectorType.HAVEN:
                parts = sid.split("_")
                havens.append((int(parts[1]), int(parts[2])))
        # All haven pairs should be at least 2 apart
        for i, (q1, r1) in enumerate(havens):
            for j, (q2, r2) in enumerate(havens):
                if i < j:
                    assert _hex_distance(q1, r1, q2, r2) >= 2


class TestAdjacency:
    def test_adjacency_is_symmetric(self):
        world = create_open_world(42)
        for sid, sector in world.sectors.items():
            for adj_id in sector.adjacent_sector_ids:
                assert sid in world.sectors[adj_id].adjacent_sector_ids, \
                    f"{sid} -> {adj_id} not symmetric"

    def test_interior_sectors_have_6_neighbors(self):
        world = create_open_world(42)
        # Interior sectors (not on edge) should have exactly 6 neighbors
        for q in range(1, GRID_SIZE - 1):
            for r in range(1, GRID_SIZE - 1):
                sid = f"H_{q}_{r}"
                assert len(world.sectors[sid].adjacent_sector_ids) == 6

    def test_edge_sectors_have_fewer_neighbors(self):
        world = create_open_world(42)
        for sid, sector in world.sectors.items():
            parts = sid.split("_")
            q, r = int(parts[1]), int(parts[2])
            n = len(sector.adjacent_sector_ids)
            if q in (0, GRID_SIZE - 1) or r in (0, GRID_SIZE - 1):
                assert n < 6, f"Edge sector {sid} has {n} neighbors"
            else:
                assert n == 6


class TestBiomes:
    def test_all_sectors_have_biome(self):
        world = create_open_world(42)
        for sector in world.sectors.values():
            assert sector.biome is not None
            assert isinstance(sector.biome, BiomeType)

    def test_all_five_biomes_present(self):
        # At least across a few seeds, all biomes should appear
        all_biomes = set()
        for seed in range(5):
            world = create_open_world(seed)
            for s in world.sectors.values():
                all_biomes.add(s.biome)
        assert all_biomes == set(BiomeType)

    def test_biome_balance_reasonable(self):
        """No single biome should dominate more than 60% of sectors."""
        for seed in range(10):
            stats = get_open_world_stats(create_open_world(seed))
            for count in stats["biomes"].values():
                assert count <= 40, f"Seed {seed}: biome has {count}/64 sectors"


class TestResources:
    def test_ironlands_has_metal(self):
        world = create_open_world(42)
        for s in world.sectors.values():
            if s.biome == BiomeType.IRONLANDS and s.sector_type != SectorType.WASTELAND:
                metal = [n for n in s.resource_nodes if n.resource_type.value == "METAL"]
                assert len(metal) >= 1

    def test_nexus_has_all_three(self):
        world = create_open_world(42)
        for s in world.sectors.values():
            if s.biome == BiomeType.NEXUS and s.sector_type != SectorType.WASTELAND:
                types = {n.resource_type for n in s.resource_nodes}
                assert types == {r for r in __import__("engine.enums", fromlist=["ResourceType"]).ResourceType}

    def test_wasteland_has_reduced_resources(self):
        world = create_open_world(42)
        for s in world.sectors.values():
            if s.sector_type == SectorType.WASTELAND and s.biome != BiomeType.NEXUS:
                # Wasteland should have fewer/weaker resources than normal
                total_richness = sum(n.richness for n in s.resource_nodes)
                assert total_richness <= 8  # reduced from normal


class TestSectorTypes:
    def test_all_four_types_present(self):
        world = create_open_world(42)
        types = {s.sector_type for s in world.sectors.values()}
        assert SectorType.HAVEN in types
        assert SectorType.SETTLED in types
        assert SectorType.FRONTIER in types
        assert SectorType.WASTELAND in types

    def test_frontier_borders_multiple_biomes(self):
        world = create_open_world(42)
        for sid, sector in world.sectors.items():
            if sector.sector_type == SectorType.FRONTIER:
                neighbor_biomes = {sector.biome}
                for adj_id in sector.adjacent_sector_ids:
                    neighbor_biomes.add(world.sectors[adj_id].biome)
                assert len(neighbor_biomes) >= 2, \
                    f"Frontier {sid} borders only one biome"


class TestStats:
    def test_stats_structure(self):
        stats = get_open_world_stats(create_open_world(42))
        assert stats["total_sectors"] == 64
        assert "sector_types" in stats
        assert "biomes" in stats
        assert "resource_nodes" in stats
        assert sum(stats["sector_types"].values()) == 64
        assert sum(stats["biomes"].values()) == 64
