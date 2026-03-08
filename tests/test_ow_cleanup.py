"""Tests for inactive player cleanup and world KPIs."""

import pytest

from engine.openworld import (
    INACTIVE_CLEANUP_THRESHOLD,
    INACTIVE_DECAY_THRESHOLD,
    apply_inactive_cleanup,
    compute_world_kpis,
    init_open_world,
    join_open_world,
)


@pytest.fixture
def world():
    return init_open_world(seed=99)


class TestInactiveCleanup:
    def test_no_cleanup_active_player(self, world):
        join_open_world(world, "Active")
        world.heartbeat = 100
        world.players["p1"].last_active_heartbeat = 95
        result = apply_inactive_cleanup(world)
        assert result == []
        assert "p1" in world.players

    def test_no_cleanup_below_threshold(self, world):
        join_open_world(world, "Recent")
        world.heartbeat = INACTIVE_CLEANUP_THRESHOLD - 1
        world.players["p1"].last_active_heartbeat = 0
        result = apply_inactive_cleanup(world)
        assert result == []
        assert "p1" in world.players

    def test_cleanup_at_threshold(self, world):
        join_open_world(world, "Ghost")
        world.heartbeat = INACTIVE_CLEANUP_THRESHOLD
        world.players["p1"].last_active_heartbeat = 0
        result = apply_inactive_cleanup(world)
        assert len(result) == 1
        assert result[0]["player_id"] == "p1"
        assert result[0]["player_name"] == "Ghost"
        assert "p1" not in world.players

    def test_cleanup_structures_become_ruins(self, world):
        join_open_world(world, "Abandoned")
        # Player has sanctuary core
        structures_before = [
            st_id for st_id, st in world.structures.items()
            if st.owner_player_id == "p1"
        ]
        assert len(structures_before) > 0

        world.heartbeat = INACTIVE_CLEANUP_THRESHOLD + 100
        world.players["p1"].last_active_heartbeat = 0
        apply_inactive_cleanup(world)

        # Structures should now be ownerless (ruins) at 50% HP
        for st_id in structures_before:
            if st_id in world.structures:
                assert world.structures[st_id].owner_player_id is None

    def test_cleanup_only_affects_inactive(self, world):
        join_open_world(world, "Active")
        join_open_world(world, "Ghost")
        world.heartbeat = INACTIVE_CLEANUP_THRESHOLD + 10
        world.players["p1"].last_active_heartbeat = world.heartbeat  # active
        world.players["p2"].last_active_heartbeat = 0  # inactive
        result = apply_inactive_cleanup(world)
        assert len(result) == 1
        assert result[0]["player_id"] == "p2"
        assert "p1" in world.players
        assert "p2" not in world.players


class TestWorldKPIs:
    def test_empty_world(self, world):
        kpis = compute_world_kpis(world)
        assert kpis["heartbeat"] == 0
        assert kpis["total_players_alltime"] == 0
        assert kpis["alive_players"] == 0
        assert kpis["active_players"] == 0
        assert kpis["total_sectors"] == 64
        assert kpis["unclaimed_sectors"] == 64
        assert kpis["players"] == []

    def test_with_players(self, world):
        join_open_world(world, "Alice")
        join_open_world(world, "Bob")
        world.heartbeat = 10
        world.players["p1"].last_active_heartbeat = 10
        world.players["p2"].last_active_heartbeat = 10

        kpis = compute_world_kpis(world)
        assert kpis["total_players_alltime"] == 2
        assert kpis["alive_players"] == 2
        assert kpis["active_players"] == 2
        assert kpis["inactive_players"] == 0
        assert kpis["total_structures"] >= 2  # at least 2 sanctuary cores
        assert kpis["controlled_sectors"] >= 2
        assert len(kpis["players"]) == 2

    def test_inactive_player_counted(self, world):
        join_open_world(world, "Alice")
        world.heartbeat = 100
        world.players["p1"].last_active_heartbeat = 0  # inactive

        kpis = compute_world_kpis(world)
        assert kpis["alive_players"] == 1
        assert kpis["active_players"] == 0
        assert kpis["inactive_players"] == 1
        assert kpis["players"][0]["active"] is False

    def test_structures_by_type(self, world):
        join_open_world(world, "Builder")
        kpis = compute_world_kpis(world)
        assert "SANCTUARY_CORE" in kpis["structures_by_type"]

    def test_kpi_season(self, world):
        world.heartbeat = 4500
        kpis = compute_world_kpis(world)
        assert kpis["season"] == 3  # 4500 // 2000 + 1

    def test_alltime_counter_after_cleanup(self, world):
        join_open_world(world, "Temp")
        world.heartbeat = INACTIVE_CLEANUP_THRESHOLD + 1
        world.players["p1"].last_active_heartbeat = 0
        apply_inactive_cleanup(world)

        kpis = compute_world_kpis(world)
        assert kpis["total_players_alltime"] == 1  # counter doesn't reset
        assert kpis["alive_players"] == 0
