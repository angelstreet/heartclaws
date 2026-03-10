"""PRD Section 30 — 12 mandatory acceptance tests."""
from __future__ import annotations

import copy
import tempfile
import os

import pytest

from engine.config import GameConfig, STRUCTURE_CATALOG, BUILD_ENERGY_COSTS, ACTION_ENERGY_COSTS
from engine.engine import init_game, submit_action, run_heartbeat, save_game, load_game
from engine.energy import (
    compute_player_income,
    compute_player_reserve_cap,
    compute_player_throughput_cap,
)
from engine.enums import ActionType, ActionStatus, StructureType, SectorType
from engine.models import next_id

from .conftest import make_action, init_two_player_game, build_structure_in_frontier


# --------------------------------------------------------------------------- #
# 1. test_sanctuary_baseline
# --------------------------------------------------------------------------- #
class TestSanctuaryBaseline:
    def test_sanctuary_baseline(self):
        state = init_two_player_game()
        # Before heartbeat, check computed values for p1
        assert compute_player_income(state, "p1") == 15
        assert compute_player_throughput_cap(state, "p1") == 15
        assert compute_player_reserve_cap(state, "p1") == 20

        # p1 starts with 0 reserve; heartbeat adds income(15) - upkeep(0) = 15, capped at 20
        run_heartbeat(state)
        assert state.players["p1"].energy_reserve == 15


# --------------------------------------------------------------------------- #
# 2. test_no_overspend
# --------------------------------------------------------------------------- #
class TestNoOverspend:
    def test_no_overspend(self):
        state = init_two_player_game()
        # Run heartbeat to give p1 energy (reserve = 15)
        run_heartbeat(state)
        assert state.players["p1"].energy_reserve == 15

        # Now submit actions totaling > 15 energy.
        # SCAN_SECTOR costs 2 energy each. Submit 8 scans = 16 energy.
        # p1 controls S1 (safe), so scan S1 or adjacent F1.
        # S1 is adjacent to F1, S2. p1 controls S1, so scanning F1 (adjacent) is valid.
        results = []
        for i in range(8):
            action = make_action(
                state, "p1", ActionType.SCAN_SECTOR,
                {"sector_id": "F1"}, priority=5,
            )
            results.append(submit_action(state, action))

        run_heartbeat(state)

        # submit_action now validates at submission time with escalating costs.
        # With escalating multipliers the 5th+ action exceeds available energy (15).
        # At least one submission must have been rejected.
        rejected = [r for r in results if not r.accepted]
        assert len(rejected) >= 1  # At least one rejected due to energy limit

        # Resolved actions must not exceed throughput cap (15 energy)
        resolved = [a for a in state.event_log if a.event_type == "ACTION_RESOLVED"
                     and a.details.get("action_type") == "SCAN_SECTOR"]
        total_spent = sum(2 for _ in resolved)
        assert total_spent <= 15


# --------------------------------------------------------------------------- #
# 3. test_reactor_increases_income
# --------------------------------------------------------------------------- #
class TestReactorIncreasesIncome:
    def test_reactor_increases_income(self):
        state = init_two_player_game()
        # Build reactor in F1 (adjacent to S1 which p1 controls)
        build_structure_in_frontier(state, "p1", "F1", StructureType.REACTOR)

        # After build heartbeat, reactor is active. Check income.
        assert compute_player_income(state, "p1") == 15 + 8  # 23


# --------------------------------------------------------------------------- #
# 4. test_battery_increases_reserve_cap
# --------------------------------------------------------------------------- #
class TestBatteryIncreasesReserveCap:
    def test_battery_increases_reserve_cap(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.BATTERY)
        assert compute_player_reserve_cap(state, "p1") == 20 + 10  # 30


# --------------------------------------------------------------------------- #
# 5. test_relay_increases_throughput_cap
# --------------------------------------------------------------------------- #
class TestRelayIncreasesThroughputCap:
    def test_relay_increases_throughput_cap(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.RELAY)
        assert compute_player_throughput_cap(state, "p1") == 15 + 5  # 20


# --------------------------------------------------------------------------- #
# 6. test_frontier_build_adjacency
# --------------------------------------------------------------------------- #
class TestFrontierBuildAdjacency:
    def test_frontier_build_adjacency(self):
        state = init_two_player_game()
        # Give p1 enough reserve to build
        run_heartbeat(state)

        # F1 is adjacent to S1 (p1 controls S1) — should succeed
        action_ok = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.TOWER.value},
        )
        vr_ok = submit_action(state, action_ok)
        assert vr_ok.accepted

        # F5 is NOT adjacent to any p1-controlled sector — should fail at heartbeat
        action_bad = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F5", "structure_type": StructureType.TOWER.value},
        )
        vr_bad = submit_action(state, action_bad)
        # submit_action now validates at submission time — non-adjacent sector is rejected immediately
        assert not vr_bad.accepted

        run_heartbeat(state)

        # F1 build should have resolved
        f1_structures = [
            s for s in state.structures.values()
            if s.sector_id == "F1" and s.owner_player_id == "p1"
        ]
        assert len(f1_structures) >= 1

        # F5 build was rejected at submission, so no structure exists there
        f5_structures = [
            s for s in state.structures.values()
            if s.sector_id == "F5" and s.owner_player_id == "p1"
        ]
        assert len(f5_structures) == 0


