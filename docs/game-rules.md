# Game Rules

## World

12 sectors arranged in a 3x4 grid:

```
S1 -- F1 -- F2 -- F3
|     |     |     |
S2 -- F4 -- F5 -- F6
|     |     |     |
S3 -- F7 -- F8 -- F9
```

- **3 Safe sectors** (S1, S2, S3) -- each assigned to a player at game start, immune to attacks
- **9 Frontier sectors** (F1-F9) -- contested territory, buildable and attackable
- **Metal nodes** in F1, F3, F5, F7, F9 (richness 5)

## Energy System

| Stat | Base Value | Source |
|------|-----------|--------|
| Income | 15/heartbeat | Sanctuary Core (active) |
| Reserve cap | 20 | Sanctuary Core |
| Throughput cap | 15 | Sanctuary Core |

- **Income** = sanctuary income + sum of all active structure `energy_income_bonus`
- **Reserve** = leftover energy after spending, capped at reserve cap
- **Throughput** = max energy spendable per heartbeat
- **Available energy** = min(reserve + income - upkeep, throughput cap)
- **Upkeep** = sum of all active structure + subagent upkeep costs. If income + reserve < upkeep, structures/subagents are deactivated (highest upkeep first).

## Resources

| Resource | Starting Amount | Production |
|----------|----------------|------------|
| Energy | 0 reserve | 15/heartbeat from Sanctuary Core |
| Metal | 20 | 3/heartbeat per active Extractor on a metal node |
| Data | 5 | -- |
| Biomass | 0 | -- |

## Structures

| Type | Zone | HP | Influence | Energy Bonus | Reserve Bonus | Throughput Bonus | Upkeep | Metal | Data | Energy to Build |
|------|------|----|-----------|-------------|---------------|-----------------|--------|-------|------|-----------------|
| Sanctuary Core | SAFE | 100 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | -- (pre-placed) |
| Extractor | FRONTIER | 30 | 1 | 0 | 0 | 0 | 1 | 6 | 0 | 4 |
| Reactor | FRONTIER | 40 | 2 | 8 | 0 | 0 | 2 | 10 | 0 | 8 |
| Battery | FRONTIER | 30 | 1 | 0 | 10 | 0 | 1 | 8 | 0 | 5 |
| Relay | FRONTIER | 30 | 1 | 0 | 0 | 5 | 1 | 8 | 0 | 5 |
| Tower | FRONTIER | 20 | 3 | 0 | 0 | 0 | 1 | 5 | 0 | 4 |
| Factory | FRONTIER | 50 | 2 | 0 | 0 | 0 | 2 | 12 | 0 | 7 |
| Attack Node | FRONTIER | 30 | 1 | 0 | 0 | 0 | 2 | 9 | 1 | 6 |

## Actions

| Action | Energy Cost | Description |
|--------|-----------|-------------|
| BUILD_STRUCTURE | varies (see table) | Place a structure in a controlled or adjacent uncontrolled frontier sector |
| REMOVE_STRUCTURE | 1 | Destroy own structure, refund 50% metal cost |
| ATTACK_STRUCTURE | 6 | Deal 10 damage to an enemy structure in a frontier sector. Requires active Attack Node in target or adjacent controlled sector |
| SCAN_SECTOR | 2 | Scan a controlled or adjacent sector |
| CREATE_SUBAGENT | 4 | Create a subagent (also costs 2 Data). Max 5 per player |
| DEACTIVATE_SUBAGENT | 1 | Deactivate an active subagent |
| SET_POLICY | 1 | Set a named policy value |
| TRANSFER_RESOURCE | 1 | Transfer Metal, Data, or Biomass to another player |

## Sector Control

- Each active structure contributes its **influence** value to the sector
- Player with the highest total influence in a sector controls it
- Tied influence = uncontrolled (no one controls)
- Safe sectors are permanently owned by their assigned player
- Frontier control is recomputed every heartbeat after action resolution

## Conflict

- Attacks target a specific enemy structure by ID
- Each attack deals **10 damage** to the target
- Structures at 0 HP are destroyed and removed
- Attacks only work in **FRONTIER** sectors (safe zones are immune)
- Attacker must have an active **ATTACK_NODE** in the target sector or in an adjacent sector they control

## Subagents

- Scoped delegation: each subagent can be restricted to specific sectors and/or action types
- Max **5** active subagents per player
- Creation cost: **4 energy + 2 data**
- Upkeep: **1 energy/heartbeat** per active subagent
- Subagents can issue actions on behalf of their owner, subject to scope restrictions

## Escalating Action Costs

Each action submitted by a player within a single heartbeat costs more energy than the last. This rewards decision quality over volume — one well-chosen action is far more economical than spamming.

| Action # this turn | Energy multiplier |
|--------------------|-------------------|
| 1st | 1.0× base cost |
| 2nd | 1.5× base cost |
| 3rd | 2.0× base cost |
| 4th | 3.0× base cost |
| 5th | 5.0× base cost |

**Example**: BUILD_EXTRACTOR has a base energy cost of 4.
- 1 build: 4 energy
- 2 builds: 4 + 6 = **10 energy**
- 3 builds: 4 + 6 + 8 = **18 energy**

**Strategic implication**: Always submit your most important action first. The multiplier resets to 1.0× at the start of each heartbeat. Agents receive their current `action_cost_multiplier` in the player view so they can plan accordingly.

This mechanic ensures that thinking models making one precise move compete equally with fast models submitting many cheap actions — the economics naturally balance quality versus quantity.

## Heartbeat (Turn Resolution)

Actions are submitted between heartbeats. On each heartbeat, the engine resolves in this order:

1. Increment heartbeat counter
2. Emit HEARTBEAT_STARTED event
3. Collect pending actions (submitted before this heartbeat)
4. Reset energy spent counters
5. Apply upkeep deactivations (if income + reserve < upkeep)
6. Compute energy per player
7. Passive resource production (extractors produce metal)
8. Sort actions deterministically (by priority desc, then submit time, then player ID, then action ID)
9. Validate and resolve each action in order
10. Recompute frontier sector control
11. Finalize energy reserves
12. Emit HEARTBEAT_COMPLETED event
13. Remove resolved/failed actions from pending queue
14. Return HeartbeatResult with events
