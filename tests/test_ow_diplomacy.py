"""Tests for Phase OW-4: Diplomacy."""

from engine.config import ACTION_ENERGY_COSTS, ATTACK_DAMAGE, STRUCTURE_CATALOG
from engine.enums import (
    ActionStatus,
    ActionType,
    DiplomaticStance,
    SectorType,
    StructureType,
)
from engine.models import Action, GameState, StructureState, next_id
from engine.openworld import (
    are_mutual_allies,
    get_diplomatic_relations,
    get_messages,
    init_open_world,
    join_open_world,
    send_message,
    set_diplomatic_stance,
)
from engine.actions import validate_action, resolve_action, get_action_energy_cost
from engine.control import recompute_sector_control


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_player_state() -> tuple[GameState, str, str]:
    """Create an open world with two players and return (state, p1_id, p2_id)."""
    state = init_open_world(seed=42)
    r1 = join_open_world(state, "Alice")
    r2 = join_open_world(state, "Bob")
    return state, r1["player_id"], r2["player_id"]


def _place_attack_node(state: GameState, owner_id: str, sector_id: str) -> str:
    """Place an active ATTACK_NODE in a sector for the given player."""
    catalog = STRUCTURE_CATALOG[StructureType.ATTACK_NODE]
    st_id = next_id(state, "st")
    structure = StructureState(
        structure_id=st_id,
        owner_player_id=owner_id,
        sector_id=sector_id,
        structure_type=StructureType.ATTACK_NODE,
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


def _place_tower(state: GameState, owner_id: str, sector_id: str) -> str:
    """Place an active TOWER in a sector for the given player."""
    catalog = STRUCTURE_CATALOG[StructureType.TOWER]
    st_id = next_id(state, "st")
    structure = StructureState(
        structure_id=st_id,
        owner_player_id=owner_id,
        sector_id=sector_id,
        structure_type=StructureType.TOWER,
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


def _find_frontier_sector(state: GameState) -> str:
    """Find any FRONTIER sector."""
    for sid, sec in state.world.sectors.items():
        if sec.sector_type == SectorType.FRONTIER:
            return sid
    # Fallback: use a SETTLED sector
    for sid, sec in state.world.sectors.items():
        if sec.sector_type in (SectorType.SETTLED, SectorType.WASTELAND):
            return sid
    raise RuntimeError("No frontier/settled sector found")


def _setup_attack_scenario(state: GameState, attacker_id: str, defender_id: str):
    """Set up a scenario where attacker can attack defender's structure.

    Returns (target_structure_id, sector_id).
    """
    # Find a non-safe sector
    sector_id = _find_frontier_sector(state)
    sector = state.world.sectors[sector_id]

    # Place a target structure owned by defender
    target_id = _place_tower(state, defender_id, sector_id)

    # Place an attack node owned by attacker in the same sector
    _place_attack_node(state, attacker_id, sector_id)

    # Give attacker control of sector so they can attack
    sector.controller_player_id = attacker_id

    return target_id, sector_id


# ---------------------------------------------------------------------------
# Tests: Stance management
# ---------------------------------------------------------------------------


class TestSetStanceAlly:
    def test_set_stance_ally(self):
        """Set stance to ALLY and verify it's stored."""
        state, p1, p2 = _two_player_state()
        result = set_diplomatic_stance(state, p1, p2, DiplomaticStance.ALLY)

        assert result["new_stance"] == "ALLY"
        assert result["old_stance"] == "NEUTRAL"
        assert state.players[p1].diplomacy_stance[p2] == DiplomaticStance.ALLY


class TestSetStanceHostile:
    def test_set_stance_hostile(self):
        """Set stance to HOSTILE and verify it's stored."""
        state, p1, p2 = _two_player_state()
        result = set_diplomatic_stance(state, p1, p2, DiplomaticStance.HOSTILE)

        assert result["new_stance"] == "HOSTILE"
        assert result["old_stance"] == "NEUTRAL"
        assert state.players[p1].diplomacy_stance[p2] == DiplomaticStance.HOSTILE


# ---------------------------------------------------------------------------
# Tests: Combat effects
# ---------------------------------------------------------------------------


class TestAllyCannotAttack:
    def test_ally_cannot_attack(self):
        """Attack rejected when stance is ALLY toward target's owner."""
        state, p1, p2 = _two_player_state()
        target_id, sector_id = _setup_attack_scenario(state, p1, p2)

        # Set ALLY stance
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.ALLY)

        # Try to attack
        action = Action(
            action_id="act_001",
            issuer_player_id=p1,
            issuer_subagent_id=None,
            action_type=ActionType.ATTACK_STRUCTURE,
            payload={"target_structure_id": target_id},
        )
        result = validate_action(state, action)
        assert not result.accepted
        assert result.reason == "Cannot attack ally"


class TestHostileBonusDamage:
    def test_hostile_bonus_damage(self):
        """HOSTILE stance gives +50% attack damage (10 -> 15)."""
        state, p1, p2 = _two_player_state()
        target_id, sector_id = _setup_attack_scenario(state, p1, p2)

        # Set HOSTILE stance
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.HOSTILE)

        target = state.structures[target_id]
        original_hp = target.hp

        # Resolve attack
        action = Action(
            action_id="act_001",
            issuer_player_id=p1,
            issuer_subagent_id=None,
            action_type=ActionType.ATTACK_STRUCTURE,
            payload={"target_structure_id": target_id},
        )
        # Give player enough energy
        state.players[p1].energy_reserve = 100
        resolve_action(state, action)

        expected_damage = ATTACK_DAMAGE + ATTACK_DAMAGE // 2  # 10 + 5 = 15
        assert target.hp == original_hp - expected_damage


