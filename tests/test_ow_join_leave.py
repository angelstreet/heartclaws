"""Tests for Phase OW-2: Dynamic Join/Leave."""

from engine.enums import SectorType, StructureType, ActionType, ActionStatus
from engine.models import Action, GameState, StructureState, next_id
from engine.openworld import (
    init_open_world,
    join_open_world,
    leave_open_world,
    apply_open_world_decay,
)
from engine.config import STRUCTURE_CATALOG
from engine.actions import validate_action


class TestInitOpenWorld:
    def test_init_open_world(self):
        """Creates 64-sector world with no players."""
        state = init_open_world(seed=42)
        assert len(state.world.sectors) == 64
        assert len(state.players) == 0
        assert state.open_world is True
        assert state.game_id == "openworld_42"
        assert state.player_counter == 0


class TestJoinOpenWorld:
    def test_join_assigns_haven(self):
        """First player spawns in a HAVEN."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        sector = state.world.sectors[result["sector_id"]]
        assert sector.sector_type == SectorType.HAVEN

    def test_join_multiple_players(self):
        """3 players get different HAVENs."""
        state = init_open_world(seed=42)
        r1 = join_open_world(state, "Alice")
        r2 = join_open_world(state, "Bob")
        r3 = join_open_world(state, "Charlie")

        sectors = {r1["sector_id"], r2["sector_id"], r3["sector_id"]}
        assert len(sectors) == 3  # All different

        for sid in sectors:
            assert state.world.sectors[sid].sector_type == SectorType.HAVEN

        # Player IDs are sequential
        assert r1["player_id"] == "p1"
        assert r2["player_id"] == "p2"
        assert r3["player_id"] == "p3"

    def test_join_gets_starting_resources(self):
        """20 metal, 5 data, 5 biomass."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        assert result["resources"] == {"metal": 20, "data": 5, "biomass": 5}

        player = state.players[result["player_id"]]
        assert player.metal == 20
        assert player.data == 5
        assert player.biomass == 5

    def test_join_creates_sanctuary_core(self):
        """Structure exists in spawn sector."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        sid = result["sector_id"]

        player = state.players[pid]
        assert player.sanctuary_core_structure_id is not None

        structure = state.structures[player.sanctuary_core_structure_id]
        assert structure.structure_type == StructureType.SANCTUARY_CORE
        assert structure.sector_id == sid
        assert structure.owner_player_id == pid
        assert structure.hp == STRUCTURE_CATALOG[StructureType.SANCTUARY_CORE]["hp"]

        # Structure is listed in the sector
        assert player.sanctuary_core_structure_id in state.world.sectors[sid].structure_ids

    def test_join_overflow_to_settled(self):
        """When 8+ HAVENs taken, spawns in SETTLED."""
        state = init_open_world(seed=42)

        # Fill all 8 HAVENs
        haven_results = []
        for i in range(8):
            r = join_open_world(state, f"Player{i}")
            haven_results.append(r)

        # Verify all 8 are in HAVENs
        for r in haven_results:
            assert state.world.sectors[r["sector_id"]].sector_type == SectorType.HAVEN

        # 9th player should overflow to SETTLED
        r9 = join_open_world(state, "Overflow")
        sector_type = state.world.sectors[r9["sector_id"]].sector_type
        assert sector_type == SectorType.SETTLED

        # No grace period for non-HAVEN spawn
        assert r9["grace_expires"] == r9["spawn_heartbeat"]

    def test_join_sets_spawn_heartbeat(self):
        """spawn_heartbeat and last_active_heartbeat are set correctly."""
        state = init_open_world(seed=42)
        state.heartbeat = 5
        result = join_open_world(state, "Alice")
        player = state.players[result["player_id"]]
        assert player.spawn_heartbeat == 5
        assert player.last_active_heartbeat == 5

    def test_join_with_gateway_id(self):
        """gateway_id is stored on the player."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice", gateway_id="gw_abc")
        player = state.players[result["player_id"]]
        assert player.gateway_id == "gw_abc"


class TestLeaveOpenWorld:
    def test_leave_sets_structures_to_50pct(self):
        """Structures lose half HP on leave."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]

        # Get the sanctuary core
        core_id = state.players[pid].sanctuary_core_structure_id
        original_hp = state.structures[core_id].max_hp

        summary = leave_open_world(state, pid)

        # Structure still exists but at 50% HP
        assert core_id in state.structures
        assert state.structures[core_id].hp == original_hp // 2

        assert len(summary["abandoned_structures"]) == 1
        assert summary["abandoned_structures"][0]["hp"] == original_hp // 2

    def test_leave_removes_player(self):
        """Player gone from state.players."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        assert pid in state.players

        leave_open_world(state, pid)
        assert pid not in state.players

    def test_leave_structures_become_neutral(self):
        """Structures have owner set to None after leave."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        core_id = state.players[pid].sanctuary_core_structure_id

        leave_open_world(state, pid)
        assert state.structures[core_id].owner_player_id is None

    def test_leave_clears_sector_control(self):
        """Sector control cleared for departing player."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        sid = result["sector_id"]

        assert state.world.sectors[sid].controller_player_id == pid
        leave_open_world(state, pid)
        assert state.world.sectors[sid].controller_player_id is None


