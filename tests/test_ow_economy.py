"""OW-3: Three-resource economy tests."""
from __future__ import annotations

from engine.config import (
    BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT,
    DATA_HARVESTER_DATA_PER_HEARTBEAT,
    EXTRACTOR_METAL_PER_HEARTBEAT,
    GameConfig,
    STRUCTURE_CATALOG,
)
from engine.engine import init_game, submit_action, run_heartbeat
from engine.enums import ActionType, ResourceType, StructureType
from engine.models import (
    GameState,
    ResourceNode,
    StructureState,
    next_id,
)

from .conftest import build_structure_in_frontier, init_two_player_game, make_action


def _add_resource_node(state: GameState, sector_id: str, resource_type: ResourceType, richness: int = 5) -> None:
    """Add a resource node to a sector."""
    sector = state.world.sectors[sector_id]
    node_id = next_id(state, "node")
    sector.resource_nodes.append(
        ResourceNode(node_id=node_id, resource_type=resource_type, richness=richness)
    )


def _place_structure(
    state: GameState,
    player_id: str,
    sector_id: str,
    structure_type: StructureType,
) -> str:
    """Directly place a structure without going through action validation."""
    catalog = STRUCTURE_CATALOG[structure_type]
    st_id = next_id(state, "st")
    structure = StructureState(
        structure_id=st_id,
        owner_player_id=player_id,
        sector_id=sector_id,
        structure_type=structure_type,
        hp=catalog["hp"],
        max_hp=catalog["hp"],
        active=True,
        activation_heartbeat=0,
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
    state.world.sectors[sector_id].structure_ids.append(st_id)
    return st_id


class TestDataHarvesterProducesData:
    def test_data_harvester_produces_data(self):
        state = init_two_player_game()

        # Add a DATA resource node to F1 (frontier sector adjacent to p1's safe zone)
        _add_resource_node(state, "F1", ResourceType.DATA)

        # Place a data harvester directly (skip build validation for production test)
        _place_structure(state, "p1", "F1", StructureType.DATA_HARVESTER)

        data_before = state.players["p1"].data
        run_heartbeat(state)
        data_after = state.players["p1"].data

        assert data_after == data_before + DATA_HARVESTER_DATA_PER_HEARTBEAT


class TestBioCultivatorProducesBiomass:
    def test_bio_cultivator_produces_biomass(self):
        state = init_two_player_game()

        # Add a BIOMASS resource node to F1
        _add_resource_node(state, "F1", ResourceType.BIOMASS)

        # Place a bio cultivator directly
        _place_structure(state, "p1", "F1", StructureType.BIO_CULTIVATOR)

        biomass_before = state.players["p1"].biomass
        run_heartbeat(state)
        biomass_after = state.players["p1"].biomass

        assert biomass_after == biomass_before + BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT


class TestDataHarvesterRequiresDataNode:
    def test_data_harvester_requires_data_node(self):
        state = init_two_player_game()
        # Give player enough resources to build
        state.players["p1"].data = 50

        # F1 has a METAL node but no DATA node — build should fail
        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.DATA_HARVESTER.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        # Should have failed with resource node reason
        failed = [
            e for e in state.event_log
            if e.event_type == "ACTION_FAILED"
            and e.details.get("action_id") == action.action_id
        ]
        assert len(failed) == 1
        assert "DATA" in failed[0].details.get("failure_reason", "")

        # No DATA_HARVESTER should exist
        harvesters = [
            s for s in state.structures.values()
            if s.structure_type == StructureType.DATA_HARVESTER
        ]
        assert len(harvesters) == 0


class TestBioCultivatorRequiresBiomassNode:
    def test_bio_cultivator_requires_biomass_node(self):
        state = init_two_player_game()
        # Give player enough resources to build
        state.players["p1"].biomass = 50

        # F1 has a METAL node but no BIOMASS node — build should fail
        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.BIO_CULTIVATOR.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        # Should have failed
        failed = [
            e for e in state.event_log
            if e.event_type == "ACTION_FAILED"
            and e.details.get("action_id") == action.action_id
        ]
        assert len(failed) == 1
        assert "BIOMASS" in failed[0].details.get("failure_reason", "")

        # No BIO_CULTIVATOR should exist
        cultivators = [
            s for s in state.structures.values()
            if s.structure_type == StructureType.BIO_CULTIVATOR
        ]
        assert len(cultivators) == 0


class TestExtractorStillProducesMetal:
    def test_extractor_still_produces_metal(self):
        state = init_two_player_game()

        # F1 already has a METAL node in the default world
        _place_structure(state, "p1", "F1", StructureType.EXTRACTOR)

        metal_before = state.players["p1"].metal
        run_heartbeat(state)
        metal_after = state.players["p1"].metal

        assert metal_after == metal_before + EXTRACTOR_METAL_PER_HEARTBEAT


class TestThreeResourcesAllProduce:
    def test_three_resources_all_produce(self):
        state = init_two_player_game()

        # F1 already has METAL node; add DATA and BIOMASS nodes
        _add_resource_node(state, "F1", ResourceType.DATA)
        _add_resource_node(state, "F1", ResourceType.BIOMASS)

        # Place all three producer types
        _place_structure(state, "p1", "F1", StructureType.EXTRACTOR)
        _place_structure(state, "p1", "F1", StructureType.DATA_HARVESTER)
        _place_structure(state, "p1", "F1", StructureType.BIO_CULTIVATOR)

        metal_before = state.players["p1"].metal
        data_before = state.players["p1"].data
        biomass_before = state.players["p1"].biomass

        run_heartbeat(state)

        assert state.players["p1"].metal == metal_before + EXTRACTOR_METAL_PER_HEARTBEAT
        assert state.players["p1"].data == data_before + DATA_HARVESTER_DATA_PER_HEARTBEAT
        assert state.players["p1"].biomass == biomass_before + BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT
