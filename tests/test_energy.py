"""Energy system tests."""
from __future__ import annotations

import pytest

from engine.config import GameConfig, SUBAGENT_UPKEEP
from engine.engine import init_game, submit_action, run_heartbeat
from engine.energy import (
    compute_player_income,
    compute_player_reserve_cap,
    compute_player_throughput_cap,
    apply_upkeep_deactivations,
)
from engine.enums import ActionType, StructureType

from .conftest import make_action, init_two_player_game, build_structure_in_frontier


class TestIncomeNoStructures:
    def test_income_no_structures(self):
        state = init_two_player_game()
        assert compute_player_income(state, "p1") == 15


class TestIncomeWithReactor:
    def test_income_with_reactor(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.REACTOR)
        assert compute_player_income(state, "p1") == 15 + 8


class TestReserveCapBase:
    def test_reserve_cap_base(self):
        state = init_two_player_game()
        assert compute_player_reserve_cap(state, "p1") == 20


class TestReserveCapWithBattery:
    def test_reserve_cap_with_battery(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.BATTERY)
        assert compute_player_reserve_cap(state, "p1") == 20 + 10


class TestThroughputCapBase:
    def test_throughput_cap_base(self):
        state = init_two_player_game()
        assert compute_player_throughput_cap(state, "p1") == 15


class TestThroughputCapWithRelay:
    def test_throughput_cap_with_relay(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.RELAY)
        assert compute_player_throughput_cap(state, "p1") == 15 + 5


class TestUpkeepDeactivation:
    def test_upkeep_deactivation(self):
        """When upkeep exceeds income+reserve, subagents deactivate first."""
        state = init_two_player_game()

        # Give p1 some energy via heartbeats
        run_heartbeat(state)
        run_heartbeat(state)

        # Create multiple subagents to drive up upkeep
        # Each subagent costs 1 upkeep. Also build expensive structures.
        # First build some structures to expand territory
        build_structure_in_frontier(state, "p1", "F1", StructureType.REACTOR)  # upkeep 2

        # Create several subagents
        for i in range(4):
            sa = make_action(
                state, "p1", ActionType.CREATE_SUBAGENT,
                {"name": f"sa_{i}", "mandate": "test"},
                priority=5,
            )
            submit_action(state, sa)
        run_heartbeat(state)

        active_subs_before = [
            s for s in state.subagents.values()
            if s.owner_player_id == "p1" and s.active
        ]

        # Now drain p1's reserve to 0 and set a situation where upkeep > income
        state.players["p1"].energy_reserve = 0

        # Add structures with high upkeep but NO income bonus (ATTACK_NODE: upkeep=2, income=0)
        from engine.models import StructureState, next_id
        from engine.config import STRUCTURE_CATALOG

        for i in range(20):
            cat = STRUCTURE_CATALOG[StructureType.ATTACK_NODE]
            st_id = next_id(state, "st")
            struct = StructureState(
                structure_id=st_id,
                owner_player_id="p1",
                sector_id="F1",
                structure_type=StructureType.ATTACK_NODE,
                hp=cat["hp"], max_hp=cat["hp"],
                active=True, activation_heartbeat=0,
                influence=cat["influence"],
                energy_income_bonus=0, reserve_cap_bonus=0, throughput_cap_bonus=0,
                upkeep_cost=cat["upkeep"],  # 2 each
                metal_cost=cat["metal_cost"], data_cost=0, biomass_cost=0,
            )
            state.structures[st_id] = struct
            state.world.sectors["F1"].structure_ids.append(st_id)

        # Now upkeep is very high. Apply deactivations.
        # The function deactivates subagents first, then structures.
        apply_upkeep_deactivations(state, "p1")

        # Check that at least some subagents were deactivated
        active_subs_after = [
            s for s in state.subagents.values()
            if s.owner_player_id == "p1" and s.active
        ]
        assert len(active_subs_after) < len(active_subs_before)
