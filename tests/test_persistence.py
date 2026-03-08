"""Persistence / serialization tests."""
from __future__ import annotations

import tempfile
import os

from engine.engine import save_game, load_game, run_heartbeat
from engine.enums import StructureType, SectorType

from .conftest import init_two_player_game, build_structure_in_frontier


class TestSaveLoadRoundTrip:
    def test_save_load_round_trip(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)
        run_heartbeat(state)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_game(state, path)
            loaded = load_game(path)

            assert loaded.game_id == state.game_id
            assert loaded.heartbeat == state.heartbeat
            assert loaded.seed == state.seed
            assert len(loaded.structures) == len(state.structures)
            assert len(loaded.players) == len(state.players)
            assert len(loaded.event_log) == len(state.event_log)

            for pid in state.players:
                assert loaded.players[pid].energy_reserve == state.players[pid].energy_reserve
                assert loaded.players[pid].metal == state.players[pid].metal

            for sid in state.world.sectors:
                assert (loaded.world.sectors[sid].sector_type
                        == state.world.sectors[sid].sector_type)
                assert (loaded.world.sectors[sid].controller_player_id
                        == state.world.sectors[sid].controller_player_id)
        finally:
            os.unlink(path)


class TestEnumsSerializeCorrectly:
    def test_enums_serialize_correctly(self):
        state = init_two_player_game()
        build_structure_in_frontier(state, "p1", "F1", StructureType.TOWER)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_game(state, path)
            loaded = load_game(path)

            # Check structure types are proper enums after load
            for st in loaded.structures.values():
                assert isinstance(st.structure_type, StructureType)

            # Check sector types
            for sector in loaded.world.sectors.values():
                assert isinstance(sector.sector_type, SectorType)
        finally:
            os.unlink(path)
