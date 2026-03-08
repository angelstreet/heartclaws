from __future__ import annotations

import math

from .agents import validate_subagent_scope
from .config import (
    ACTION_ENERGY_COSTS,
    ATTACK_DAMAGE,
    BUILD_ENERGY_COSTS,
    REMOVE_REFUND_RATIO,
    STRUCTURE_CATALOG,
    SUBAGENT_DATA_COST,
    SUBAGENT_UPKEEP,
)
from .control import recompute_sector_control
from .energy import compute_player_available_energy
from .enums import ActionStatus, ActionType, ResourceType, SectorType, StructureType
from .events import (
    emit_structure_attacked,
    emit_structure_built,
    emit_structure_destroyed,
    emit_structure_removed,
)
from .models import (
    Action,
    GameState,
    StructureState,
    SubagentState,
    ValidationResult,
    next_id,
)


def get_action_energy_cost(action: Action) -> int:
    if action.action_type == ActionType.BUILD_STRUCTURE:
        raw = action.payload.get("structure_type")
        if raw is None:
            return 0
        try:
            st = StructureType(raw) if isinstance(raw, str) else raw
        except ValueError:
            return 0
        return BUILD_ENERGY_COSTS.get(st, 0)
    return ACTION_ENERGY_COSTS.get(action.action_type, 0)


def validate_action(state: GameState, action: Action) -> ValidationResult:
    aid = action.action_id

    # 1. Player exists and alive
    player = state.players.get(action.issuer_player_id)
    if player is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Player not found")
    if not player.alive:
        return ValidationResult(accepted=False, action_id=aid, reason="Player is dead")

    # 2. Subagent checks
    if action.issuer_subagent_id is not None:
        subagent = state.subagents.get(action.issuer_subagent_id)
        if subagent is None:
            return ValidationResult(accepted=False, action_id=aid, reason="Subagent not found")
        if not subagent.active:
            return ValidationResult(accepted=False, action_id=aid, reason="Subagent is not active")
        if subagent.owner_player_id != action.issuer_player_id:
            return ValidationResult(accepted=False, action_id=aid, reason="Subagent not owned by player")
        scope_err = validate_subagent_scope(state, subagent, action)
        if scope_err is not None:
            return ValidationResult(accepted=False, action_id=aid, reason=scope_err)

    # 3. Energy affordability
    cost = get_action_energy_cost(action)
    available = compute_player_available_energy(state, action.issuer_player_id)
    remaining = available - player.energy_spent_this_heartbeat
    if remaining < cost:
        return ValidationResult(
            accepted=False, action_id=aid,
            reason=f"Not enough energy (need {cost}, have {remaining})",
        )

    # Per-type validation
    at = action.action_type

    if at == ActionType.BUILD_STRUCTURE:
        return _validate_build(state, action, player)
    elif at == ActionType.REMOVE_STRUCTURE:
        return _validate_remove(state, action, player)
    elif at == ActionType.ATTACK_STRUCTURE:
        return _validate_attack(state, action, player)
    elif at == ActionType.SCAN_SECTOR:
        return _validate_scan(state, action, player)
    elif at == ActionType.CREATE_SUBAGENT:
        return _validate_create_subagent(state, action, player)
    elif at == ActionType.DEACTIVATE_SUBAGENT:
        return _validate_deactivate_subagent(state, action, player)
    elif at == ActionType.SET_POLICY:
        return _validate_set_policy(state, action, player)
    elif at == ActionType.TRANSFER_RESOURCE:
        return _validate_transfer(state, action, player)

    return ValidationResult(accepted=False, action_id=aid, reason="Unknown action type")