# ---------------------------------------------------------------------------
# Tests: Transfer effects
# ---------------------------------------------------------------------------


class TestAllyTransferZeroEnergy:
    def test_ally_transfer_zero_energy(self):
        """TRANSFER_RESOURCE costs 0 energy when stance is ALLY."""
        state, p1, p2 = _two_player_state()

        # Set ALLY stance
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.ALLY)

        action = Action(
            action_id="act_001",
            issuer_player_id=p1,
            issuer_subagent_id=None,
            action_type=ActionType.TRANSFER_RESOURCE,
            payload={
                "target_player_id": p2,
                "resource_type": "METAL",
                "amount": 5,
            },
        )
        cost = get_action_energy_cost(action, state)
        assert cost == 0

    def test_neutral_transfer_normal_cost(self):
        """TRANSFER_RESOURCE costs normal energy when stance is NEUTRAL."""
        state, p1, p2 = _two_player_state()

        action = Action(
            action_id="act_001",
            issuer_player_id=p1,
            issuer_subagent_id=None,
            action_type=ActionType.TRANSFER_RESOURCE,
            payload={
                "target_player_id": p2,
                "resource_type": "METAL",
                "amount": 5,
            },
        )
        cost = get_action_energy_cost(action, state)
        assert cost == ACTION_ENERGY_COSTS[ActionType.TRANSFER_RESOURCE]


class TestHostileTransferBlocked:
    def test_hostile_transfer_blocked(self):
        """TRANSFER_RESOURCE rejected when stance is HOSTILE."""
        state, p1, p2 = _two_player_state()

        # Set HOSTILE stance
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.HOSTILE)

        action = Action(
            action_id="act_001",
            issuer_player_id=p1,
            issuer_subagent_id=None,
            action_type=ActionType.TRANSFER_RESOURCE,
            payload={
                "target_player_id": p2,
                "resource_type": "METAL",
                "amount": 5,
            },
        )
        result = validate_action(state, action)
        assert not result.accepted
        assert result.reason == "Cannot transfer to hostile player"


# ---------------------------------------------------------------------------
# Tests: Alliance broken event
# ---------------------------------------------------------------------------


class TestAllianceBrokenEvent:
    def test_alliance_broken_event(self):
        """Switching from ALLY to something else generates ALLIANCE_BROKEN event."""
        state, p1, p2 = _two_player_state()

        # First set ALLY
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.ALLY)
        events_before = len(state.event_log)

        # Now break it
        result = set_diplomatic_stance(state, p1, p2, DiplomaticStance.NEUTRAL)
        assert result.get("alliance_broken") is True

        # Check event was emitted
        new_events = state.event_log[events_before:]
        assert len(new_events) == 1
        assert new_events[0].event_type == "ALLIANCE_BROKEN"
        assert new_events[0].actor_player_id == p1
        assert new_events[0].target_id == p2

    def test_no_event_when_not_breaking_ally(self):
        """No ALLIANCE_BROKEN event when changing from NEUTRAL to HOSTILE."""
        state, p1, p2 = _two_player_state()
        events_before = len(state.event_log)

        set_diplomatic_stance(state, p1, p2, DiplomaticStance.HOSTILE)

        new_events = state.event_log[events_before:]
        alliance_events = [e for e in new_events if e.event_type == "ALLIANCE_BROKEN"]
        assert len(alliance_events) == 0


