# HeartClaws v0.1

Headless Agent Strategy Engine — a deterministic simulation engine for a single-planet strategy game where AI agents compete through economic growth, territorial control, and disruption.

## Quick Start

```bash
# CLI interactive game (you vs simple AI)
python3 play.py

# API server (requires FastAPI + uvicorn)
uvicorn server:app --port 5013

# Run tests
python3 -m pytest tests/ -v
```

## Project Structure

```
heartclaws/
  play.py                    # CLI interactive game runner (player vs AI)
  server.py                  # FastAPI HTTP server (optional)
  engine/
    __init__.py
    enums.py                 # All enums: SectorType, ResourceType, StructureType, ActionType, etc.
    config.py                # GameConfig defaults, structure catalog, cost tables
    models.py                # Dataclass models: GameState, PlayerState, Action, Event, etc.
    engine.py                # Public API: init_game, submit_action, run_heartbeat, save/load
    heartbeat.py             # Turn resolution pipeline (14-step heartbeat sequence)
    actions.py               # Action validation and resolution logic for all 8 action types
    energy.py                # Energy income, upkeep, reserve cap, throughput computation
    control.py               # Influence-based sector control computation
    conflict.py              # Attack resolution and structure destruction
    agents.py                # Subagent scope validation
    events.py                # Event emitters for the event log
    world.py                 # Default world generation (12-sector grid, resource nodes)
    persistence.py           # JSON save/load serialization
  tests/
    conftest.py              # Shared fixtures
    test_acceptance.py       # End-to-end acceptance tests
    test_actions.py          # Action validation and resolution tests
    test_conflict.py         # Attack and destruction tests
    test_control.py          # Sector control tests
    test_determinism.py      # Determinism guarantees
    test_energy.py           # Energy system tests
    test_persistence.py      # Save/load round-trip tests
```

## Tech Stack

- Python 3.11+
- pytest (testing)
- FastAPI + uvicorn (optional HTTP API)
- No external runtime dependencies for the engine itself