# --------------------------------------------------------------------------- #
# 7. test_control_change
# --------------------------------------------------------------------------- #
class TestControlChange:
    def test_control_change(self):
        state = init_two_player_game()

        # Build p1 tower in F1 (adjacent to S1)
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        assert state.world.sectors["F1"].controller_player_id == "p1"

        # Now build p2 tower in F1.
        # p2 controls S2. S2 is adjacent to S1 and F4. F1 is adjacent to S1, F2, F4.
        # p2 doesn't control any sector adjacent to F1 initially.
        # But p2 controls S2, and S2 is adjacent to F4, and F4 is adjacent to F1.
        # So p2 needs to first build in F4, then F1.
        # Actually, looking at adjacency: S2 adj = [S1, F4, S3]. F4 adj = [F1, S2, F5, F7].
        # p2 controls S2. F4 is adjacent to S2, so p2 can build in F4.
        build_structure_in_frontier(state, "p2", "F4", StructureType.TOWER)

        # Now p2 can build in F1 since F1 is adjacent to F4 which p2 now controls.
        # But wait — F1 is controlled by p1 now. Can't build in a sector controlled by another player.
        # So we need equal influence. Let's build p2's structure in F4 and check tie somewhere else.

        # Let's use a different approach: build structures for both players in an uncontrolled sector.
        # p1 controls F1 (from tower). F2 is adjacent to F1. p1 can build in F2.
        build_structure_in_frontier(state, "p1", "F2", StructureType.TOWER)
        assert state.world.sectors["F2"].controller_player_id == "p1"

        # p2 controls F4. F5 is adjacent to F4 and F2. p2 can build in F5.
        build_structure_in_frontier(state, "p2", "F5", StructureType.TOWER)
        assert state.world.sectors["F5"].controller_player_id == "p2"

        # Now p2 can build in F2 since F2 is adjacent to F5 which p2 controls?
        # F2 adj = [F1, F3, F5]. F5 is controlled by p2. But F2 is controlled by p1.
        # Validation says: if sector controlled by another player -> fail.
        # So we need a truly uncontrolled sector.

        # Let's build equal influence in F5 instead.
        # p1 controls F2. F5 is adjacent to F2. p1 can build in F5 since p1 controls F2 adj to F5.
        # But F5 is controlled by p2 now. So p1 can't build there (controlled by another).

        # Actually, let's just pick a fresh sector. F3 is adjacent to F2 (p1 controls F2).
        # p1 builds TOWER in F3 (influence 3)
        build_structure_in_frontier(state, "p1", "F3", StructureType.TOWER)
        assert state.world.sectors["F3"].controller_player_id == "p1"

        # F6 is adjacent to F3 and F5. p2 controls F5.
        # F6 is uncontrolled. p2 can build there (adj to F5).
        build_structure_in_frontier(state, "p2", "F6", StructureType.TOWER)
        assert state.world.sectors["F6"].controller_player_id == "p2"

        # Now F3 is adjacent to F6 (p2 controls). p2 can build in F3? No, F3 is controlled by p1.
        # This approach is tricky due to the "controlled by another player" rule.

        # Simpler: build equal influence structures directly in the world state.
        # Let's just place a structure for p2 directly in F3 to create a tie.
        from engine.config import STRUCTURE_CATALOG
        from engine.models import StructureState
        from engine.control import recompute_sector_control

        cat = STRUCTURE_CATALOG[StructureType.TOWER]
        st_id = next_id(state, "st")
        struct = StructureState(
            structure_id=st_id,
            owner_player_id="p2",
            sector_id="F3",
            structure_type=StructureType.TOWER,
            hp=cat["hp"], max_hp=cat["hp"],
            active=True, activation_heartbeat=0,
            influence=cat["influence"],  # 3 same as p1's tower
            energy_income_bonus=0, reserve_cap_bonus=0, throughput_cap_bonus=0,
            upkeep_cost=cat["upkeep"],
            metal_cost=cat["metal_cost"], data_cost=0, biomass_cost=0,
        )
        state.structures[st_id] = struct
        state.world.sectors["F3"].structure_ids.append(st_id)
        recompute_sector_control(state, "F3")

        # Now F3 has p1 tower (inf 3) and p2 tower (inf 3) => tie => uncontrolled
        assert state.world.sectors["F3"].controller_player_id is None


