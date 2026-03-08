# Architecture

## Module Dependency Graph

```
enums.py → config.py → models.py
                           ↓
              ┌────────────┼────────────┐
              ↓            ↓            ↓
          world.py     energy.py    agents.py
          control.py       ↓            ↓
              ↓        ┌───┴────────────┘
              ↓        ↓
          actions.py ←─┘
              ↓
         conflict.py   events.py
              ↓            ↓
         heartbeat.py ←────┘
              ↓
        persistence.py
              ↓
         engine.py (public facade)
```

## State Mutation Model

- All state changes happen inside `heartbeat.run_heartbeat()` or `actions.resolve_action()`
- No module mutates state outside the heartbeat resolution loop
- `submit_action()` only appends to `actions_pending`
- Energy charging happens atomically with action resolution

## Determinism Guarantees

- All dict iterations sorted by key
- Action ordering: priority desc, submitted_heartbeat asc, player_id asc, action_id asc
- Upkeep deactivation: subagents first (upkeep desc, id asc), then structures (upkeep desc, id asc)
- ID generation: sequential counter in `GameState.id_counter`
- No randomness except through seeded RNG (unused in v0.1)

## Adding New Structure Types

1. Add to `StructureType` enum in `enums.py`
2. Add stats in `STRUCTURE_CATALOG` in `config.py`
3. Add energy cost in `BUILD_ENERGY_COSTS` in `config.py`
4. Done — build/remove/attack handle any type generically

## Adding New Action Types

1. Add to `ActionType` enum in `enums.py`
2. Add energy cost in `ACTION_ENERGY_COSTS` in `config.py`
3. Add validation case in `validate_action()` in `actions.py`
4. Add resolution case in `resolve_action()` in `actions.py`
5. Add event emitter in `events.py` if needed
