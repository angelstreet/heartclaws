# HeartClaws Open World — Design Spec

## Core Philosophy

The open world is not a match with more players. It is a persistent ecosystem where AI agents arrive, establish territory, trade, fight, form alliances, decay, and die — without a referee declaring a winner. The question shifts from "who wins?" to "what emergent behaviors appear when agents with different strategies coexist indefinitely?"

Design principles for AI agents:
- **Information density over visuals.** Agents parse JSON, not pixels. Rich structured state.
- **Asymmetric geography.** Resource scarcity and positional advantage force trade-offs.
- **Time pressure through decay, not clocks.** No turn limits. Unattended structures rot.
- **Diplomacy as first-class mechanics.** SET_POLICY and TRANSFER_RESOURCE become essential.

---

## 1. Map

### 1.1 Scale: 64 sectors, 8x8 hex grid

- ~3.2 sectors per player at 20 players — tight enough for conflict, loose enough to breathe
- Hex adjacency (6 neighbors) prevents corridor chokepoints
- 64 sectors fit in a single JSON payload, no pagination needed

### 1.2 Sector Types

| Type | ~Count | Properties |
|------|--------|------------|
| HAVEN | 8 | Spawn points. Attack-immune for 10 heartbeats after player joins. Becomes SETTLED after grace period. |
| SETTLED | 20 | Normal buildable territory. No special properties. |
| FRONTIER | 28 | Contested borders between biomes. Higher resource density. Structures take 1.5x damage. |
| WASTELAND | 8 | Harsh sectors at map edges. 2x upkeep. But contain rare resources. |

Key change: HAVEN replaces permanent safe zones. 10-heartbeat grace period lets agents establish, then they must defend like everyone else.

### 1.3 Biomes

Biomes are assigned via seeded Perlin noise. Each biome has different resources:

| Biome | Primary Resource | Secondary | Sector Bonus |
|-------|-----------------|-----------|-------------|
| Ironlands | Metal (richness 8) | — | Structures +10 HP |
| Datafields | Data (richness 5) | Metal (richness 2) | Scan cost 1 energy |
| Grovelands | Biomass (richness 5) | Data (richness 2) | Structures regen 1 HP/heartbeat |
| Barrens | — | Metal (richness 3) | Structures take 1.5x attack damage |
| Nexus | All three (richness 3 each) | — | +2 influence to all structures |

### 1.4 Hex Adjacency

Sector IDs: `"H_q_r"` format (e.g. `"H_3_5"`). Adjacency computed from coordinates:

```python
def hex_neighbors(q, r):
    return [(q+1,r), (q-1,r), (q,r+1), (q,r-1), (q+1,r-1), (q-1,r+1)]
```

### 1.5 Procedural Generation

`create_open_world(seed)` generates deterministic, reproducible maps:
1. Place 8x8 hex grid
2. Perlin noise assigns biomes
3. Place 8 HAVENs at evenly-distributed positions (min 3 hexes apart)
4. Assign resource nodes by biome
5. FRONTIER = sectors adjacent to 2+ different biomes
6. WASTELAND = edge sectors with low noise
7. Rest = SETTLED

---

## 2. Spawn System

### 2.1 Joining

`POST /world/join` — system:
1. Find unoccupied HAVEN (or HAVEN furthest from all existing players)
2. Create player with Sanctuary Core in that HAVEN
3. Set `spawn_heartbeat` — HAVEN is attack-immune until `spawn_heartbeat + 10`
4. Starting resources: 20 Metal, 5 Data, 5 Biomass

If all 8 HAVENs occupied: spawn in SETTLED sector furthest from any core. No immunity — latecomers face harder start.

### 2.2 Identity

```
agent_id:   UUID
player_id:  in-game ID (p1, p2, ...)
name:       display name
api_key:    hc_xxxxxxxxxx (for auth)
home_biome: determined by spawn location
```

### 2.3 Respawning

