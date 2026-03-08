"""Tests for Phase OW-7: New Structures (Outpost, Shield Generator, Trade Hub)."""
from __future__ import annotations

from engine.config import ATTACK_DAMAGE, BUILD_ENERGY_COSTS, STRUCTURE_CATALOG
from engine.engine import submit_action, run_heartbeat
from engine.enums import ActionType, ResourceType, SectorType, StructureType
from engine.models import Action, GameState, StructureState, next_id
from engine.openworld import init_open_world, join_open_world
from engine.actions import get_action_energy_cost, validate_action

from .conftest import make_action, init_two_player_game, build_structure_in_frontier


def _ow_two_players(seed: int = 42) -> tuple[GameState, str, str]:
    """Create open world with two players. Returns (state, p1_id, p2_id)."""
    state = init_open_world(seed=seed)
    r1 = join_open_world(state, "Alice")
    r2 = join_open_world(state, "Bob")
    # Advance past grace period (10 heartbeats)
    state.heartbeat = 20
    for p in state.players.values():
        p.spawn_heartbeat = 0
    return state, r1["player_id"], r2["player_id"]


def _give_resources(state: GameState, player_id: str, metal: int = 100, data: int = 100, biomass: int = 100) -> None:
    """Give a player plenty of resources."""
    p = state.players[player_id]
    p.metal = metal
    p.data = data
    p.biomass = biomass


def _build_ow_structure(
    state: GameState, player_id: str, sector_id: str, structure_type: StructureType
) -> str:
    """Build a structure directly in open world (bypass action system for setup)."""
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
    state.world.sectors[sector_id].structure_ids.append(st_id)
    return st_id


class TestBuildOutpost:
    def test_build_outpost(self):
        """Can build an Outpost in a frontier sector, verify stats."""
        state = init_two_player_game()
        p1 = state.players["p1"]
        p1.metal = 50
        p1.data = 10

        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.OUTPOST.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        outposts = [
            s for s in state.structures.values()
            if s.sector_id == "F1" and s.structure_type == StructureType.OUTPOST
        ]
        assert len(outposts) == 1
        outpost = outposts[0]
        assert outpost.hp == 60
        assert outpost.max_hp == 60
        assert outpost.influence == 4
        assert outpost.energy_income_bonus == 8
        assert outpost.upkeep_cost == 3
        assert outpost.owner_player_id == "p1"


class TestOutpostSecondaryLife:
    def test_outpost_secondary_life(self):
        """Destroy sanctuary core — player survives if they have an active Outpost."""
        state, p1_id, p2_id = _ow_two_players()
        p1 = state.players[p1_id]
        p2 = state.players[p2_id]
        _give_resources(state, p1_id)
        _give_resources(state, p2_id)

        # p1's sanctuary core sector
        p1_core_sector = p1.sanctuary_sector_id
        p1_core_id = p1.sanctuary_core_structure_id

        # Find an adjacent sector for p1 to build an outpost
        adj_sectors = state.world.sectors[p1_core_sector].adjacent_sector_ids
        outpost_sector = adj_sectors[0]
        # Ensure p1 controls outpost sector
        state.world.sectors[outpost_sector].controller_player_id = p1_id

        # Build outpost for p1
        outpost_id = _build_ow_structure(state, p1_id, outpost_sector, StructureType.OUTPOST)

        # p2 needs an attack node. Find a sector adjacent to p1's core sector
        p2_atk_sector = adj_sectors[1] if len(adj_sectors) > 1 else adj_sectors[0]
        state.world.sectors[p2_atk_sector].controller_player_id = p2_id
        _build_ow_structure(state, p2_id, p2_atk_sector, StructureType.ATTACK_NODE)

        # Weaken the core to 1 HP so one attack destroys it
        state.structures[p1_core_id].hp = 1

        # p2 attacks p1's core
        atk = make_action(
            state, p2_id, ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": p1_core_id},
        )
        submit_action(state, atk)
        run_heartbeat(state)

        # Core should be destroyed
        assert p1_core_id not in state.structures
        # Player should still be alive because they have an outpost
        assert p1.alive is True
        # sanctuary_core_structure_id should be cleared
        assert p1.sanctuary_core_structure_id is None

    def test_no_outpost_means_death(self):
        """Destroy sanctuary core without outpost — player dies."""
        state, p1_id, p2_id = _ow_two_players()
        p1 = state.players[p1_id]
        _give_resources(state, p2_id)

        p1_core_sector = p1.sanctuary_sector_id
        p1_core_id = p1.sanctuary_core_structure_id
        adj_sectors = state.world.sectors[p1_core_sector].adjacent_sector_ids

        # p2 needs an attack node adjacent to p1's core
        p2_atk_sector = adj_sectors[0]
        state.world.sectors[p2_atk_sector].controller_player_id = p2_id
        _build_ow_structure(state, p2_id, p2_atk_sector, StructureType.ATTACK_NODE)

        # Weaken core to 1 HP
        state.structures[p1_core_id].hp = 1

        atk = make_action(
            state, p2_id, ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": p1_core_id},
        )
        submit_action(state, atk)
        run_heartbeat(state)

        assert p1_core_id not in state.structures
        # No outpost — player should be dead
        assert p1.alive is False


