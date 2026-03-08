from __future__ import annotations

from .models import Action, GameState, SubagentState


def validate_subagent_scope(
    state: GameState, subagent: SubagentState, action: Action
) -> str | None:
    if subagent.scope_sector_ids is not None:
        sector_id = action.payload.get("sector_id")
        if sector_id is not None and sector_id not in subagent.scope_sector_ids:
            return f"Subagent '{subagent.subagent_id}' not scoped for sector '{sector_id}'"

    if subagent.scope_action_types is not None:
        if action.action_type not in subagent.scope_action_types:
            return (
                f"Subagent '{subagent.subagent_id}' not scoped for "
                f"action type '{action.action_type.value}'"
            )

    return None
