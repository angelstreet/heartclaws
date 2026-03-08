# Architecture

## Module Dependency Graph

```
enums.py
  └─> config.py
        └─> models.py
              ├─> world.py          (default world generation)
              ├─> energy.py         (income, upkeep, reserve, throughput)
              ├─> actions.py        (validation + resolution for all 8 action types)
              ├─> control.py        (influence-based sector control)
              ├─> conflict.py       (attack resolution, structure destruction)
              ├─> agents.py         (subagent scope validation)
              ├─> events.py         (event emitters)
              ├─> heartbeat.py      (14-step turn resolution pipeline)
              └─> persistence.py    (JSON save/load)
                    └─> engine.py   (public API facade)
```

`server.py` imports only from `engine.py`, `config.py`, `enums.py`, and `models.py`.

## Module Responsibilities

| Module | Role |
|--------|------|
| `enums.py` | All enums: SectorType, ResourceType, StructureType, ActionType, ActionStatus, DiplomaticStance |
| `config.py` | GameConfig dataclass (defaults), STRUCTURE_CATALOG, BUILD_ENERGY_COSTS, ACTION_ENERGY_COSTS, constants |
| `models.py` | All dataclass models: GameState, PlayerState, WorldState, SectorState, StructureState, Action, Event, etc. |
| `world.py` | `create_default_world()` — generates the 12-sector grid, places sanctuary cores, assigns safe sectors |
| `energy.py` | Income/upkeep/reserve/throughput computation, upkeep deactivation logic |
| `actions.py` | `validate_action()` and `resolve_action()` for all 8 action types |
| `control.py` | `recompute_all_frontier_control()` — sums influence per player per sector, highest unique wins |
| `conflict.py` | `resolve_attack_structure()` — applies damage, destroys at 0 HP, enforces ATTACK_NODE adjacency |
| `agents.py` | Subagent scope checks (sector + action type restrictions) |
| `events.py` | Event factory functions (heartbeat started/completed, action resolved/failed, energy computed) |
| `heartbeat.py` | `run_heartbeat()` — the 14-step turn resolution pipeline |
| `persistence.py` | `save_game()` / `load_game()` — JSON serialization round-trip |
| `engine.py` | Public API surface: `init_game`, `submit_action`, `run_heartbeat`, `get_player_view`, `save_game`, `load_game` |

## State Mutation Rules

- **GameState is mutable.** All mutations happen in-place on the shared `GameState` dataclass.
- State is only mutated in two places:
  1. **Action resolution** (`actions.py`, `conflict.py`) — during step 9 of the heartbeat pipeline.
  2. **Heartbeat resolution** (`heartbeat.py`) — energy resets, upkeep deactivations, resource production, control recomputation, reserve finalization.
- `submit_action()` in `engine.py` only appends to `actions_pending` — no game logic runs until the next heartbeat.

## Determinism Guarantees

- All player iteration uses `sorted(state.players)` (sorted by player ID string).
- Actions are sorted deterministically: `(-priority, submitted_heartbeat, issuer_player_id, action_id)`.
- Upkeep deactivation order: highest upkeep first, then by ID ascending.
- ID generation: sequential counter via `GameState.id_counter`.
- No unordered set operations in game logic paths.
- Seeded RNG stored in `GameState.seed` (unused in v0.1 beyond game ID).

## Extension Points

### Adding a New Structure Type

1. Add entry to `StructureType` enum in `enums.py`.
2. Add catalog entry to `STRUCTURE_CATALOG` in `config.py` (hp, influence, bonuses, upkeep, material costs).
3. Add energy cost to `BUILD_ENERGY_COSTS` in `config.py`.
4. If the structure has special behavior (like Extractor's metal production), add logic to `heartbeat.py` step 7.

### Adding a New Action Type

1. Add entry to `ActionType` enum in `enums.py`.
2. Add energy cost to `ACTION_ENERGY_COSTS` in `config.py`.
3. Add validation case in `validate_action()` in `actions.py`.
4. Add resolution case in `resolve_action()` in `actions.py`.
5. If the action needs special heartbeat handling (like ATTACK_STRUCTURE), add a branch in `heartbeat.py` step 9.
