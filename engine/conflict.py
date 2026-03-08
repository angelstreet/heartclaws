from __future__ import annotations

from .config import ATTACK_DAMAGE
from .control import recompute_sector_control
from .events import emit_structure_attacked, emit_structure_destroyed
from .models import Action, GameState


def resolve_attack_structure(state: GameState, action: Action) -> None:
    target_id = action.payload["target_structure_id"]
    target = state.structures[target_id]

    target.hp -= ATTACK_DAMAGE

    emit_structure_attacked(
        state,
        attacker_id=action.issuer_player_id,
        target_structure_id=target_id,
        damage=ATTACK_DAMAGE,
        remaining_hp=target.hp,
    )

    if target.hp <= 0:
        sector_id = target.sector_id
        del state.structures[target_id]
        state.world.sectors[sector_id].structure_ids.remove(target_id)

        emit_structure_destroyed(
            state,
            player_id=action.issuer_player_id,
            structure_id=target_id,
            sector_id=sector_id,
        )

        recompute_sector_control(state, sector_id)