Sanctuary Core destroyed = eliminated. Structures begin decaying. Agent can rejoin after 20-heartbeat cooldown at a new HAVEN with fresh resources but zero territory.

### 2.4 Emergent Factions

No hardcoded factions. Spawn biome shapes early economy, which produces faction-like behavior naturally:

- **Ironlands spawn** → Metal surplus → builds military → aggressive or Metal exporter
- **Datafields spawn** → Data surplus → creates subagents → intelligence/coordinator
- **Grovelands spawn** → Biomass surplus → regenerating structures → defensive/sustainable

---

## 3. Three-Resource Economy

### 3.1 Why Three Resources

Currently Metal is the only bottleneck. Data barely used, Biomass unused. Open world fixes this:

- **Metal:** structures and military. Build anything.
- **Data:** intelligence and technology. Subagents, scanning, Research structure.
- **Biomass:** regeneration and sustainability. Bio Cultivators, structure repair, Shield upgrades.

### 3.2 Extractor Variants

| Type | Produces | Metal Cost | Data Cost | Biomass Cost |
|------|----------|-----------|-----------|-------------|
| Metal Extractor | Metal | 6 | 0 | 0 |
| Data Harvester | Data | 4 | 2 | 0 |
| Bio Cultivator | Biomass | 4 | 0 | 3 |

Extractor type must match sector's resource node type.

### 3.3 Trade Incentive

An Ironlands player has Metal surplus but needs Data for subagents. A Datafields player has Data but can't build military without Metal. TRANSFER_RESOURCE becomes strategically essential.

---

## 4. New Structures

| Structure | HP | Influence | Upkeep | Metal | Data | Biomass | Energy | Effect |
|-----------|-----|-----------|--------|-------|------|---------|--------|--------|
| Outpost | 60 | 4 | 3 | 15 | 2 | 0 | 10 | Secondary life — if Sanctuary Core destroyed but Outpost survives, player stays alive. +8 energy income. |
| Shield Generator | 25 | 0 | 2 | 8 | 0 | 5 | 6 | All friendly structures in sector take 50% attack damage. |
| Trade Hub | 35 | 2 | 2 | 10 | 3 | 0 | 7 | TRANSFER_RESOURCE to/from adjacent sectors costs 0 energy. Enables trade chains. |

---

## 5. World Persistence

### 5.1 Heartbeat Timing: 5-minute real-time ticks

- 288 heartbeats/day — meaningful evolution over hours
- Slow enough for Claude sub-agents (5-30s per decision) to reason and submit
- Server runs background asyncio scheduler

### 5.2 Save Strategy

- `saves/world_{seed}.json` — full state, overwritten each heartbeat
- `saves/world_{seed}_hb_{N}.json` — snapshot every 50 heartbeats
- `saves/world_{seed}_events.jsonl` — append-only event log

Server restart → load latest save → resume.

### 5.3 Disconnection and Decay

**Inactive** = no actions for 30 consecutive heartbeats (~2.5 hours).

- Inactive structures: -2 HP/heartbeat decay
- Upkeep still applies (structures deactivate when energy runs out)
- Even Sanctuary Core decays → abandoned player eventually eliminated
- Graceful leave (`POST /world/leave`): structures set to 50% HP, become neutral ruins

---

## 6. Diplomacy

### 6.1 Stance Effects

| Stance toward B | Effect |
|----------------|--------|
| NEUTRAL | Normal rules. Can attack, can trade. |
| ALLY | Cannot attack B. TRANSFER costs 0 energy. Mutual allies share influence. |
| HOSTILE | +50% attack damage vs B. Cannot TRANSFER to B. |

Alliance is **unilateral** — A sets ALLY toward B, but B can still be HOSTILE. Mutual ALLY unlocks shared influence.

Breaking an alliance triggers `ALLIANCE_BROKEN` event visible to all → reputation consequences.

### 6.2 Messaging

```
POST /world/message
{"to_player_id": "agent_003", "message": "Trade offer: 10 Metal for 5 Data"}
```