def _validate_build(state, action, player):
    aid = action.action_id
    payload = action.payload

    sector_id = payload.get("sector_id")
    raw_st = payload.get("structure_type")
    if sector_id is None or raw_st is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Missing sector_id or structure_type")

    try:
        structure_type = StructureType(raw_st) if isinstance(raw_st, str) else raw_st
    except ValueError:
        return ValidationResult(accepted=False, action_id=aid, reason=f"Invalid structure_type '{raw_st}'")

    sector = state.world.sectors.get(sector_id)
    if sector is None:
        return ValidationResult(accepted=False, action_id=aid, reason=f"Sector '{sector_id}' not found")

    catalog = STRUCTURE_CATALOG.get(structure_type)
    if catalog is None:
        return ValidationResult(accepted=False, action_id=aid, reason=f"Unknown structure type '{structure_type}'")

    allowed_sector = catalog["allowed_sector"]
    if sector.sector_type != allowed_sector:
        return ValidationResult(
            accepted=False, action_id=aid,
            reason=f"Structure '{structure_type.value}' not allowed in {sector.sector_type.value} sectors",
        )

    if sector.sector_type == SectorType.SAFE:
        if sector.safe_owner_player_id != player.player_id:
            return ValidationResult(accepted=False, action_id=aid, reason="Player is not safe zone owner")
    elif sector.sector_type == SectorType.FRONTIER:
        if sector.controller_player_id != player.player_id:
            if sector.controller_player_id is not None:
                return ValidationResult(accepted=False, action_id=aid, reason="Sector controlled by another player")
            adjacent_controlled = any(
                state.world.sectors[adj_id].controller_player_id == player.player_id
                for adj_id in sector.adjacent_sector_ids
                if adj_id in state.world.sectors
            )
            if not adjacent_controlled:
                return ValidationResult(
                    accepted=False, action_id=aid,
                    reason="Uncontrolled sector not adjacent to a player-controlled sector",
                )

    if player.metal < catalog["metal_cost"]:
        return ValidationResult(accepted=False, action_id=aid, reason="Not enough metal")
    if player.data < catalog["data_cost"]:
        return ValidationResult(accepted=False, action_id=aid, reason="Not enough data")
    if player.biomass < catalog["biomass_cost"]:
        return ValidationResult(accepted=False, action_id=aid, reason="Not enough biomass")

    # Resource node requirements for production structures
    _STRUCTURE_REQUIRED_RESOURCE: dict[StructureType, ResourceType] = {
        StructureType.EXTRACTOR: ResourceType.METAL,
        StructureType.DATA_HARVESTER: ResourceType.DATA,
        StructureType.BIO_CULTIVATOR: ResourceType.BIOMASS,
    }
    required_resource = _STRUCTURE_REQUIRED_RESOURCE.get(structure_type)
    if required_resource is not None:
        has_node = any(
            node.resource_type == required_resource and not node.depleted
            for node in sector.resource_nodes
        )
        if not has_node:
            return ValidationResult(
                accepted=False, action_id=aid,
                reason=f"Sector has no {required_resource.value} resource node",
            )

    return ValidationResult(accepted=True, action_id=aid)


def _validate_remove(state, action, player):
    aid = action.action_id
    structure_id = action.payload.get("structure_id")
    if structure_id is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Missing structure_id")

    structure = state.structures.get(structure_id)
    if structure is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Structure not found")
    if structure.owner_player_id != player.player_id:
        return ValidationResult(accepted=False, action_id=aid, reason="Player does not own structure")
    if structure.structure_type == StructureType.SANCTUARY_CORE:
        return ValidationResult(accepted=False, action_id=aid, reason="Cannot remove SANCTUARY_CORE")

    return ValidationResult(accepted=True, action_id=aid)


def _validate_attack(state, action, player):
    aid = action.action_id
    target_id = action.payload.get("target_structure_id")
    if target_id is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Missing target_structure_id")

    target = state.structures.get(target_id)
    if target is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Target structure not found")

    target_sector = state.world.sectors.get(target.sector_id)
    if target_sector is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Target sector not found")

    if target_sector.sector_type == SectorType.SAFE:
        return ValidationResult(accepted=False, action_id=aid, reason="Cannot attack structures in safe zones")

    # Open world spawn protection: HAVEN sectors have grace period
    if target_sector.sector_type == SectorType.HAVEN and state.open_world:
        # Find the player who spawned in this HAVEN
        for p in state.players.values():
            if p.sanctuary_sector_id == target_sector.sector_id:
                if p.spawn_heartbeat + 10 > state.heartbeat:
                    return ValidationResult(
                        accepted=False, action_id=aid,
                        reason="Target is in spawn protection",
                    )
                break

    if target_sector.sector_type not in (SectorType.FRONTIER, SectorType.HAVEN, SectorType.SETTLED, SectorType.WASTELAND):
        return ValidationResult(accepted=False, action_id=aid, reason="Target sector is not attackable")

    if target.owner_player_id == player.player_id:
        return ValidationResult(accepted=False, action_id=aid, reason="Cannot attack own structure")

    # Check attacker has an active ATTACK_NODE in target sector or adjacent controlled sector
    has_attack_node = False
    check_sectors = {target.sector_id}
    for adj_id in target_sector.adjacent_sector_ids:
        adj_sector = state.world.sectors.get(adj_id)
        if adj_sector is not None and adj_sector.controller_player_id == player.player_id:
            check_sectors.add(adj_id)

    for s_id in check_sectors:
        sec = state.world.sectors.get(s_id)
        if sec is None:
            continue
        for st_id in sec.structure_ids:
            s = state.structures.get(st_id)
            if (
                s is not None
                and s.owner_player_id == player.player_id
                and s.structure_type == StructureType.ATTACK_NODE
                and s.active
            ):
                has_attack_node = True
                break
        if has_attack_node:
            break

    if not has_attack_node:
        return ValidationResult(
            accepted=False, action_id=aid,
            reason="No active ATTACK_NODE in target or adjacent controlled sector",
        )

    return ValidationResult(accepted=True, action_id=aid)


