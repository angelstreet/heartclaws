from __future__ import annotations

from .models import Event, GameState, next_id


def emit_event(
    state: GameState,
    event_type: str,
    actor_player_id: str | None = None,
    actor_subagent_id: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
) -> Event:
    event = Event(
        event_id=next_id(state, "evt"),
        heartbeat=state.heartbeat,
        event_type=event_type,
        actor_player_id=actor_player_id,
        actor_subagent_id=actor_subagent_id,
        target_id=target_id,
        details=details or {},
    )
    state.event_log.append(event)
    return event


def emit_heartbeat_started(state: GameState) -> Event:
    return emit_event(state, "HEARTBEAT_STARTED", details={"heartbeat": state.heartbeat})


def emit_heartbeat_completed(state: GameState) -> Event:
    return emit_event(state, "HEARTBEAT_COMPLETED")


def emit_energy_computed(
    state: GameState, player_id: str, income: int, upkeep: int, available: int, reserve: int
) -> Event:
    return emit_event(
        state,
        "ENERGY_COMPUTED",
        actor_player_id=player_id,
        details={
            "income": income,
            "upkeep": upkeep,
            "available": available,
            "reserve": reserve,
        },
    )


def emit_upkeep_deactivation(
    state: GameState, player_id: str, target_id: str, target_type: str
) -> Event:
    return emit_event(
        state,
        "UPKEEP_DEACTIVATION",
        actor_player_id=player_id,
        target_id=target_id,
        details={"target_type": target_type},
    )


def emit_action_failed(state: GameState, action) -> Event:
    return emit_event(
        state,
        "ACTION_FAILED",
        actor_player_id=action.issuer_player_id,
        actor_subagent_id=action.issuer_subagent_id,
        details={
            "action_id": action.action_id,
            "action_type": action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type),
            "failure_reason": action.failure_reason,
        },
    )


def emit_action_resolved(state: GameState, action) -> Event:
    return emit_event(
        state,
        "ACTION_RESOLVED",
        actor_player_id=action.issuer_player_id,
        actor_subagent_id=action.issuer_subagent_id,
        details={
            "action_id": action.action_id,
            "action_type": action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type),
        },
    )


def emit_structure_built(
    state: GameState, player_id: str, structure_id: str, structure_type: str, sector_id: str
) -> Event:
    return emit_event(
        state,
        "STRUCTURE_BUILT",
        actor_player_id=player_id,
        target_id=structure_id,
        details={"structure_type": structure_type, "sector_id": sector_id},
    )


def emit_structure_removed(state: GameState, player_id: str, structure_id: str) -> Event:
    return emit_event(
        state,
        "STRUCTURE_REMOVED",
        actor_player_id=player_id,
        target_id=structure_id,
    )


def emit_structure_attacked(
    state: GameState, attacker_id: str, target_structure_id: str, damage: int, remaining_hp: int
) -> Event:
    return emit_event(
        state,
        "STRUCTURE_ATTACKED",
        actor_player_id=attacker_id,
        target_id=target_structure_id,
        details={"damage": damage, "remaining_hp": remaining_hp},
    )


def emit_structure_destroyed(
    state: GameState, player_id: str, structure_id: str, sector_id: str
) -> Event:
    return emit_event(
        state,
        "STRUCTURE_DESTROYED",
        actor_player_id=player_id,
        target_id=structure_id,
        details={"sector_id": sector_id},
    )


def emit_sector_control_changed(
    state: GameState, sector_id: str, old_controller: str | None, new_controller: str | None
) -> Event:
    return emit_event(
        state,
        "SECTOR_CONTROL_CHANGED",
        target_id=sector_id,
        details={"old_controller": old_controller, "new_controller": new_controller},
    )


def emit_subagent_created(state: GameState, player_id: str, subagent_id: str) -> Event:
    return emit_event(
        state,
        "SUBAGENT_CREATED",
        actor_player_id=player_id,
        target_id=subagent_id,
    )


def emit_subagent_deactivated(state: GameState, player_id: str, subagent_id: str) -> Event:
    return emit_event(
        state,
        "SUBAGENT_DEACTIVATED",
        actor_player_id=player_id,
        target_id=subagent_id,
    )
