from __future__ import annotations

import dataclasses
import json
from enum import Enum
from typing import Any

from .config import GameConfig
from .enums import (
    ActionStatus,
    ActionType,
    BiomeType,
    DiplomaticStance,
    ResourceType,
    SectorType,
    StructureType,
)
from .models import (
    Action,
    Event,
    GameState,
    Message,
    PlayerState,
    ResourceNode,
    SectorState,
    StructureState,
    SubagentState,
    WorldState,
)


def _to_dict(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    return obj


def _resource_node(d: dict) -> ResourceNode:
    return ResourceNode(
        node_id=d["node_id"],
        resource_type=ResourceType(d["resource_type"]),
        richness=d["richness"],
        depleted=d["depleted"],
    )


def _sector_state(d: dict) -> SectorState:
    biome_raw = d.get("biome")
    return SectorState(
        sector_id=d["sector_id"],
        name=d["name"],
        sector_type=SectorType(d["sector_type"]),
        adjacent_sector_ids=d.get("adjacent_sector_ids", []),
        resource_nodes=[_resource_node(n) for n in d.get("resource_nodes", [])],
        structure_ids=d.get("structure_ids", []),
        controller_player_id=d.get("controller_player_id"),
        safe_owner_player_id=d.get("safe_owner_player_id"),
        biome=BiomeType(biome_raw) if biome_raw else None,
    )


def _world_state(d: dict) -> WorldState:
    return WorldState(
        planet_id=d["planet_id"],
        sectors={k: _sector_state(v) for k, v in d.get("sectors", {}).items()},
    )


def _player_state(d: dict) -> PlayerState:
    return PlayerState(
        player_id=d["player_id"],
        name=d["name"],
        alive=d["alive"],
        sanctuary_sector_id=d["sanctuary_sector_id"],
        sanctuary_core_structure_id=d.get("sanctuary_core_structure_id"),
        energy_reserve=d.get("energy_reserve", 0),
        energy_spent_this_heartbeat=d.get("energy_spent_this_heartbeat", 0),
        metal=d.get("metal", 0),
        data=d.get("data", 0),
        biomass=d.get("biomass", 0),
        diplomacy_stance={
            k: DiplomaticStance(v) for k, v in d.get("diplomacy_stance", {}).items()
        },
        subagent_ids=d.get("subagent_ids", []),
        spawn_heartbeat=d.get("spawn_heartbeat", 0),
        last_active_heartbeat=d.get("last_active_heartbeat", 0),
        gateway_id=d.get("gateway_id"),
        structures_destroyed=d.get("structures_destroyed", 0),
        structures_lost=d.get("structures_lost", 0),
    )


def _subagent_state(d: dict) -> SubagentState:
    scope_action_types = d.get("scope_action_types")
    return SubagentState(
        subagent_id=d["subagent_id"],
        owner_player_id=d["owner_player_id"],
        name=d["name"],
        scope_sector_ids=d.get("scope_sector_ids"),
        scope_action_types=(
            [ActionType(v) for v in scope_action_types]
            if scope_action_types is not None
            else None
        ),
        mandate=d.get("mandate", ""),
        upkeep_cost=d.get("upkeep_cost", 0),
        active=d.get("active", True),
    )


def _structure_state(d: dict) -> StructureState:
    return StructureState(
        structure_id=d["structure_id"],
        owner_player_id=d["owner_player_id"],
        sector_id=d["sector_id"],
        structure_type=StructureType(d["structure_type"]),
        hp=d["hp"],
        max_hp=d["max_hp"],
        active=d["active"],
        activation_heartbeat=d["activation_heartbeat"],
        influence=d["influence"],
        energy_income_bonus=d["energy_income_bonus"],
        reserve_cap_bonus=d["reserve_cap_bonus"],
        throughput_cap_bonus=d["throughput_cap_bonus"],
        upkeep_cost=d["upkeep_cost"],
        metal_cost=d["metal_cost"],
        data_cost=d["data_cost"],
        biomass_cost=d["biomass_cost"],
    )


def _action(d: dict) -> Action:
    return Action(
        action_id=d["action_id"],
        issuer_player_id=d["issuer_player_id"],
        issuer_subagent_id=d.get("issuer_subagent_id"),
        action_type=ActionType(d["action_type"]),
        payload=d.get("payload", {}),
        energy_cost=d.get("energy_cost", 0),
        submitted_heartbeat=d.get("submitted_heartbeat", 0),
        priority=d.get("priority", 0),
        status=ActionStatus(d.get("status", "PENDING")),
        failure_reason=d.get("failure_reason"),
    )


def _event(d: dict) -> Event:
    return Event(
        event_id=d["event_id"],
        heartbeat=d["heartbeat"],
        event_type=d["event_type"],
        actor_player_id=d.get("actor_player_id"),
        actor_subagent_id=d.get("actor_subagent_id"),
        target_id=d.get("target_id"),
        details=d.get("details", {}),
    )


def _game_config(d: dict) -> GameConfig:
    return GameConfig(**d)


def _message(d: dict) -> Message:
    return Message(
        message_id=d["message_id"],
        from_player_id=d["from_player_id"],
        to_player_id=d["to_player_id"],
        content=d["content"],
        heartbeat=d["heartbeat"],
    )


def _game_state(d: dict) -> GameState:
    return GameState(
        game_id=d["game_id"],
        heartbeat=d["heartbeat"],
        seed=d["seed"],
        config=_game_config(d.get("config", {})),
        world=_world_state(d.get("world", {"planet_id": ""})),
        players={k: _player_state(v) for k, v in d.get("players", {}).items()},
        subagents={k: _subagent_state(v) for k, v in d.get("subagents", {}).items()},
        structures={k: _structure_state(v) for k, v in d.get("structures", {}).items()},
        actions_pending=[_action(a) for a in d.get("actions_pending", [])],
        event_log=[_event(e) for e in d.get("event_log", [])],
        messages=[_message(m) for m in d.get("messages", [])],
        id_counter=d.get("id_counter", 0),
        open_world=d.get("open_world", False),
        player_counter=d.get("player_counter", 0),
        season_history=d.get("season_history", []),
        world_events_active=d.get("world_events_active", []),
        player_elo=d.get("player_elo", {}),
    )


def save_game(state: GameState, path: str) -> None:
    data = _to_dict(state)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_game(path: str) -> GameState:
    with open(path, "r") as f:
        data = json.load(f)
    return _game_state(data)
