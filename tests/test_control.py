"""Sector control tests."""
from __future__ import annotations

from engine.config import STRUCTURE_CATALOG
from engine.control import recompute_sector_control
from engine.engine import init_game
from engine.enums import SectorType, StructureType
from engine.models import StructureState, next_id

from .conftest import init_two_player_game, build_structure_in_frontier


class TestSafeSectorControlImmutable:
    def test_safe_sector_control_immutable(self):
        state = init_two_player_game()
        # S1 is owned by p1 (safe zone)
        assert state.world.sectors["S1"].controller_player_id == "p1"
        assert state.world.sectors["S1"].sector_type == SectorType.SAFE

        # Attempt to recompute — should not change
        recompute_sector_control(state, "S1")
        assert state.world.sectors["S1"].controller_player_id == "p1"


class TestFrontierUncontrolledInitially:
    def test_frontier_uncontrolled_initially(self):
        state = init_two_player_game()
        for sid, sector in state.world.sectors.items():
            if sector.sector_type == SectorType.FRONTIER:
                assert sector.controller_player_id is None, f"{sid} should be uncontrolled"


class TestHighestInfluenceWins:
    def test_highest_influence_wins(self):
        state = init_two_player_game()
        # Build p1 tower in F1 (influence 3)
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        assert state.world.sectors["F1"].controller_player_id == "p1"


class TestTieBecomesUncontrolled:
    def test_tie_becomes_uncontrolled(self):
        state = init_two_player_game()
        # Build p1 tower in F1 (influence 3)
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        assert state.world.sectors["F1"].controller_player_id == "p1"

        # Directly place p2 tower in F1 with same influence to create tie
        cat = STRUCTURE_CATALOG[StructureType.TOWER]
        st_id = next_id(state, "st")
        struct = StructureState(
            structure_id=st_id,
            owner_player_id="p2",
            sector_id="F1",
            structure_type=StructureType.TOWER,
            hp=cat["hp"], max_hp=cat["hp"],
            active=True, activation_heartbeat=0,
            influence=cat["influence"],
            energy_income_bonus=0, reserve_cap_bonus=0, throughput_cap_bonus=0,
            upkeep_cost=cat["upkeep"],
            metal_cost=cat["metal_cost"], data_cost=0, biomass_cost=0,
        )
        state.structures[st_id] = struct
        state.world.sectors["F1"].structure_ids.append(st_id)
        recompute_sector_control(state, "F1")

        # Equal influence => uncontrolled
        assert state.world.sectors["F1"].controller_player_id is None
