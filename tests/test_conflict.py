"""Conflict / attack tests."""
from __future__ import annotations

from engine.engine import submit_action, run_heartbeat
from engine.enums import ActionType, StructureType

from .conftest import make_action, init_two_player_game, build_structure_in_frontier


class TestAttackReducesHp:
    def test_attack_reduces_hp(self):
        state = init_two_player_game()
        # p1 builds tower in F1 (20 HP)
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        tower_id = [
            s.structure_id for s in state.structures.values()
            if s.sector_id == "F1" and s.structure_type == StructureType.TOWER
        ][0]

        # p2 builds attack node in F4 (adjacent to F1 via F4 adj list)
        build_structure_in_frontier(state, "p2", "F4", StructureType.ATTACK_NODE)

        # Attack once — 10 damage
        atk = make_action(
            state, "p2", ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": tower_id},
        )
        submit_action(state, atk)
        run_heartbeat(state)

        assert state.structures[tower_id].hp == 10


class TestAttackDestroysAtZeroHp:
    def test_attack_destroys_at_zero_hp(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        tower_id = [
            s.structure_id for s in state.structures.values()
            if s.sector_id == "F1" and s.structure_type == StructureType.TOWER
        ][0]

        build_structure_in_frontier(state, "p2", "F4", StructureType.ATTACK_NODE)

        # Two attacks = 20 damage = destroyed
        for _ in range(2):
            atk = make_action(
                state, "p2", ActionType.ATTACK_STRUCTURE,
                {"target_structure_id": tower_id},
            )
            submit_action(state, atk)
            run_heartbeat(state)

        assert tower_id not in state.structures


class TestCannotAttackSafeZone:
    def test_cannot_attack_safe_zone(self):
        state = init_two_player_game()
        core_id = state.players["p1"].sanctuary_core_structure_id

        build_structure_in_frontier(state, "p2", "F4", StructureType.ATTACK_NODE)

        atk = make_action(
            state, "p2", ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": core_id},
        )
        submit_action(state, atk)
        run_heartbeat(state)

        # Core untouched
        assert core_id in state.structures
        assert state.structures[core_id].hp == 100