class TestDecay:
    def test_decay_inactive_player(self):
        """After 30 HBs of no actions, structures take -2 HP/HB."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        core_id = state.players[pid].sanctuary_core_structure_id
        original_hp = state.structures[core_id].hp

        # Advance 30 heartbeats without activity
        state.heartbeat = 30

        events = apply_open_world_decay(state)
        assert len(events) > 0

        # Structure should have lost 2 HP
        assert state.structures[core_id].hp == original_hp - 2

    def test_decay_active_player_no_decay(self):
        """Active players don't get decay."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        core_id = state.players[pid].sanctuary_core_structure_id
        original_hp = state.structures[core_id].hp

        # Player stays active
        state.heartbeat = 29
        state.players[pid].last_active_heartbeat = 10

        events = apply_open_world_decay(state)
        # 29 - 10 = 19, not yet inactive
        assert state.structures[core_id].hp == original_hp

    def test_decay_destroys_at_zero_hp(self):
        """Structures at 0 HP are destroyed."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        core_id = state.players[pid].sanctuary_core_structure_id

        # Set HP to 1 so it goes to -1 after decay
        state.structures[core_id].hp = 1
        state.heartbeat = 30

        events = apply_open_world_decay(state)

        # Structure should be destroyed
        assert core_id not in state.structures
        destroyed_events = [e for e in events if e["type"] == "destroyed"]
        assert len(destroyed_events) == 1

    def test_decay_neutral_structures(self):
        """Neutral (ownerless) structures also decay."""
        state = init_open_world(seed=42)
        result = join_open_world(state, "Alice")
        pid = result["player_id"]
        core_id = state.players[pid].sanctuary_core_structure_id
        original_hp = state.structures[core_id].hp

        # Player leaves, structures become neutral
        leave_open_world(state, pid)
        state.heartbeat = 1  # Any heartbeat -- neutral always decays

        events = apply_open_world_decay(state)
        assert state.structures[core_id].hp == (original_hp // 2) - 2


class TestGracePeriod:
    def _setup_attacker_with_attack_node(self, state: GameState, target_sector_id: str) -> str:
        """Join a second player and give them an ATTACK_NODE near the target."""
        result = join_open_world(state, "Attacker")
        attacker_id = result["player_id"]
        attacker_sector = result["sector_id"]

        # Place an ATTACK_NODE in the attacker's sector or adjacent to target
        from engine.config import STRUCTURE_CATALOG
        cat = STRUCTURE_CATALOG[StructureType.ATTACK_NODE]
        atk_id = next_id(state, "st")
        atk_node = StructureState(
            structure_id=atk_id,
            owner_player_id=attacker_id,
            sector_id=target_sector_id,
            structure_type=StructureType.ATTACK_NODE,
            hp=cat["hp"],
            max_hp=cat["hp"],
            active=True,
            activation_heartbeat=0,
            influence=cat["influence"],
            energy_income_bonus=cat["energy_income_bonus"],
            reserve_cap_bonus=cat["reserve_cap_bonus"],
            throughput_cap_bonus=cat["throughput_cap_bonus"],
            upkeep_cost=cat["upkeep"],
            metal_cost=cat["metal_cost"],
            data_cost=cat["data_cost"],
            biomass_cost=cat["biomass_cost"],
        )
        state.structures[atk_id] = atk_node
        state.world.sectors[target_sector_id].structure_ids.append(atk_id)

        # Give attacker enough energy
        state.players[attacker_id].energy_reserve = 100

        return attacker_id

    def test_grace_period_blocks_attack(self):
        """Can't attack structures in HAVEN during grace period."""
        state = init_open_world(seed=42)
        state.heartbeat = 0

        # Defender joins at heartbeat 0
        defender = join_open_world(state, "Defender")
        defender_id = defender["player_id"]
        defender_sector = defender["sector_id"]
        defender_core = state.players[defender_id].sanctuary_core_structure_id

        assert state.world.sectors[defender_sector].sector_type == SectorType.HAVEN

        # Attacker
        attacker_id = self._setup_attacker_with_attack_node(state, defender_sector)

        # Try to attack at heartbeat 5 (within grace period: 0 + 10 = 10)
        state.heartbeat = 5
        action = Action(
            action_id="atk_001",
            issuer_player_id=attacker_id,
            issuer_subagent_id=None,
            action_type=ActionType.ATTACK_STRUCTURE,
            payload={"target_structure_id": defender_core},
        )

        vr = validate_action(state, action)
        assert not vr.accepted
        assert vr.reason == "Target is in spawn protection"

    def test_grace_period_expires(self):
        """Attack works after 10 HBs."""
        state = init_open_world(seed=42)
        state.heartbeat = 0

        # Defender joins at heartbeat 0
        defender = join_open_world(state, "Defender")
        defender_id = defender["player_id"]
        defender_sector = defender["sector_id"]
        defender_core = state.players[defender_id].sanctuary_core_structure_id

        assert state.world.sectors[defender_sector].sector_type == SectorType.HAVEN

        # Attacker
        attacker_id = self._setup_attacker_with_attack_node(state, defender_sector)

        # Attack at heartbeat 10 (grace expires: 0 + 10 = 10, condition is < heartbeat)
        state.heartbeat = 10
        action = Action(
            action_id="atk_002",
            issuer_player_id=attacker_id,
            issuer_subagent_id=None,
            action_type=ActionType.ATTACK_STRUCTURE,
            payload={"target_structure_id": defender_core},
        )

        vr = validate_action(state, action)
        assert vr.accepted
