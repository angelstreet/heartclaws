from __future__ import annotations

from .config import ATTACK_DAMAGE
from .control import recompute_sector_control
from .enums import DiplomaticStance, StructureType
from .events import emit_structure_attacked, emit_structure_destroyed
from .models import Action, GameState


def _has_active_shield_in_sector(state: GameState, owner_player_id: str, sector_id: str) -> bool:
    """Check if the owner has an active SHIELD_GENERATOR in the given sector."""
    sector = state.world.sectors.get(sector_id)
    if sector is None:
        return False
    for st_id in sector.structure_ids:
        s = state.structures.get(st_id)
        if (
            s is not None
            and s.owner_player_id == owner_player_id
            and s.structure_type == StructureType.SHIELD_GENERATOR
            and s.active
        ):
            return True
    return False


def _has_active_mech_bay_in_sector(state: GameState, owner_player_id: str, sector_id: str) -> bool:
    """Check if the owner has an active MECH_BAY in the given sector."""
    sector = state.world.sectors.get(sector_id)
    if sector is None:
        return False
    for st_id in sector.structure_ids:
        s = state.structures.get(st_id)
        if (
            s is not None
            and s.owner_player_id == owner_player_id
            and s.structure_type == StructureType.MECH_BAY
            and s.active
        ):
            return True
    return False


def _player_has_active_outpost(state: GameState, player_id: str) -> bool:
    """Check if a player has any active OUTPOST structure anywhere."""
    for s in state.structures.values():
        if (
            s.owner_player_id == player_id
            and s.structure_type == StructureType.OUTPOST
            and s.active
        ):
            return True
    return False


def _handle_core_destroyed(state: GameState, owner_player_id: str | None) -> None:
    """Handle sanctuary core destruction — eliminate player unless they have an outpost."""
    if owner_player_id is None:
        return
    player = state.players.get(owner_player_id)
    if player is None:
        return

    if _player_has_active_outpost(state, owner_player_id):
        # Outpost acts as secondary life — player survives
        # Clear the sanctuary_core_structure_id since it's destroyed
        player.sanctuary_core_structure_id = None
    else:
        # No outpost — player is eliminated
        player.alive = False
        player.sanctuary_core_structure_id = None


def resolve_attack_structure(state: GameState, action: Action) -> None:
    target_id = action.payload["target_structure_id"]
    target = state.structures[target_id]

    # Diplomacy: HOSTILE stance gives +50% damage
    damage = ATTACK_DAMAGE
    attacker = state.players.get(action.issuer_player_id)
    if attacker is not None and target.owner_player_id is not None:
        stance = attacker.diplomacy_stance.get(target.owner_player_id, DiplomaticStance.NEUTRAL)
        if stance == DiplomaticStance.HOSTILE:
            damage = ATTACK_DAMAGE + ATTACK_DAMAGE // 2  # +50%

    # Shield Generator: 50% damage reduction if owner has active shield in same sector
    if target.owner_player_id is not None and _has_active_shield_in_sector(
        state, target.owner_player_id, target.sector_id
    ):
        damage = damage // 2

    # Mech Bay: 30% damage reduction if owner has active MECH_BAY in same sector
    if target.owner_player_id is not None and _has_active_mech_bay_in_sector(
        state, target.owner_player_id, target.sector_id
    ):
        damage = int(damage * 0.7)

    target.hp -= damage

    emit_structure_attacked(
        state,
        attacker_id=action.issuer_player_id,
        target_structure_id=target_id,
        damage=damage,
        remaining_hp=target.hp,
    )

    if target.hp <= 0:
        sector_id = target.sector_id
        owner_player_id = target.owner_player_id
        is_sanctuary_core = target.structure_type == StructureType.SANCTUARY_CORE

        del state.structures[target_id]
        state.world.sectors[sector_id].structure_ids.remove(target_id)

        emit_structure_destroyed(
            state,
            player_id=action.issuer_player_id,
            structure_id=target_id,
            sector_id=sector_id,
        )

        recompute_sector_control(state, sector_id)

        # Check for sanctuary core destruction → elimination or outpost secondary life
        if is_sanctuary_core:
            _handle_core_destroyed(state, owner_player_id)