# --------------------------------------------------------------------------- #
# 8. test_attack_and_destroy
# --------------------------------------------------------------------------- #
class TestAttackAndDestroy:
    def test_attack_and_destroy(self):
        state = init_two_player_game()

        # Build p1 tower in F1 (adjacent to S1). Tower has 20 HP.
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        tower_id = [
            s.structure_id for s in state.structures.values()
            if s.sector_id == "F1" and s.structure_type == StructureType.TOWER
        ][0]
        assert state.structures[tower_id].hp == 20

        # Build p2 attack_node in F4 (adjacent to S2, p2 controls S2).
        # F4 is adjacent to F1, so p2 can attack structures in F1 from F4.
        build_structure_in_frontier(state, "p2", "F4", StructureType.ATTACK_NODE)

        # Attack the tower twice (10 damage each = 20 total = destroyed)
        # First attack
        atk1 = make_action(
            state, "p2", ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": tower_id}, priority=5,
        )
        submit_action(state, atk1)
        run_heartbeat(state)

        # Tower should have 10 HP remaining
        assert state.structures[tower_id].hp == 10

        # Second attack
        atk2 = make_action(
            state, "p2", ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": tower_id}, priority=5,
        )
        submit_action(state, atk2)
        run_heartbeat(state)

        # Tower should be destroyed and removed
        assert tower_id not in state.structures
        assert tower_id not in state.world.sectors["F1"].structure_ids


# --------------------------------------------------------------------------- #
# 9. test_safe_zone_immunity
# --------------------------------------------------------------------------- #
class TestSafeZoneImmunity:
    def test_safe_zone_immunity(self):
        state = init_two_player_game()
        core_id = state.players["p1"].sanctuary_core_structure_id

        # Give p2 an attack node somewhere first
        build_structure_in_frontier(state, "p2", "F4", StructureType.ATTACK_NODE)

        # Attempt to attack p1's sanctuary core in safe zone S1
        atk = make_action(
            state, "p2", ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": core_id}, priority=5,
        )
        vr_atk = submit_action(state, atk)
        run_heartbeat(state)

        # submit_action now validates at submission time — safe zone attack is rejected immediately
        assert not vr_atk.accepted
        # Core should still exist
        assert core_id in state.structures


# --------------------------------------------------------------------------- #
# 10. test_subagent_scope
# --------------------------------------------------------------------------- #
class TestSubagentScope:
    def test_subagent_scope(self):
        state = init_two_player_game()
        # Give energy
        run_heartbeat(state)

        # Create subagent scoped to F1 with BUILD_STRUCTURE only
        sa_action = make_action(
            state, "p1", ActionType.CREATE_SUBAGENT,
            {
                "name": "builder_f1",
                "scope_sector_ids": ["F1"],
                "scope_action_types": [ActionType.BUILD_STRUCTURE],
                "mandate": "Build in F1 only",
            },
            priority=5,
        )
        submit_action(state, sa_action)
        run_heartbeat(state)

        # Find the created subagent
        sa_id = state.players["p1"].subagent_ids[0]
        subagent = state.subagents[sa_id]
        assert subagent.active

        # Submit action for F2 via subagent — should fail scope check
        build_f2 = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F2", "structure_type": StructureType.TOWER.value},
            priority=5,
            subagent_id=sa_id,
        )
        vr_f2 = submit_action(state, build_f2)
        run_heartbeat(state)

        # submit_action validates scope at submission time now
        assert not vr_f2.accepted
        assert "not scoped for sector" in (vr_f2.reason or "")


# --------------------------------------------------------------------------- #
# 11. test_persistence_round_trip
# --------------------------------------------------------------------------- #
class TestPersistenceRoundTrip:
    def test_persistence_round_trip(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        run_heartbeat(state)  # extra heartbeat for energy

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_game(state, path)
            loaded = load_game(path)

            assert loaded.heartbeat == state.heartbeat
            assert loaded.players["p1"].energy_reserve == state.players["p1"].energy_reserve
            assert loaded.players["p2"].energy_reserve == state.players["p2"].energy_reserve
            assert len(loaded.structures) == len(state.structures)
            assert len(loaded.event_log) == len(state.event_log)
            for sid, sector in state.world.sectors.items():
                assert loaded.world.sectors[sid].controller_player_id == sector.controller_player_id
        finally:
            os.unlink(path)


# --------------------------------------------------------------------------- #
# 12. test_determinism
# --------------------------------------------------------------------------- #
class TestDeterminism:
    def test_determinism(self):
        def run_scenario():
            state = init_game(GameConfig(), ["p1", "p2"], seed=99)
            # Build some structures
            a1 = make_action(
                state, "p1", ActionType.BUILD_STRUCTURE,
                {"sector_id": "F1", "structure_type": StructureType.TOWER.value},
                priority=5,
            )
            submit_action(state, a1)
            run_heartbeat(state)

            a2 = make_action(
                state, "p2", ActionType.BUILD_STRUCTURE,
                {"sector_id": "F4", "structure_type": StructureType.TOWER.value},
                priority=5,
            )
            submit_action(state, a2)
            run_heartbeat(state)

            # One more heartbeat
            run_heartbeat(state)
            return state

        s1 = run_scenario()
        s2 = run_scenario()

        assert s1.heartbeat == s2.heartbeat
        assert s1.players["p1"].energy_reserve == s2.players["p1"].energy_reserve
        assert s1.players["p2"].energy_reserve == s2.players["p2"].energy_reserve
        assert len(s1.structures) == len(s2.structures)
        assert len(s1.event_log) == len(s2.event_log)
        for sid in s1.world.sectors:
            assert (s1.world.sectors[sid].controller_player_id
                    == s2.world.sectors[sid].controller_player_id)