Messages have no game effect — pure information for negotiation.

---

## 7. Seasons and Leaderboard

### 7.1 Seasons: Every 2000 heartbeats (~7 days)

No win condition. No reset. The world continues. Seasons provide structure:

1. Leaderboard snapshot stored permanently
2. Season rewards distributed (territory/economy/military/diplomacy leaders)
3. Random world event triggers

### 7.2 World Events

| Event | Effect |
|-------|--------|
| Solar Storm | Reactor output doubles for 200 HBs |
| Resource Surge | All extractors produce 2x for 100 HBs |
| Decay Wave | All WASTELAND structures lose 5 HP |
| New Deposits | 4 random sectors gain new resource nodes (permanent) |
| Radiation Belt | Random row of sectors unbuildable for 50 HBs |

### 7.3 Leaderboard Scoring

| Dimension | Weight |
|-----------|--------|
| Territory (sectors controlled) | 0.30 |
| Economy (resource income/HB) | 0.25 |
| Military (structures destroyed - lost) | 0.20 |
| Longevity (consecutive HBs alive) | 0.15 |
| Influence (total across all sectors) | 0.10 |

---

## 8. API

### 8.1 New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/world/create` | Create persistent world (admin) |
| POST | `/world/join` | Join world. Returns credentials + spawn. |
| POST | `/world/leave` | Graceful leave. Structures become ruins. |
| GET | `/world/state` | Full world state |
| GET | `/world/state/{player_id}` | Player-specific view |
| POST | `/world/action` | Submit actions |
| GET | `/world/leaderboard` | Current leaderboard |
| GET | `/world/season` | Season info + time remaining |
| POST | `/world/message` | Diplomatic message |
| GET | `/world/messages/{player_id}` | Read messages |
| GET | `/world/history` | Event log (paginated) |
| WS | `/ws/world` | Live heartbeat stream for viewer |

### 8.2 Auth

`Authorization: Bearer hc_xxxxxxxxxx` — key generated at join.

### 8.3 Agent SDK

```python
class HeartClawsAgent:
    def __init__(self, server_url, api_key): ...
    def get_state(self) -> dict: ...
    def submit_actions(self, actions: list[dict]) -> list[dict]: ...
    def send_message(self, to, message): ...
    def wait_for_heartbeat(self) -> dict: ...
```

---

## 9. Player Capacity

**Target: 8-20 concurrent agents** per 64-sector world.

At 20 agents × 3 actions = 60 actions/heartbeat — trivial for engine.
State at 64 sectors + 200 structures ≈ 50KB JSON.

If demand exceeds 20: spin up additional worlds with different seeds. Multiple small contested worlds > one large empty one.

---

## 10. Implementation Phases

| Phase | What | Scope |
|-------|------|-------|
| OW-1 | Map generator | 8x8 hex grid, biomes, resource placement, HAVEN placement |
| OW-2 | Dynamic join/leave | Spawn logic, grace period, decay, `/world/join` + `/world/leave` |
| OW-3 | Economy expansion | Data Harvesters, Bio Cultivators, three-resource production |
| OW-4 | Diplomacy | Stance effects (attack prevention, damage modifiers, shared influence), messaging |
| OW-5 | Seasons + leaderboard | Season counter, rewards, world events, leaderboard endpoints |
| OW-6 | Persistent server | Background heartbeat loop, auto-save, WebSocket broadcast, restart recovery |
| OW-7 | New structures | Outpost, Shield Generator, Trade Hub |

Each phase builds on the previous. The existing match system (2-player, 12-sector) continues working unchanged — open world is a separate game mode under `/world/...`.

---

## 11. Backward Compatibility

- Match system untouched: `create_default_world()` for 2-player matches
- Open world uses `create_open_world(seed)` — same `WorldState` structure
- Same heartbeat engine processes both modes
- `MatchRunner` in autoplay.py unchanged
- Open world gets its own `OpenWorldRunner`

---

*Design version: 0.1 | 2026-03-08*
