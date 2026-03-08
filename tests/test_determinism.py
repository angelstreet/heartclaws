"""Determinism tests."""
from __future__ import annotations

from engine.config import GameConfig
from engine.engine import init_game, submit_action, run_heartbeat
from engine.enums import ActionType, StructureType

from .conftest import make_action


class TestIdenticalRunsProduceIdenticalState:
    def test_identical_runs_produce_identical_state(self):
        def run_scenario():
            cfg = GameConfig()
            state = init_game(cfg, ["p1", "p2"], seed=77)

            # Build structures
            a1 = make_action(
                state, "p1", ActionType.BUILD_STRUCTURE,
                {"sector_id": "F1", "structure_type": StructureType.REACTOR.value},
            )
            submit_action(state, a1)
            run_heartbeat(state)

            a2 = make_action(
                state, "p2", ActionType.BUILD_STRUCTURE,
                {"sector_id": "F4", "structure_type": StructureType.TOWER.value},
            )
            submit_action(state, a2)

            a3 = make_action(
                state, "p1", ActionType.BUILD_STRUCTURE,
                {"sector_id": "F1", "structure_type": StructureType.TOWER.value},
            )
            submit_action(state, a3)
            run_heartbeat(state)

            run_heartbeat(state)
            return state

        s1 = run_scenario()
        s2 = run_scenario()

        # Compare all key fields
        assert s1.heartbeat == s2.heartbeat
        assert s1.id_counter == s2.id_counter
        assert len(s1.structures) == len(s2.structures)
        assert len(s1.event_log) == len(s2.event_log)

        for pid in s1.players:
            p1 = s1.players[pid]
            p2 = s2.players[pid]
            assert p1.energy_reserve == p2.energy_reserve
            assert p1.metal == p2.metal
            assert p1.data == p2.data

        for sid in s1.world.sectors:
            assert (s1.world.sectors[sid].controller_player_id
                    == s2.world.sectors[sid].controller_player_id)
