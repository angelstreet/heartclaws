"""Tests for Phase OW-5: Seasons & Leaderboard."""

from engine.config import STRUCTURE_CATALOG
from engine.enums import ResourceType, SectorType, StructureType
from engine.models import GameState, ResourceNode, StructureState, next_id
from engine.openworld import init_open_world, join_open_world
from engine.seasons import (
    SEASON_LENGTH,
    SCORE_WEIGHTS,
    apply_world_event,
    check_season_boundary,
    compute_elo_change,
    compute_leaderboard,
    compute_player_score,
    get_current_season,
)


def _add_structure(
    state: GameState,
    player_id: str,
    sector_id: str,
    structure_type: StructureType,
    active: bool = True,
    hp: int | None = None,
) -> str:
    """Helper to add a structure to a player in a sector."""
    catalog = STRUCTURE_CATALOG[structure_type]
    st_id = next_id(state, "st")
    structure = StructureState(
        structure_id=st_id,
        owner_player_id=player_id,
        sector_id=sector_id,
        structure_type=structure_type,
        hp=hp if hp is not None else catalog["hp"],
        max_hp=catalog["hp"],
        active=active,
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
    sector = state.world.sectors[sector_id]
    sector.structure_ids.append(st_id)
    return st_id


def _setup_two_player_world() -> GameState:
    """Create an open world with 2 players and some structures for scoring."""
    state = init_open_world(seed=42)
    r1 = join_open_world(state, "Alice")
    r2 = join_open_world(state, "Bob")

    # Advance heartbeat so longevity is non-zero
    state.heartbeat = 100

    p1 = r1["player_id"]
    p2 = r2["player_id"]

    # Find a sector with a METAL resource node for an extractor
    metal_sector = None
    for sid, sector in state.world.sectors.items():
        if sid == r1["sector_id"] or sid == r2["sector_id"]:
            continue
        if any(n.resource_type == ResourceType.METAL and not n.depleted for n in sector.resource_nodes):
            metal_sector = sid
            break

    # Give Alice an extractor on a metal sector
    if metal_sector:
        _add_structure(state, p1, metal_sector, StructureType.EXTRACTOR)
        state.world.sectors[metal_sector].controller_player_id = p1

    # Give Alice a tower for extra influence
    tower_sector = None
    for sid, sector in state.world.sectors.items():
        if sid not in (r1["sector_id"], r2["sector_id"], metal_sector):
            tower_sector = sid
            break
    if tower_sector:
        _add_structure(state, p1, tower_sector, StructureType.TOWER)
        state.world.sectors[tower_sector].controller_player_id = p1

    # Alice has destroyed 3 structures, lost 1
    state.players[p1].structures_destroyed = 3
    state.players[p1].structures_lost = 1

    return state


class TestComputePlayerScore:
    def test_compute_player_score(self):
        """Verify all dimensions computed correctly."""
        state = _setup_two_player_world()
        p1 = "p1"
        score = compute_player_score(state, p1)

        assert score["player_id"] == p1

        # Territory: Alice controls her haven + metal_sector + tower_sector = 3
        assert score["territory"] >= 2

        # Economy: extractor on metal node = 3 per heartbeat
        assert score["economy"] >= 3.0

        # Military: 3 destroyed - 1 lost = 2
        assert score["military"] == 2

        # Longevity: heartbeat 100 - spawn 0 = 100
        assert score["longevity"] == 100

        # Influence: sanctuary core (5) + extractor (1) + tower (3) = 9
        assert score["influence"] >= 9

        # Composite is a float >= 0
        assert score["composite"] >= 0.0
        assert score["composite"] <= 100.0

    def test_score_zero_for_new_player(self):
        """A freshly spawned player should have minimal scores."""
        state = init_open_world(seed=42)
        r1 = join_open_world(state, "Alice")
        score = compute_player_score(state, r1["player_id"])

        assert score["territory"] >= 1  # controls haven
        assert score["economy"] == 0.0  # no extractors
        assert score["military"] == 0
        assert score["longevity"] == 0  # just spawned
        assert score["influence"] == 5  # sanctuary core only


class TestLeaderboard:
    def test_leaderboard_sorted_by_composite(self):
        """Multiple players, sorted correctly by composite score descending."""
        state = _setup_two_player_world()

        leaderboard = compute_leaderboard(state)

        assert len(leaderboard) == 2
        # Alice (p1) should rank higher due to more structures/territory
        assert leaderboard[0]["player_id"] == "p1"
        assert leaderboard[1]["player_id"] == "p2"
        assert leaderboard[0]["composite"] >= leaderboard[1]["composite"]

    def test_leaderboard_excludes_dead_players(self):
        """Dead players should not appear on the leaderboard."""
        state = _setup_two_player_world()
        state.players["p2"].alive = False

        leaderboard = compute_leaderboard(state)
        assert len(leaderboard) == 1
        assert leaderboard[0]["player_id"] == "p1"


class TestGetCurrentSeason:
    def test_get_current_season_start(self):
        """Heartbeat 0 is season 1."""
        state = init_open_world(seed=42)
        state.heartbeat = 0
        info = get_current_season(state)

        assert info["season"] == 1
        assert info["heartbeat"] == 0
        assert info["season_start"] == 0
        assert info["season_end"] == SEASON_LENGTH
        assert info["remaining"] == SEASON_LENGTH
        assert info["progress"] == 0.0

    def test_get_current_season_mid(self):
        """Heartbeat 1000 is mid-season 1."""
        state = init_open_world(seed=42)
        state.heartbeat = 1000
        info = get_current_season(state)

        assert info["season"] == 1
        assert info["remaining"] == 1000
        assert abs(info["progress"] - 0.5) < 0.01

    def test_get_current_season_boundary(self):
        """Heartbeat 2000 is season 2."""
        state = init_open_world(seed=42)
        state.heartbeat = 2000
        info = get_current_season(state)

        assert info["season"] == 2
        assert info["season_start"] == 2000
        assert info["season_end"] == 4000
        assert info["remaining"] == 2000
        assert info["progress"] == 0.0

    def test_get_current_season_various_heartbeats(self):
        """Verify season calculation at various heartbeats."""
        state = init_open_world(seed=42)

        for hb, expected_season in [(0, 1), (1999, 1), (2000, 2), (4000, 3), (5999, 3), (6000, 4)]:
            state.heartbeat = hb
            info = get_current_season(state)
            assert info["season"] == expected_season, f"heartbeat {hb} should be season {expected_season}"


class TestSeasonBoundary:
    def test_season_boundary_detection(self):
        """Heartbeat 2000 triggers season, 1999 doesn't."""
        state = _setup_two_player_world()
        state.heartbeat = 1999
        assert check_season_boundary(state) is None

        state.heartbeat = 2000
        report = check_season_boundary(state)
        assert report is not None
        assert report["season"] == 1  # season 1 just ended
        assert report["heartbeat"] == 2000
        assert len(report["leaderboard"]) == 2
        assert report["world_event"] in [
            "SOLAR_STORM", "RESOURCE_SURGE", "DECAY_WAVE", "NEW_DEPOSITS", "RADIATION_BELT"
        ]

    def test_season_boundary_at_zero(self):
        """Heartbeat 0 should NOT trigger a season boundary."""
        state = init_open_world(seed=42)
        state.heartbeat = 0
        assert check_season_boundary(state) is None

    def test_season_boundary_results(self):
        """Rank 1 = win, top 3 = draw, rest = loss."""
        state = init_open_world(seed=42)
        # Add 5 players
        for name in ["Alice", "Bob", "Charlie", "Dave", "Eve"]:
            join_open_world(state, name)

        # Give different scores by giving p1 more structures/territory
        # p1 gets extra structures
        for sid, sector in list(state.world.sectors.items())[:3]:
            if sid != state.players["p1"].sanctuary_sector_id:
                _add_structure(state, "p1", sid, StructureType.TOWER)
                sector.controller_player_id = "p1"

        state.players["p1"].structures_destroyed = 10

        state.heartbeat = 2000
        report = check_season_boundary(state)

        assert report is not None
        results = report["results"]
        assert len(results) == 5

        # First result should be the winner
        assert results[0]["outcome"] == "win"
        assert results[0]["rank"] == 1

        # Ranks 2 and 3 are draw
        assert results[1]["outcome"] == "draw"
        assert results[2]["outcome"] == "draw"

        # Ranks 4+ are loss
        assert results[3]["outcome"] == "loss"
        assert results[4]["outcome"] == "loss"

    def test_season_history_stored(self):
        """After season boundary, history is saved."""
        state = _setup_two_player_world()
        assert len(state.season_history) == 0

        state.heartbeat = 2000
        report = check_season_boundary(state)

        assert len(state.season_history) == 1
        assert state.season_history[0] == report
        assert state.season_history[0]["season"] == 1


class TestEloCalculation:
    def test_elo_calculation_winner_gains(self):
        """Winner should gain ELO."""
        # Equal ELO, rank 1 of 4 players
        change = compute_elo_change(1200, 1200, 1, 4)
        assert change > 0, "Rank 1 should gain ELO"

    def test_elo_calculation_loser_loses(self):
        """Bottom-ranked player should lose ELO."""
        # Equal ELO, rank 4 of 4 players
        change = compute_elo_change(1200, 1200, 4, 4)
        assert change < 0, "Bottom rank should lose ELO"

    def test_elo_calculation_underdog_gains_more(self):
        """Lower-rated player gains more for winning than higher-rated player."""
        underdog_change = compute_elo_change(1000, 1200, 1, 4)
        favorite_change = compute_elo_change(1400, 1200, 1, 4)
        assert underdog_change > favorite_change

    def test_elo_calculation_symmetric_for_equal_ratings(self):
        """With equal ratings, mid-rank gets ~0 ELO change."""
        # Rank 2 of 4: percentile = 0.5, actual = 0.5, expected = 0.5 -> change = 0
        change = compute_elo_change(1200, 1200, 2, 4)
        assert change == 0
        # Rank 1 of 4 with equal elo should gain
        change_top = compute_elo_change(1200, 1200, 1, 4)
        assert change_top > 0

    def test_elo_single_player(self):
        """Single player always gets actual=1.0."""
        change = compute_elo_change(1200, 1200, 1, 1)
        assert change > 0

    def test_elo_updated_at_season_boundary(self):
        """ELO should be updated when season boundary triggers."""
        state = _setup_two_player_world()
        # Initialize ELO
        state.player_elo["p1"] = 1200
        state.player_elo["p2"] = 1200

        state.heartbeat = 2000
        report = check_season_boundary(state)

        assert report is not None
        # Both players should have ELO changes
        for result in report["results"]:
            assert "elo_change" in result
            assert "new_elo" in result

        # Winner should have gained, loser lost
        assert report["results"][0]["elo_change"] > 0
        assert report["results"][1]["elo_change"] < 0


class TestWorldEventDecayWave:
    def test_world_event_decay_wave(self):
        """WASTELAND structures lose 5 HP."""
        state = init_open_world(seed=42)
        join_open_world(state, "Alice")

        # Find a WASTELAND sector
        wasteland_sid = None
        for sid, sector in state.world.sectors.items():
            if sector.sector_type == SectorType.WASTELAND:
                wasteland_sid = sid
                break

        if wasteland_sid is None:
            # No wasteland in this seed, skip
            return

        # Place a structure in the wasteland
        st_id = _add_structure(state, "p1", wasteland_sid, StructureType.TOWER, hp=20)
        original_hp = state.structures[st_id].hp

        events = apply_world_event(state, "DECAY_WAVE")

        # Structure should have lost 5 HP
        assert st_id in state.structures
        assert state.structures[st_id].hp == original_hp - 5
        assert len(events) >= 1
        assert any(e.event_type == "WORLD_EVENT_DECAY" for e in events)

    def test_decay_wave_destroys_low_hp(self):
        """DECAY_WAVE destroys structures with HP <= 5."""
        state = init_open_world(seed=42)
        join_open_world(state, "Alice")

        # Find a WASTELAND sector
        wasteland_sid = None
        for sid, sector in state.world.sectors.items():
            if sector.sector_type == SectorType.WASTELAND:
                wasteland_sid = sid
                break

        if wasteland_sid is None:
            return

        # Place a structure with only 3 HP
        st_id = _add_structure(state, "p1", wasteland_sid, StructureType.TOWER, hp=3)

        events = apply_world_event(state, "DECAY_WAVE")

        # Structure should be destroyed
        assert st_id not in state.structures
        assert any(e.event_type == "WORLD_EVENT_DESTROYED" for e in events)

    def test_decay_wave_ignores_non_wasteland(self):
        """Structures in non-WASTELAND sectors are unaffected."""
        state = init_open_world(seed=42)
        r1 = join_open_world(state, "Alice")

        # Find a non-wasteland sector (the haven is fine)
        haven_sid = r1["sector_id"]
        haven_structures_before = {
            st_id: state.structures[st_id].hp
            for st_id in state.world.sectors[haven_sid].structure_ids
            if st_id in state.structures
        }

        apply_world_event(state, "DECAY_WAVE")

        # Haven structures should be unaffected
        for st_id, old_hp in haven_structures_before.items():
            if st_id in state.structures:
                assert state.structures[st_id].hp == old_hp


class TestWorldEventNewDeposits:
    def test_world_event_new_deposits(self):
        """4 random sectors gain new resource nodes."""
        state = init_open_world(seed=42)
        state.heartbeat = 2000

        # Count total resource nodes before
        total_nodes_before = sum(
            len(sector.resource_nodes) for sector in state.world.sectors.values()
        )

        events = apply_world_event(state, "NEW_DEPOSITS")

        # Count total resource nodes after
        total_nodes_after = sum(
            len(sector.resource_nodes) for sector in state.world.sectors.values()
        )

        assert total_nodes_after == total_nodes_before + 4
        assert len(events) == 4
        assert all(e.event_type == "WORLD_EVENT_NEW_DEPOSIT" for e in events)

        # Each event should have resource details
        for evt in events:
            assert "resource_type" in evt.details
            assert "richness" in evt.details
            assert evt.details["richness"] >= 3
            assert evt.details["richness"] <= 6


class TestWorldEventStorage:
    def test_solar_storm_stored(self):
        """SOLAR_STORM stores modifier in world_events_active."""
        state = init_open_world(seed=42)
        state.heartbeat = 100

        events = apply_world_event(state, "SOLAR_STORM")

        assert len(state.world_events_active) == 1
        active = state.world_events_active[0]
        assert active["event_type"] == "SOLAR_STORM"
        assert active["expiry_heartbeat"] == 300
        assert len(events) == 1

    def test_resource_surge_stored(self):
        """RESOURCE_SURGE stores modifier in world_events_active."""
        state = init_open_world(seed=42)
        state.heartbeat = 100

        events = apply_world_event(state, "RESOURCE_SURGE")

        assert len(state.world_events_active) == 1
        active = state.world_events_active[0]
        assert active["event_type"] == "RESOURCE_SURGE"
        assert active["expiry_heartbeat"] == 200

    def test_radiation_belt_stored(self):
        """RADIATION_BELT stores modifier with affected sectors."""
        state = init_open_world(seed=42)
        state.heartbeat = 100

        events = apply_world_event(state, "RADIATION_BELT")

        assert len(state.world_events_active) == 1
        active = state.world_events_active[0]
        assert active["event_type"] == "RADIATION_BELT"
        assert active["expiry_heartbeat"] == 150
        assert "affected_sectors" in active
        assert len(active["affected_sectors"]) > 0
