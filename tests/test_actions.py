"""Action validation and resolution tests."""
from __future__ import annotations

from engine.engine import init_game, submit_action, run_heartbeat
from engine.enums import ActionType, ActionStatus, StructureType

from .conftest import make_action, init_two_player_game, build_structure_in_frontier


class TestBuildStructureValid:
    def test_build_structure_valid(self):
        state = init_two_player_game()
        # F1 is adjacent to S1 (p1 controls). Build a tower.
        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.TOWER.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        # Tower should exist in F1
        towers = [
            s for s in state.structures.values()
            if s.sector_id == "F1" and s.structure_type == StructureType.TOWER
        ]
        assert len(towers) == 1


class TestBuildInWrongSectorTypeFails:
    def test_build_in_wrong_sector_type_fails(self):
        state = init_two_player_game()
        # Try to build REACTOR (frontier-only) in S1 (safe zone)
        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "S1", "structure_type": StructureType.REACTOR.value},
        )
        vr = submit_action(state, action)
        run_heartbeat(state)

        # submit_action now validates at submission time — wrong sector type is rejected immediately
        assert not vr.accepted


class TestBuildWithoutAdjacencyFails:
    def test_build_without_adjacency_fails(self):
        state = init_two_player_game()
        # F5 is not adjacent to any p1-controlled sector
        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F5", "structure_type": StructureType.TOWER.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        f5_structures = [
            s for s in state.structures.values()
            if s.sector_id == "F5" and s.owner_player_id == "p1"
        ]
        assert len(f5_structures) == 0


class TestRemoveStructure:
    def test_remove_structure(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)

        tower_id = [
            s.structure_id for s in state.structures.values()
            if s.sector_id == "F1" and s.structure_type == StructureType.TOWER
        ][0]

        action = make_action(
            state, "p1", ActionType.REMOVE_STRUCTURE,
            {"structure_id": tower_id},
        )
        submit_action(state, action)
        run_heartbeat(state)

        assert tower_id not in state.structures


class TestCannotRemoveSanctuaryCore:
    def test_cannot_remove_sanctuary_core(self):
        state = init_two_player_game()
        core_id = state.players["p1"].sanctuary_core_structure_id

        action = make_action(
            state, "p1", ActionType.REMOVE_STRUCTURE,
            {"structure_id": core_id},
        )
        vr = submit_action(state, action)
        run_heartbeat(state)

        # Core should still exist
        assert core_id in state.structures
        # submit_action now rejects at submission time
        assert not vr.accepted
