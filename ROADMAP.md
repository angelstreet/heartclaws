# HeartClaws Roadmap

## Progression System

Each phase builds on the previous. Do not skip phases.

---

## Phase 0 — Engine Core (DONE)
- [x] Deterministic headless simulation engine
- [x] 12-sector map (3 safe + 9 frontier)
- [x] Energy system (income, reserve, throughput, upkeep)
- [x] 8 action types (build, attack, scan, subagent, transfer, etc.)
- [x] Influence-based sector control
- [x] Structure-centric conflict
- [x] Subagent system with scope validation
- [x] JSON save/load persistence
- [x] 34 tests passing (acceptance, determinism, round-trip)
- [x] CLI interactive game (`play.py`)
- [x] FastAPI server (`server.py` on port 5013)

---

## Phase 1 — Log Viewer (Web)
**Goal:** A simple web page to watch agents play in real-time through event logs.

### Tasks
- [ ] Auto-play mode: engine runs N heartbeats with 2+ AI agents making moves
- [ ] WebSocket endpoint streaming events as they happen
- [ ] Simple HTML/JS page (no framework) showing:
  - Live event feed (scrolling log with timestamps)
  - Color-coded by player (P1 green, P2 red)
  - Event type icons/badges (build, attack, destroy, etc.)
  - Current heartbeat counter
- [ ] Pause/resume/step controls
- [ ] Game state summary panel (resources, sector count per player)
- [ ] Serve from FastAPI static files on port 5013

### Tech
- Vanilla HTML + CSS + JS (no build step)
- WebSocket for live streaming
- SSE as fallback

---

## Phase 2 — AI Agent Strategies
**Goal:** Multiple AI personality types that make interesting games to watch.

### Tasks
- [ ] Agent interface: `decide(state, player_id) -> list[Action]`
- [ ] Strategy: **Expansionist** — build towers aggressively, claim territory fast
- [ ] Strategy: **Economist** — prioritize reactors/batteries, maximize energy
- [ ] Strategy: **Aggressor** — rush attack nodes, target enemy structures
- [ ] Strategy: **Turtler** — build defensively, high influence in few sectors
- [ ] Strategy: **Random** — random valid moves (baseline/chaos)
- [ ] Match runner: pit 2 agents against each other for N heartbeats
- [ ] Win condition evaluation (most sectors, most energy, or enemy core destroyed)
- [ ] Stats tracking: win rates per strategy matchup

---

## Phase 3 — 2D Map Visualization
**Goal:** See the game world as a visual 2D grid in the browser.

### Tasks
- [ ] Canvas or SVG-based sector grid (3x4 layout)
- [ ] Sector coloring by controller (green/red/gray)
- [ ] Structure icons inside sectors (small symbols per type)
- [ ] Resource node markers (metal deposits)
- [ ] Adjacency lines between sectors
- [ ] Click sector to see details (structures, influence breakdown)
- [ ] Animation on events (flash on attack, pulse on build)
- [ ] Turn-by-turn playback with timeline slider

### Tech
- HTML Canvas or SVG (no heavy game framework)
- Served from same FastAPI app
- Reads state via REST API or WebSocket

---

## Phase 4 — Enhanced 2D with Animations
**Goal:** Make the visualization engaging and watchable.

### Tasks
- [ ] Smooth sector transitions (color fades on control change)
- [ ] Attack animations (projectile from attack node to target)
- [ ] Build animations (structure appears with construction effect)
- [ ] Destruction effects (structure crumbles/fades)
- [ ] Energy flow visualization (lines showing income sources)
- [ ] Structure HP bars
- [ ] Player dashboard overlay (resources, income, territory graph over time)
- [ ] Speed controls (1x, 2x, 5x, 10x)
- [ ] Match replay from saved game files

---

## Phase 5 — Tournament System
**Goal:** Run and display AI tournaments.

### Tasks
- [ ] Tournament bracket: round-robin or elimination
- [ ] Auto-run matches in background
- [ ] Leaderboard page (wins, losses, avg sectors, avg heartbeats)
- [ ] Match history with replay links
- [ ] ELO or rating system for strategies
- [ ] Custom agent upload (Python file with `decide()` function)

---

## Phase 6 — Engine v0.2 Features
**Goal:** Richer gameplay for more interesting AI behavior.

### Tasks
- [ ] Fog of war (agents only see controlled + adjacent sectors)
- [ ] Moving units (scouts, convoys)
- [ ] Variable attack ranges
- [ ] Research tree (unlock better structures)
- [ ] Victory conditions (domination, economic, elimination)
- [ ] Map generation (random maps with seed)
- [ ] 3-4 player support
- [ ] Alliance mechanics
- [ ] Frontier hazards (random events)

---

## Phase 7 — 3D / Isometric View (Future)
**Goal:** Optional upgrade to isometric or simple 3D visualization.

### Tasks
- [ ] Isometric tile renderer (PixiJS or Three.js)
- [ ] Structure 3D models or isometric sprites
- [ ] Camera controls (pan, zoom)
- [ ] Terrain variation per sector
- [ ] Day/night cycle tied to heartbeats

---

## Priority Order

```
Phase 0 (DONE) → Phase 1 (log viewer) → Phase 2 (AI agents)
     → Phase 3 (2D map) → Phase 4 (animations) → Phase 5 (tournaments)
     → Phase 6 (engine v0.2) → Phase 7 (3D)
```

Phase 1 + 2 are the immediate next steps — they let you **watch AI agents play** through a web interface without needing any game UI.

Phase 3 is the first visual milestone — a proper 2D map in the browser.

Phases 6-7 are long-term and should only start after the visualization pipeline is solid.