def _validate_scan(state, action, player):
    aid = action.action_id
    sector_id = action.payload.get("sector_id")
    if sector_id is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Missing sector_id")

    sector = state.world.sectors.get(sector_id)
    if sector is None:
        return ValidationResult(accepted=False, action_id=aid, reason=f"Sector '{sector_id}' not found")

    controlled = sector.controller_player_id == player.player_id
    adjacent_to_controlled = any(
        state.world.sectors[adj_id].controller_player_id == player.player_id
        for adj_id in sector.adjacent_sector_ids
        if adj_id in state.world.sectors
    )
    if not controlled and not adjacent_to_controlled:
        return ValidationResult(
            accepted=False, action_id=aid,
            reason="Sector not controlled or adjacent to a controlled sector",
        )

    return ValidationResult(accepted=True, action_id=aid)


def _validate_create_subagent(state, action, player):
    aid = action.action_id
    current_count = len([
        sa for sa in state.subagents.values()
        if sa.owner_player_id == player.player_id and sa.active
    ])
    if current_count >= state.config.max_subagents_per_player:
        return ValidationResult(accepted=False, action_id=aid, reason="Max subagent cap reached")

    if player.data < SUBAGENT_DATA_COST:
        return ValidationResult(accepted=False, action_id=aid, reason="Not enough data")

    return ValidationResult(accepted=True, action_id=aid)


def _validate_deactivate_subagent(state, action, player):
    aid = action.action_id
    subagent_id = action.payload.get("subagent_id")
    if subagent_id is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Missing subagent_id")

    subagent = state.subagents.get(subagent_id)
    if subagent is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Subagent not found")
    if subagent.owner_player_id != player.player_id:
        return ValidationResult(accepted=False, action_id=aid, reason="Subagent not owned by player")
    if not subagent.active:
        return ValidationResult(accepted=False, action_id=aid, reason="Subagent already inactive")

    return ValidationResult(accepted=True, action_id=aid)


def _validate_set_policy(state, action, player):
    aid = action.action_id
    if "policy_name" not in action.payload or "value" not in action.payload:
        return ValidationResult(accepted=False, action_id=aid, reason="Missing policy_name or value")
    return ValidationResult(accepted=True, action_id=aid)


def _validate_transfer(state, action, player):
    aid = action.action_id
    payload = action.payload
    target_pid = payload.get("target_player_id")
    resource_type = payload.get("resource_type")
    amount = payload.get("amount")

    if target_pid is None or resource_type is None or amount is None:
        return ValidationResult(
            accepted=False, action_id=aid,
            reason="Missing target_player_id, resource_type, or amount",
        )

    target = state.players.get(target_pid)
    if target is None:
        return ValidationResult(accepted=False, action_id=aid, reason="Target player not found")

    if amount <= 0:
        return ValidationResult(accepted=False, action_id=aid, reason="Amount must be positive")

    resource_map = {"METAL": player.metal, "DATA": player.data, "BIOMASS": player.biomass}
    rt = resource_type if isinstance(resource_type, str) else resource_type.value
    current = resource_map.get(rt)
    if current is None:
        return ValidationResult(accepted=False, action_id=aid, reason=f"Invalid resource_type '{resource_type}'")
    if current < amount:
        return ValidationResult(accepted=False, action_id=aid, reason="Not enough resources")

    return ValidationResult(accepted=True, action_id=aid)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_action(state: GameState, action: Action) -> None:
    player = state.players[action.issuer_player_id]
    cost = get_action_energy_cost(action)
    at = action.action_type

    if at == ActionType.BUILD_STRUCTURE:
        _resolve_build(state, action, player, cost)
    elif at == ActionType.REMOVE_STRUCTURE:
        _resolve_remove(state, action, player, cost)
    elif at == ActionType.ATTACK_STRUCTURE:
        _resolve_attack(state, action, player, cost)
    elif at == ActionType.SCAN_SECTOR:
        _resolve_scan(state, action, player, cost)
    elif at == ActionType.CREATE_SUBAGENT:
        _resolve_create_subagent(state, action, player, cost)
    elif at == ActionType.DEACTIVATE_SUBAGENT:
        _resolve_deactivate_subagent(state, action, player, cost)
    elif at == ActionType.SET_POLICY:
        _resolve_set_policy(state, action, player, cost)
    elif at == ActionType.TRANSFER_RESOURCE:
        _resolve_transfer(state, action, player, cost)

    action.status = ActionStatus.RESOLVED


