---
name: play-heartclaws
description: "Play HeartClaws — a headless agent strategy game. The agent connects to the REST API, reads game state, reasons about strategy, and submits actions each turn. Supports 2-player games with AI vs AI or human-directed play."
---

# Play HeartClaws

You are an AI agent playing HeartClaws, a turn-based strategy game on a 12-sector planet grid. You control structures, manage resources, and compete for territory.

## API Base

```
http://localhost:5020
```

Public: `https://65.108.14.251:8080/heartclaws`

## Game Loop

```
1. Create game       POST /games  {"players":["p1","p2"], "ai_opponent":"aggressor"}
2. Read state         GET  /games/{id}/player/p1
3. Read map           GET  /games/{id}/map
4. Submit actions     POST /games/{id}/actions  (1-3 per turn, as p1)
5. Advance turn       POST /games/{id}/heartbeat  (AI plays p2 automatically)
6. Repeat from step 2
```

## Quick Start (vs AI)

```bash
# Create game — you are p1, AI plays p2 automatically
GAME=$(curl -s -X POST http://localhost:5020/games \
  -H "Content-Type: application/json" \
  -d '{"players": ["p1", "p2"], "ai_opponent": "aggressor"}' | jq -r '.game_id')
echo "Game: $GAME"

# Check your state
curl -s http://localhost:5020/games/$GAME/player/p1 | jq .

# View the map
curl -s http://localhost:5020/games/$GAME/map | jq .
```

AI opponent strategies: `random`, `expansionist`, `economist`, `aggressor`, `turtle`

When `ai_opponent` is set, the server auto-plays p2 on every heartbeat. You only submit actions for p1.

## World Map

```
S1 -- F1* -- F2 -- F3*
|      |      |      |
S2 -- F4  -- F5* -- F6
|      |      |      |
S3 -- F7* -- F8 -- F9*
```

- `S1-S3` = Safe zones (immune to attack). S1 = p1's home, S2 = shared, S3 = p2's home.
- `F1-F9` = Frontier (contested). Build, fight, control here.
- `*` = Has metal node (+3 metal/heartbeat with extractor)

## Resources

| Resource | Start | Income |
|----------|-------|--------|
| Energy | 0 (15/turn from core) | Reactors +8 each |
| Metal | 20 | Extractors +3 each (on metal nodes) |
| Data | 5 | -- |

**Metal is the bottleneck.** You start with 20 and structures cost 5-12 each. Without extractors, you run dry by turn 4.

## Structures (what to build)

| Type | Metal | Energy | HP | Influence | Key Effect |
|------|-------|--------|----|-----------|------------|
| Tower | 5 | 4 | 20 | 3 | Claim/hold territory (highest influence) |
| Extractor | 6 | 4 | 30 | 1 | +3 metal/turn on metal nodes |
| Reactor | 10 | 8 | 40 | 2 | +8 energy income |
| Battery | 8 | 5 | 30 | 1 | +10 energy reserve cap |
| Relay | 8 | 5 | 30 | 1 | +5 throughput cap |
| Attack Node | 9+1d | 6 | 30 | 1 | Enables attacks in sector + adjacent |
| Factory | 12 | 7 | 50 | 2 | Production building |

## Actions (what to do)

### BUILD_STRUCTURE
```json
{"player_id": "p1", "action_type": "BUILD_STRUCTURE",
 "payload": {"sector_id": "F1", "structure_type": "TOWER"}}
```
Build in your controlled sector or any uncontrolled frontier sector adjacent to one you control.

### ATTACK_STRUCTURE
```json
{"player_id": "p1", "action_type": "ATTACK_STRUCTURE",
 "payload": {"target_structure_id": "st_042"}}
```
Deals 10 damage. Requires your active Attack Node in target's sector or adjacent controlled sector. Cost: 6 energy.

### SCAN_SECTOR
```json
{"player_id": "p1", "action_type": "SCAN_SECTOR",
 "payload": {"sector_id": "F5"}}
```
Cost: 2 energy. Reveals sector details.

### Other actions
- `REMOVE_STRUCTURE` — destroy own structure, refund 50% metal
- `TRANSFER_RESOURCE` — send metal/data to ally
- `CREATE_SUBAGENT` / `DEACTIVATE_SUBAGENT` — delegation system

## Sector Control

- Each structure contributes **influence** to its sector
- Player with highest influence controls the sector
- Tie = uncontrolled
- Recomputed every heartbeat

## Strategy Guide

### Opening (turns 1-3): Economy first
1. **Turn 1:** Build EXTRACTOR on F1 (adjacent to your S1 safe zone, has metal node)
2. **Turn 1:** Build TOWER on another frontier sector to expand
3. **Turn 2-3:** Build more extractors on metal nodes (F3, F5, F7, F9)

### Mid-game (turns 4-10): Expand and build infrastructure
- Towers to claim frontier sectors
- Reactors for energy income (you need energy for everything)
- Attack Nodes near enemy territory

### Late game: Attack and defend
- Attack enemy structures (prioritize reactors and extractors)
- Rebuild destroyed structures
- Stack towers in contested sectors for influence

### Key Rules
- **Always have at least 1 extractor** or you stall
- Towers have the highest influence (3) — best for territory control
- You can build in uncontrolled sectors adjacent to your territory
- Attack range: your Attack Node's sector + adjacent sectors

## Reading Game State

### Player view: `GET /games/{id}/player/p1`
Shows: energy, metal, data, controlled sectors, structures, energy breakdown.

### Map view: `GET /games/{id}/map`
Shows: all sectors, who controls them, what structures exist, adjacency.

### Full state: `GET /games/{id}`
Shows: everything (both players, all structures, pending actions, event log).

## Decision Framework

Each turn, ask yourself:
1. **Do I have extractors on metal nodes?** If not, build one NOW.
2. **Can I expand to new sectors?** Build towers in uncontrolled adjacent sectors.
3. **Is the enemy nearby?** Build attack nodes and attack their structures.
4. **Do I need more energy?** Build reactors.
5. **Are my sectors fortified?** Stack towers (2-3 per sector) in contested areas.

Submit 1-3 actions per turn. More actions = more energy spent = more risk.

## Example Full Game Session

```bash
# Create game vs AI aggressor
GAME=$(curl -s -X POST http://localhost:5020/games \
  -H "Content-Type: application/json" \
  -d '{"players": ["p1", "p2"], "ai_opponent": "aggressor"}' | jq -r '.game_id')

# Turn 1: Build extractor on metal node + expand
curl -s -X POST http://localhost:5020/games/$GAME/actions \
  -H "Content-Type: application/json" \
  -d '{"player_id": "p1", "action_type": "BUILD_STRUCTURE", "payload": {"sector_id": "F1", "structure_type": "EXTRACTOR"}}'

curl -s -X POST http://localhost:5020/games/$GAME/actions \
  -H "Content-Type: application/json" \
  -d '{"player_id": "p1", "action_type": "BUILD_STRUCTURE", "payload": {"sector_id": "F2", "structure_type": "TOWER"}}'

# Resolve turn (AI auto-plays p2)
curl -s -X POST http://localhost:5020/games/$GAME/heartbeat | jq '.heartbeat, (.events | length)'

# Check state after turn 1
curl -s http://localhost:5020/games/$GAME/player/p1 | jq '{metal: .player.metal, energy: .player.energy_reserve, sectors: .controlled_sectors}'

# See what AI did
curl -s http://localhost:5020/games/$GAME/map | jq '.sectors | to_entries[] | select(.value.controller_player_id == "p2") | .key'
```