# ---------------------------------------------------------------------------
# Tests: Messaging
# ---------------------------------------------------------------------------


class TestSendAndReceiveMessage:
    def test_send_and_receive_message(self):
        """Message round-trip: send, then retrieve."""
        state, p1, p2 = _two_player_state()
        state.heartbeat = 5

        result = send_message(state, p1, p2, "Hello Bob!")
        assert result["from"] == p1
        assert result["to"] == p2
        assert result["heartbeat"] == 5
        assert "message_id" in result

        # Retrieve messages for p2
        msgs = get_messages(state, p2)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Hello Bob!"
        assert msgs[0]["from"] == p1

        # p1 should have no messages
        assert len(get_messages(state, p1)) == 0


# ---------------------------------------------------------------------------
# Tests: Mutual ally shared influence
# ---------------------------------------------------------------------------


class TestMutualAllySharedInfluence:
    def test_mutual_ally_shared_influence(self):
        """Both players set ALLY: their influence combines for sector control."""
        state, p1, p2 = _two_player_state()

        # Find a frontier sector
        sector_id = _find_frontier_sector(state)
        sector = state.world.sectors[sector_id]

        # Clear existing structures in this sector
        sector.structure_ids.clear()

        # Add a third player with more individual influence
        r3 = join_open_world(state, "Charlie")
        p3 = r3["player_id"]

        # p1 has 3 influence (1 tower), p2 has 3 influence (1 tower), p3 has 6 (2 towers)
        _place_tower(state, p1, sector_id)    # influence: 3
        _place_tower(state, p2, sector_id)    # influence: 3
        _place_tower(state, p3, sector_id)    # influence: 3
        _place_tower(state, p3, sector_id)    # influence: 3 (total 6 for p3)

        # Without alliance: p3 controls (6 > 3)
        recompute_sector_control(state, sector_id)
        assert sector.controller_player_id == p3

        # Set mutual ALLY between p1 and p2
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.ALLY)
        set_diplomatic_stance(state, p2, p1, DiplomaticStance.ALLY)

        # Now p1+p2 combined = 6, p3 = 6 -> tie, but p1+p2 are in same ally group
        # Actually p1+p2 each get combined=6, p3 gets 6, so there's a 3-way tie
        # between p1 (6), p2 (6), p3 (6). p1 and p2 are in an ally group,
        # p3 is not. So it should be a tie -> controller = None
        # Let's give p1 one more tower to break the tie
        _place_tower(state, p1, sector_id)    # p1 now has 6 individual, p2 has 3
        # Combined for alliance: 6+3 = 9

        recompute_sector_control(state, sector_id)
        # p1+p2 combined = 9, p3 = 6 -> p1 or p2 should control
        # Since p1 has higher individual influence, p1 gets control
        assert sector.controller_player_id == p1

    def test_one_sided_ally_no_sharing(self):
        """Only one player sets ALLY — no influence sharing."""
        state, p1, p2 = _two_player_state()

        sector_id = _find_frontier_sector(state)
        sector = state.world.sectors[sector_id]
        sector.structure_ids.clear()

        r3 = join_open_world(state, "Charlie")
        p3 = r3["player_id"]

        # p1: 3, p2: 3, p3: 4
        _place_tower(state, p1, sector_id)
        _place_tower(state, p2, sector_id)
        _place_tower(state, p3, sector_id)
        # Give p3 a small edge
        catalog = STRUCTURE_CATALOG[StructureType.TOWER]
        extra_id = next_id(state, "st")
        extra = StructureState(
            structure_id=extra_id,
            owner_player_id=p3,
            sector_id=sector_id,
            structure_type=StructureType.TOWER,
            hp=catalog["hp"],
            max_hp=catalog["hp"],
            active=True,
            activation_heartbeat=state.heartbeat,
            influence=1,  # Just 1 extra influence
            energy_income_bonus=0,
            reserve_cap_bonus=0,
            throughput_cap_bonus=0,
            upkeep_cost=0,
            metal_cost=0,
            data_cost=0,
            biomass_cost=0,
        )
        state.structures[extra_id] = extra
        sector.structure_ids.append(extra_id)

        # Only one-sided ALLY
        set_diplomatic_stance(state, p1, p2, DiplomaticStance.ALLY)
        # p2 does NOT set ALLY toward p1

        recompute_sector_control(state, sector_id)
        # p1=3, p2=3, p3=4 -- no sharing since not mutual -> p3 wins
        assert sector.controller_player_id == p3