class TestShieldGeneratorHalvesDamage:
    def test_shield_generator_halves_damage(self):
        """Attack does 5 instead of 10 with shield generator in same sector."""
        state, p1_id, p2_id = _ow_two_players()
        _give_resources(state, p1_id)
        _give_resources(state, p2_id)

        p1 = state.players[p1_id]
        p1_sector = p1.sanctuary_sector_id
        adj_sectors = state.world.sectors[p1_sector].adjacent_sector_ids
        target_sector = adj_sectors[0]
        state.world.sectors[target_sector].controller_player_id = p1_id

        # p1 builds a tower (20 HP) and a shield generator in the same sector
        tower_id = _build_ow_structure(state, p1_id, target_sector, StructureType.TOWER)
        _build_ow_structure(state, p1_id, target_sector, StructureType.SHIELD_GENERATOR)

        # p2 builds attack node adjacent
        p2_atk_sector = None
        for adj in state.world.sectors[target_sector].adjacent_sector_ids:
            if adj != p1_sector:
                p2_atk_sector = adj
                break
        if p2_atk_sector is None:
            p2_atk_sector = p1_sector  # fallback — use any adjacent
            # This might not work if it's HAVEN; let's pick a different approach
        state.world.sectors[p2_atk_sector].controller_player_id = p2_id
        _build_ow_structure(state, p2_id, p2_atk_sector, StructureType.ATTACK_NODE)

        # Attack the tower — should do 5 damage (10 / 2) instead of 10
        atk = make_action(
            state, p2_id, ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": tower_id},
        )
        submit_action(state, atk)
        run_heartbeat(state)

        assert state.structures[tower_id].hp == 15  # 20 - 5 = 15


class TestShieldGeneratorOnlySameSector:
    def test_shield_generator_only_same_sector(self):
        """Shield in different sector doesn't reduce damage."""
        state, p1_id, p2_id = _ow_two_players()
        _give_resources(state, p1_id)
        _give_resources(state, p2_id)

        p1 = state.players[p1_id]
        p1_sector = p1.sanctuary_sector_id
        adj_sectors = state.world.sectors[p1_sector].adjacent_sector_ids
        target_sector = adj_sectors[0]
        state.world.sectors[target_sector].controller_player_id = p1_id

        # p1 builds tower in target_sector
        tower_id = _build_ow_structure(state, p1_id, target_sector, StructureType.TOWER)

        # p1 builds shield generator in a DIFFERENT sector
        other_sector = adj_sectors[1] if len(adj_sectors) > 1 else p1_sector
        state.world.sectors[other_sector].controller_player_id = p1_id
        _build_ow_structure(state, p1_id, other_sector, StructureType.SHIELD_GENERATOR)

        # p2 builds attack node
        p2_atk_sector = None
        for adj in state.world.sectors[target_sector].adjacent_sector_ids:
            if adj != p1_sector and adj != target_sector:
                p2_atk_sector = adj
                break
        if p2_atk_sector is None:
            # fallback: use any adjacent sector
            for adj in state.world.sectors[target_sector].adjacent_sector_ids:
                if adj != target_sector:
                    p2_atk_sector = adj
                    break
        state.world.sectors[p2_atk_sector].controller_player_id = p2_id
        _build_ow_structure(state, p2_id, p2_atk_sector, StructureType.ATTACK_NODE)

        # Attack the tower — should do full 10 damage (no shield in same sector)
        atk = make_action(
            state, p2_id, ActionType.ATTACK_STRUCTURE,
            {"target_structure_id": tower_id},
        )
        submit_action(state, atk)
        run_heartbeat(state)

        assert state.structures[tower_id].hp == 10  # 20 - 10 = 10


