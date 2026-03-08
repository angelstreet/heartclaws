# API Reference

FastAPI server exposing the HeartClaws engine over HTTP. Default port: **5013**.

```bash
uvicorn server:app --port 5013
```

All game state is held in memory. Use save/load endpoints for persistence.

## Endpoints

### POST /games

Create a new game.

**Request:**
```json
{
  "players": ["p1", "p2"],
  "seed": 42
}
```

`seed` is optional (random if omitted).

**Response:**
```json
{
  "game_id": "game_...",
  "heartbeat": 0,
  "players": ["p1", "p2"],
  "sector_count": 12
}
```

---

### GET /games/{game_id}

Full game state (world, players, structures, pending actions, event log).

---

### GET /games/{game_id}/player/{player_id}

Player-scoped view: player stats, controlled sectors, owned structures, energy breakdown.

**Response:**
```json
{
  "player": {
    "player_id": "p1",
    "name": "p1",
    "alive": true,
    "sanctuary_sector_id": "S1",
    "sanctuary_core_structure_id": "st_001",
    "energy_reserve": 0,
    "metal": 15,
    "data": 5,
    "biomass": 0
  },
  "controlled_sectors": ["S1", "F1"],
  "structures": { "...": "..." },
  "energy": {
    "reserve": 0,
    "spent_this_heartbeat": 0
  }
}
```

---

### POST /games/{game_id}/actions

Submit an action to the pending queue.

**Request:**
```json
{
  "player_id": "p1",
  "action_type": "BUILD_STRUCTURE",
  "payload": {
    "sector_id": "F1",
    "structure_type": "TOWER"
  }
}
```

**Response:**
```json
{
  "accepted": true,
  "action_id": "act_a1b2c3d4",
  "reason": null,
  "energy_cost": 4
}
```

If validation fails, `accepted` is `false` and `reason` explains why.

---

### POST /games/{game_id}/heartbeat

Run one heartbeat (resolve all pending actions).

**Response:**
```json
{
  "heartbeat": 1,
  "events": [
    {
      "event_id": "evt_001",
      "heartbeat": 1,
      "event_type": "HEARTBEAT_STARTED",
      "actor_player_id": null,
      "details": {"heartbeat": 1}
    }
  ]
}
```

---

### GET /games/{game_id}/map

Map overview: all sectors with adjacency, controller, and structures.

---

### POST /games/{game_id}/save

Save game state to `saves/{game_id}.json`.

### POST /games/load

Load a saved game. Body: `{"path": "saves/game_42.json"}`.

---

## Action Payload Reference

| Action Type | Required Payload Fields |
|-------------|------------------------|
| BUILD_STRUCTURE | `sector_id`, `structure_type` |
| REMOVE_STRUCTURE | `structure_id` |
| ATTACK_STRUCTURE | `target_structure_id` |
| SCAN_SECTOR | `sector_id` |
| CREATE_SUBAGENT | `name`, `scope_sector_ids`, `scope_action_types`, `mandate` (all optional) |
| DEACTIVATE_SUBAGENT | `subagent_id` |
| SET_POLICY | `policy_name`, `value` |
| TRANSFER_RESOURCE | `target_player_id`, `resource_type` (METAL/DATA/BIOMASS), `amount` |

## Example Session (2 turns)

```bash
# Turn 0: Create game
GAME=$(curl -s -X POST http://localhost:5013/games \
  -H "Content-Type: application/json" \
  -d '{"players": ["p1", "p2"], "seed": 42}' | jq -r '.game_id')

# Turn 0: p1 builds an extractor on F1
curl -s -X POST http://localhost:5013/games/$GAME/actions \
  -H "Content-Type: application/json" \
  -d '{"player_id": "p1", "action_type": "BUILD_STRUCTURE", "payload": {"sector_id": "F1", "structure_type": "EXTRACTOR"}}'

# Turn 0: p2 builds a tower on F9
curl -s -X POST http://localhost:5013/games/$GAME/actions \
  -H "Content-Type: application/json" \
  -d '{"player_id": "p2", "action_type": "BUILD_STRUCTURE", "payload": {"sector_id": "F9", "structure_type": "TOWER"}}'

# Resolve turn 0 -> heartbeat 1
curl -s -X POST http://localhost:5013/games/$GAME/heartbeat | jq .

# Turn 1: p1 builds a reactor on F4
curl -s -X POST http://localhost:5013/games/$GAME/actions \
  -H "Content-Type: application/json" \
  -d '{"player_id": "p1", "action_type": "BUILD_STRUCTURE", "payload": {"sector_id": "F4", "structure_type": "REACTOR"}}'

# Turn 1: p2 scans F5
curl -s -X POST http://localhost:5013/games/$GAME/actions \
  -H "Content-Type: application/json" \
  -d '{"player_id": "p2", "action_type": "SCAN_SECTOR", "payload": {"sector_id": "F5"}}'

# Resolve turn 1 -> heartbeat 2
curl -s -X POST http://localhost:5013/games/$GAME/heartbeat | jq .

# Check p1's state after 2 turns
curl -s http://localhost:5013/games/$GAME/player/p1 | jq .

# View map
curl -s http://localhost:5013/games/$GAME/map | jq .
```