def _resolve_build(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost

    payload = action.payload
    sector_id = payload["sector_id"]
    raw_st = payload["structure_type"]
    structure_type = StructureType(raw_st) if isinstance(raw_st, str) else raw_st

    catalog = STRUCTURE_CATALOG[structure_type]
    player.metal -= catalog["metal_cost"]
    player.data -= catalog["data_cost"]
    player.biomass -= catalog["biomass_cost"]

    st_id = next_id(state, "st")
    structure = StructureState(
        structure_id=st_id,
        owner_player_id=player.player_id,
        sector_id=sector_id,
        structure_type=structure_type,
        hp=catalog["hp"],
        max_hp=catalog["hp"],
        active=True,
        activation_heartbeat=state.heartbeat + state.config.structure_activation_delay_heartbeats,
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
    emit_structure_built(state, player.player_id, st_id, structure_type.value, sector_id)
    recompute_sector_control(state, sector_id)


def _resolve_remove(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost

    structure_id = action.payload["structure_id"]
    structure = state.structures[structure_id]
    sector_id = structure.sector_id

    refund = math.floor(structure.metal_cost * REMOVE_REFUND_RATIO)
    player.metal += refund

    emit_structure_removed(state, player.player_id, structure_id)
    del state.structures[structure_id]
    state.world.sectors[sector_id].structure_ids.remove(structure_id)
    recompute_sector_control(state, sector_id)


def _resolve_attack(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost

    target_id = action.payload["target_structure_id"]
    target = state.structures[target_id]
    target.hp -= ATTACK_DAMAGE

    emit_structure_attacked(state, player.player_id, target_id, ATTACK_DAMAGE, target.hp)

    if target.hp <= 0:
        sector_id = target.sector_id
        emit_structure_destroyed(state, target.owner_player_id, target_id, sector_id)
        del state.structures[target_id]
        state.world.sectors[sector_id].structure_ids.remove(target_id)
        recompute_sector_control(state, sector_id)


def _resolve_scan(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost


def _resolve_create_subagent(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost
    player.data -= SUBAGENT_DATA_COST

    payload = action.payload
    sa_id = next_id(state, "sa")
    subagent = SubagentState(
        subagent_id=sa_id,
        owner_player_id=player.player_id,
        name=payload.get("name", sa_id),
        scope_sector_ids=payload.get("scope_sector_ids"),
        scope_action_types=payload.get("scope_action_types"),
        mandate=payload.get("mandate", ""),
        upkeep_cost=SUBAGENT_UPKEEP,
        active=True,
    )
    state.subagents[sa_id] = subagent
    player.subagent_ids.append(sa_id)


def _resolve_deactivate_subagent(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost
    subagent_id = action.payload["subagent_id"]
    state.subagents[subagent_id].active = False


def _resolve_set_policy(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost


def _resolve_transfer(state, action, player, cost):
    player.energy_spent_this_heartbeat += cost

    payload = action.payload
    target = state.players[payload["target_player_id"]]
    rt = payload["resource_type"]
    rt_str = rt if isinstance(rt, str) else rt.value
    amount = payload["amount"]

    if rt_str == "METAL":
        player.metal -= amount
        target.metal += amount
    elif rt_str == "DATA":
        player.data -= amount
        target.data += amount
    elif rt_str == "BIOMASS":
        player.biomass -= amount
        target.biomass += amount
