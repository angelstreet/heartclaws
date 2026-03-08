from __future__ import annotations

from .actions import get_action_energy_cost, resolve_action, validate_action
from .config import (
    BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT,
    DATA_HARVESTER_DATA_PER_HEARTBEAT,
    EXTRACTOR_METAL_PER_HEARTBEAT,
)
from .conflict import resolve_attack_structure
from .control import recompute_all_frontier_control
from .energy import (
    apply_upkeep_deactivations,
    compute_player_available_energy,
    compute_player_income,
    compute_player_upkeep,
    finalize_player_reserve,
)
from .enums import ActionStatus, ActionType, ResourceType, StructureType
from .events import (
    emit_action_failed,
    emit_action_resolved,
    emit_energy_computed,
    emit_heartbeat_completed,
    emit_heartbeat_started,
)
from .models import GameState, HeartbeatResult


def _has_active_circuit_foundry(state: GameState, owner_player_id: str, sector_id: str) -> bool:
    """Check if the owner has an active CIRCUIT_FOUNDRY in the given sector."""
    sector = state.world.sectors.get(sector_id)
    if sector is None:
        return False
    for st_id in sector.structure_ids:
        s = state.structures.get(st_id)
        if (
            s is not None
            and s.owner_player_id == owner_player_id
            and s.structure_type == StructureType.CIRCUIT_FOUNDRY
            and s.active
        ):
            return True
    return False


def run_heartbeat(state: GameState) -> HeartbeatResult:
    # 1. Increment heartbeat
    state.heartbeat += 1
    events_start_idx = len(state.event_log)

    # 2. Emit HEARTBEAT_STARTED
    emit_heartbeat_started(state)

    # 3. Collect pending actions submitted before this heartbeat
    pending = [a for a in state.actions_pending if a.submitted_heartbeat < state.heartbeat]

    # 4. Reset energy spent
    for player in state.players.values():
        player.energy_spent_this_heartbeat = 0

    # 5. Upkeep deactivations
    for pid in sorted(state.players):
        apply_upkeep_deactivations(state, pid)

    # 6. Emit ENERGY_COMPUTED per player
    for pid in sorted(state.players):
        player = state.players[pid]
        income = compute_player_income(state, pid)
        upkeep = compute_player_upkeep(state, pid)
        available = compute_player_available_energy(state, pid)
        emit_energy_computed(state, pid, income, upkeep, available, player.energy_reserve)

    # 7. Passive resource production (extractors, data harvesters, bio cultivators)
    for pid in sorted(state.players):
        player = state.players[pid]
        for structure in state.structures.values():
            if structure.owner_player_id != pid or not structure.active:
                continue
            sector = state.world.sectors.get(structure.sector_id)
            if sector is None:
                continue

            if structure.structure_type == StructureType.EXTRACTOR:
                has_metal_node = any(
                    node.resource_type == ResourceType.METAL and not node.depleted
                    for node in sector.resource_nodes
                )
                if has_metal_node:
                    production = EXTRACTOR_METAL_PER_HEARTBEAT
                    if _has_active_circuit_foundry(state, pid, structure.sector_id):
                        production = int(production * 1.2)
                    player.metal += production
                    player.total_resources_produced += production

            elif structure.structure_type == StructureType.DATA_HARVESTER:
                has_data_node = any(
                    node.resource_type == ResourceType.DATA and not node.depleted
                    for node in sector.resource_nodes
                )
                if has_data_node:
                    production = DATA_HARVESTER_DATA_PER_HEARTBEAT
                    if _has_active_circuit_foundry(state, pid, structure.sector_id):
                        production = int(production * 1.2)
                    player.data += production
                    player.total_resources_produced += production

            elif structure.structure_type == StructureType.BIO_CULTIVATOR:
                has_biomass_node = any(
                    node.resource_type == ResourceType.BIOMASS and not node.depleted
                    for node in sector.resource_nodes
                )
                if has_biomass_node:
                    production = BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT
                    if _has_active_circuit_foundry(state, pid, structure.sector_id):
                        production = int(production * 1.2)
                    player.biomass += production
                    player.total_resources_produced += production

    # 7b. Trade deal execution & espionage cleanup
    for pid in sorted(state.players):
        player = state.players[pid]

        # Execute active trade deals
        remaining_deals = []
        for deal in player.active_trade_deals:
            if deal["expires_at"] > state.heartbeat:
                target = state.players.get(deal["target_player_id"])
                if target is not None:
                    rt = deal["resource_type"]
                    amt = deal["amount"]
                    has_enough = False
                    if rt == "METAL" and player.metal >= amt:
                        player.metal -= amt
                        target.metal += amt
                        has_enough = True
                    elif rt == "DATA" and player.data >= amt:
                        player.data -= amt
                        target.data += amt
                        has_enough = True
                    elif rt == "BIOMASS" and player.biomass >= amt:
                        player.biomass -= amt
                        target.biomass += amt
                        has_enough = True
                    if has_enough:
                        player.trade_volume_total += amt
                remaining_deals.append(deal)
        player.active_trade_deals = remaining_deals

        # Clean up expired espionage reveals
        player.espionage_reveals = {
            tid: expires_at
            for tid, expires_at in player.espionage_reveals.items()
            if expires_at > state.heartbeat
        }

    # 8. Sort actions deterministically
    pending.sort(key=lambda a: (-a.priority, a.submitted_heartbeat, a.issuer_player_id, a.action_id))

    # 9. Resolve actions
    for action in pending:
        vr = validate_action(state, action)
        if not vr.accepted:
            action.status = ActionStatus.FAILED
            action.failure_reason = vr.reason
            emit_action_failed(state, action)
        else:
            if action.action_type == ActionType.ATTACK_STRUCTURE:
                resolve_attack_structure(state, action)
                action.energy_cost = get_action_energy_cost(action)
                state.players[action.issuer_player_id].energy_spent_this_heartbeat += action.energy_cost
            else:
                resolve_action(state, action)
            action.status = ActionStatus.RESOLVED
            emit_action_resolved(state, action)

    # 10. Recompute frontier control
    recompute_all_frontier_control(state)

    # 11. Finalize reserves
    for pid in sorted(state.players):
        finalize_player_reserve(state, pid)

    # 12. Emit HEARTBEAT_COMPLETED
    emit_heartbeat_completed(state)

    # 13. Remove resolved/failed actions
    resolved_or_failed = {a.action_id for a in pending}
    state.actions_pending = [a for a in state.actions_pending if a.action_id not in resolved_or_failed]

    # 14. Return result
    heartbeat_events = state.event_log[events_start_idx:]
    return HeartbeatResult(
        heartbeat=state.heartbeat,
        events=list(heartbeat_events),
        state=state,
    )
