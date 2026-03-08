from __future__ import annotations

from dataclasses import dataclass, field

from .config import GameConfig
from .enums import ActionStatus, ActionType, BiomeType, DiplomaticStance, ResourceType, SectorType, StructureType


@dataclass
class ResourceNode:
    node_id: str
    resource_type: ResourceType
    richness: int
    depleted: bool = False


@dataclass
class SectorState:
    sector_id: str
    name: str
    sector_type: SectorType
    adjacent_sector_ids: list[str] = field(default_factory=list)
    resource_nodes: list[ResourceNode] = field(default_factory=list)
    structure_ids: list[str] = field(default_factory=list)
    controller_player_id: str | None = None
    safe_owner_player_id: str | None = None
    biome: BiomeType | None = None


@dataclass
class WorldState:
    planet_id: str
    sectors: dict[str, SectorState] = field(default_factory=dict)


@dataclass
class PlayerState:
    player_id: str
    name: str
    alive: bool
    sanctuary_sector_id: str
    sanctuary_core_structure_id: str | None = None
    energy_reserve: int = 0
    energy_spent_this_heartbeat: int = 0
    metal: int = 0
    data: int = 0
    biomass: int = 0
    diplomacy_stance: dict[str, DiplomaticStance] = field(default_factory=dict)
    subagent_ids: list[str] = field(default_factory=list)
    spawn_heartbeat: int = 0
    last_active_heartbeat: int = 0
    gateway_id: str | None = None
    model: str | None = None
    structures_destroyed: int = 0
    structures_lost: int = 0
    espionage_reveals: dict[str, int] = field(default_factory=dict)  # target_player_id -> expires_at_heartbeat
    active_trade_deals: list[dict] = field(default_factory=list)  # {target_player_id, resource_type, amount, expires_at}
    trade_volume_total: int = 0
    resources_spent_on_structures: int = 0
    total_resources_produced: int = 0
    sectors_gained_history: list[int] = field(default_factory=list)  # heartbeat numbers when sectors were gained


@dataclass
class SubagentState:
    subagent_id: str
    owner_player_id: str
    name: str
    scope_sector_ids: list[str] | None = None
    scope_action_types: list[ActionType] | None = None
    mandate: str = ""
    upkeep_cost: int = 0
    active: bool = True


@dataclass
class StructureState:
    structure_id: str
    owner_player_id: str
    sector_id: str
    structure_type: StructureType
    hp: int
    max_hp: int
    active: bool
    activation_heartbeat: int
    influence: int
    energy_income_bonus: int
    reserve_cap_bonus: int
    throughput_cap_bonus: int
    upkeep_cost: int
    metal_cost: int
    data_cost: int
    biomass_cost: int


@dataclass
class Action:
    action_id: str
    issuer_player_id: str
    issuer_subagent_id: str | None
    action_type: ActionType
    payload: dict = field(default_factory=dict)
    energy_cost: int = 0
    submitted_heartbeat: int = 0
    priority: int = 0
    status: ActionStatus = ActionStatus.PENDING
    failure_reason: str | None = None


@dataclass
class Message:
    message_id: str
    from_player_id: str
    to_player_id: str
    content: str
    heartbeat: int


@dataclass
class Event:
    event_id: str
    heartbeat: int
    event_type: str
    actor_player_id: str | None = None
    actor_subagent_id: str | None = None
    target_id: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    accepted: bool
    action_id: str
    reason: str | None = None


@dataclass
class HeartbeatResult:
    heartbeat: int
    events: list[Event] = field(default_factory=list)
    state: GameState | None = None


@dataclass
class GameState:
    game_id: str
    heartbeat: int
    seed: int
    config: GameConfig = field(default_factory=GameConfig)
    world: WorldState = field(default_factory=lambda: WorldState(planet_id=""))
    players: dict[str, PlayerState] = field(default_factory=dict)
    subagents: dict[str, SubagentState] = field(default_factory=dict)
    structures: dict[str, StructureState] = field(default_factory=dict)
    actions_pending: list[Action] = field(default_factory=list)
    event_log: list[Event] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    id_counter: int = 0
    open_world: bool = False
    player_counter: int = 0
    season_history: list[dict] = field(default_factory=list)
    world_events_active: list[dict] = field(default_factory=list)
    player_elo: dict[str, int] = field(default_factory=dict)
    session_id: str = ""
    session_name: str = ""


def next_id(state: GameState, prefix: str) -> str:
    state.id_counter += 1
    return f"{prefix}_{state.id_counter:03d}"