class TestTradeHubZeroEnergyTransfer:
    def test_trade_hub_zero_energy_transfer(self):
        """Transfer costs 0 energy with trade hub."""
        state = init_two_player_game()
        p1 = state.players["p1"]
        p1.metal = 50
        p1.data = 10

        # Build Trade Hub for p1 in F1
        build_structure_in_frontier(state, "p1", "F1", StructureType.TRADE_HUB)

        # Verify trade hub exists
        trade_hubs = [
            s for s in state.structures.values()
            if s.structure_type == StructureType.TRADE_HUB and s.owner_player_id == "p1"
        ]
        assert len(trade_hubs) == 1

        # Create transfer action and check energy cost is 0
        transfer_action = make_action(
            state, "p1", ActionType.TRANSFER_RESOURCE,
            {"target_player_id": "p2", "resource_type": "METAL", "amount": 5},
        )
        cost = get_action_energy_cost(transfer_action, state)
        assert cost == 0

    def test_no_trade_hub_transfer_costs_energy(self):
        """Without trade hub, transfer costs normal energy."""
        state = init_two_player_game()
        transfer_action = make_action(
            state, "p1", ActionType.TRANSFER_RESOURCE,
            {"target_player_id": "p2", "resource_type": "METAL", "amount": 5},
        )
        cost = get_action_energy_cost(transfer_action, state)
        assert cost == 1  # Normal cost


class TestBuildCostsResources:
    def test_outpost_costs_metal_and_data(self):
        """Outpost costs 15 metal + 2 data."""
        state = init_two_player_game()
        p1 = state.players["p1"]
        p1.metal = 15
        p1.data = 2

        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.OUTPOST.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        # Check resources deducted
        assert p1.metal == 0  # 15 - 15
        assert p1.data == 0   # 2 - 2

        outposts = [
            s for s in state.structures.values()
            if s.structure_type == StructureType.OUTPOST
        ]
        assert len(outposts) == 1

    def test_shield_generator_costs_metal_and_biomass(self):
        """Shield Generator costs 8 metal + 5 biomass."""
        state = init_two_player_game()
        p1 = state.players["p1"]
        p1.metal = 8
        p1.data = 0
        p1.biomass = 5

        action = make_action(
            state, "p1", ActionType.BUILD_STRUCTURE,
            {"sector_id": "F1", "structure_type": StructureType.SHIELD_GENERATOR.value},
        )
        submit_action(state, action)
        run_heartbeat(state)

        assert p1.metal == 0     # 8 - 8
        assert p1.biomass == 0   # 5 - 5

        shields = [
            s for s in state.structures.values()
            if s.structure_type == StructureType.SHIELD_GENERATOR
        ]
        assert len(shields) == 1

    def test_build_energy_costs(self):
        """Verify build energy costs for new structures."""
        assert BUILD_ENERGY_COSTS[StructureType.OUTPOST] == 10
        assert BUILD_ENERGY_COSTS[StructureType.SHIELD_GENERATOR] == 6
        assert BUILD_ENERGY_COSTS[StructureType.TRADE_HUB] == 7
