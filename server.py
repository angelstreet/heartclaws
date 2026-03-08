"""HeartClaws game engine — FastAPI server."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.config import ACTION_ENERGY_COSTS, BUILD_ENERGY_COSTS
from engine.control import get_player_controlled_sectors
from engine.energy import (
    compute_player_available_energy,
    compute_player_income,
    compute_player_upkeep,
)
from engine.engine import (
    get_player_view,
    init_game,
    load_game,
    run_heartbeat,
    save_game,
    submit_action,
)
from engine.enums import ActionType, SectorType, StructureType
from engine.models import Action, GameState
from engine.openworld import (
    apply_open_world_decay,
    init_open_world,
    join_open_world,
    leave_open_world,
    send_message,
    get_messages,
)
from engine.seasons import check_season_boundary, compute_leaderboard, get_current_season
from engine.world import get_open_world_stats

# Import autoplay helpers for WebSocket match runner
from autoplay import (
    BUILTIN_STRATEGIES,
    MatchRunner,
    resolve_strategy,
    _check_elimination,
    _check_domination,
    _check_timeout_winner,
    _collect_stats,
    P1,
    P2,
)

logger = logging.getLogger("heartclaws")

# ---------------------------------------------------------------------------
# Open world state
# ---------------------------------------------------------------------------

open_world_state: GameState | None = None
open_world_save_path: str = "saves/openworld.json"

# Connected WebSocket clients for live world updates
world_ws_clients: set[WebSocket] = set()

HEARTBEAT_INTERVAL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Background heartbeat loop
# ---------------------------------------------------------------------------


async def open_world_heartbeat_loop():
    """Background loop that runs heartbeats for the open world."""
    global open_world_state
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        if open_world_state is None:
            continue

        # 1. Apply decay
        apply_open_world_decay(open_world_state)

        # 2. Run heartbeat (same engine as matches)
        hb_result = run_heartbeat(open_world_state)

        # 3. Check season boundary
        season_result = check_season_boundary(open_world_state)

        # 4. Auto-save
        _ensure_saves_dir()
        save_game(open_world_state, open_world_save_path)

        # 5. Broadcast to WebSocket clients
        payload = {
            "type": "heartbeat",
            "heartbeat": open_world_state.heartbeat,
            "events": _serialize(hb_result.events),
            "season": season_result,
        }
        dead: set[WebSocket] = set()
        for ws in world_ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        world_ws_clients -= dead


def _ensure_saves_dir():
    """Create saves/ directory if it doesn't exist."""
    Path("saves").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


DEFAULT_WORLD_SEED = 2026


@asynccontextmanager
async def lifespan(app):
    global open_world_state
    _ensure_saves_dir()

    # Try to restore from save
    save_path = Path(open_world_save_path)
    if save_path.exists():
        try:
            open_world_state = load_game(open_world_save_path)
            logger.info(
                "Restored open world from %s (heartbeat %d, %d players)",
                open_world_save_path,
                open_world_state.heartbeat,
                len(open_world_state.players),
            )
        except Exception as e:
            logger.warning("Failed to load open world save: %s — creating fresh world", e)
            open_world_state = None

    # Auto-create if no world exists
    if open_world_state is None:
        open_world_state = init_open_world(DEFAULT_WORLD_SEED)
        save_game(open_world_state, open_world_save_path)
        logger.info("Created fresh open world (seed %d)", DEFAULT_WORLD_SEED)

    # Start background heartbeat
    task = asyncio.create_task(open_world_heartbeat_loop())
    yield
    # Save on shutdown
    if open_world_state is not None:
        save_game(open_world_state, open_world_save_path)
        logger.info("Saved open world on shutdown (heartbeat %d)", open_world_state.heartbeat)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="HeartClaws", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store of active games
games: dict[str, GameState] = {}
# AI opponents: game_id -> (player_id, strategy_callable)
game_ai: dict[str, tuple[str, any]] = {}

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
def root_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/viewer")
def viewer():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(obj: Any) -> Any:
    """Recursively convert dataclasses / enums to JSON-safe dicts."""
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _serialize(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    return obj


def _get_game(game_id: str) -> GameState:
    state = games.get(game_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    return state


def _compute_energy_cost(action_type: ActionType, payload: dict) -> int:
    base = ACTION_ENERGY_COSTS.get(action_type, 0)
    if action_type == ActionType.BUILD_STRUCTURE:
        raw = payload.get("structure_type", "")
        try:
            st = StructureType(raw)
        except ValueError:
            st = None
        if st is not None:
            base += BUILD_ENERGY_COSTS.get(st, 0)
    return base


# ---------------------------------------------------------------------------
# WebSocket match helpers
# ---------------------------------------------------------------------------


def _build_sector_list(state: GameState) -> list[dict]:
    """Build a flat list of sector data for the viewer."""
    sectors = []
    for sid, sector in state.world.sectors.items():
        structs = []
        influence_p1 = 0
        influence_p2 = 0
        for st_id in sector.structure_ids:
            st = state.structures.get(st_id)
            if st is not None:
                structs.append({
                    "id": st.structure_id,
                    "type": st.structure_type.value,
                    "owner": st.owner_player_id,
                    "hp": st.hp,
                    "max_hp": st.max_hp,
                    "active": st.active,
                })
                if st.owner_player_id == P1:
                    influence_p1 += st.influence
                elif st.owner_player_id == P2:
                    influence_p2 += st.influence
        has_metal = any(
            not n.depleted for n in sector.resource_nodes
        )
        resource_nodes = []
        for n in sector.resource_nodes:
            resource_nodes.append({
                "type": n.resource_type.value,
                "richness": n.richness,
                "depleted": n.depleted,
            })
        sectors.append({
            "id": sector.sector_id,
            "type": sector.sector_type.value,
            "controller": sector.controller_player_id,
            "structures": structs,
            "has_metal": has_metal,
            "influence_p1": influence_p1,
            "influence_p2": influence_p2,
            "resource_nodes": resource_nodes,
        })
    return sectors


def _build_player_data(
    state: GameState,
    player_id: str,
    strategy_name: str,
    hb_events: list | None = None,
) -> dict:
    """Build player resource data for the viewer."""
    p = state.players[player_id]
    income = compute_player_income(state, player_id)
    upkeep = compute_player_upkeep(state, player_id)
    available = compute_player_available_energy(state, player_id) - p.energy_spent_this_heartbeat
    controlled = get_player_controlled_sectors(state, player_id)

    # Structures owned by this player
    structures = []
    for st_id, st in state.structures.items():
        if st.owner_player_id == player_id:
            structures.append({
                "id": st.structure_id,
                "type": st.structure_type.value,
                "sector": st.sector_id,
                "hp": st.hp,
            })

    # Actions this turn (from heartbeat events)
    actions_this_turn = []
    if hb_events:
        for ev in hb_events:
            if ev.actor_player_id != player_id:
                continue
            details = ev.details or {}
            if ev.event_type == "ACTION_RESOLVED":
                atype = details.get("action_type", "?")
                actions_this_turn.append({
                    "type": atype,
                    "detail": _action_detail(ev),
                    "status": "resolved",
                })
            elif ev.event_type == "ACTION_FAILED":
                atype = details.get("action_type", "?")
                reason = details.get("failure_reason") or details.get("reason", "unknown")
                actions_this_turn.append({
                    "type": atype,
                    "detail": reason,
                    "status": "failed",
                })

    return {
        "energy_reserve": p.energy_reserve,
        "income": income,
        "upkeep": upkeep,
        "available": max(available, 0),
        "metal": p.metal,
        "data": p.data,
        "biomass": p.biomass,
        "sectors_controlled": len(controlled),
        "strategy": strategy_name,
        "structures": structures,
        "actions_this_turn": actions_this_turn,
        "territory": list(controlled),
    }


def _action_detail(ev) -> str:
    """Build a human-readable detail string from an event."""
    details = ev.details or {}
    atype = details.get("action_type", "")
    payload = details.get("payload", {})
    if atype == "BUILD_STRUCTURE":
        stype = payload.get("structure_type", details.get("structure_type", "?"))
        sector = payload.get("sector_id", details.get("sector_id", "?"))
        return f"{stype} in {sector}"
    elif atype == "ATTACK_STRUCTURE":
        target = payload.get("target_structure_id", details.get("target_structure_id", "?"))
        return f"target {target}"
    elif atype == "SCAN_SECTOR":
        sector = payload.get("sector_id", details.get("sector_id", "?"))
        return f"scan {sector}"
    return atype


def _build_events(events, hb: int) -> list[dict]:
    """Convert engine events to viewer-friendly dicts."""
    result = []
    skip_types = {"HEARTBEAT_STARTED", "HEARTBEAT_COMPLETED", "ENERGY_COMPUTED"}
    for ev in events:
        if ev.event_type in skip_types:
            continue
        details = ev.details or {}
        desc = ""
        etype = ev.event_type

        if etype == "ACTION_RESOLVED":
            atype = details.get("action_type", "?")
            desc = f"{(ev.actor_player_id or '?').upper()} resolved {atype}"
        elif etype == "ACTION_FAILED":
            atype = details.get("action_type", "?")
            reason = details.get("failure_reason") or details.get("reason", "unknown")
            desc = f"{(ev.actor_player_id or '?').upper()} {atype} failed: {reason}"
        elif etype == "STRUCTURE_BUILT":
            stype = details.get("structure_type", "?")
            sector = details.get("sector_id", "?")
            desc = f"{(ev.actor_player_id or '?').upper()} built {stype} in {sector}"
        elif etype == "STRUCTURE_ATTACKED":
            dmg = details.get("damage", "?")
            hp = details.get("remaining_hp", "?")
            desc = f"{(ev.actor_player_id or '?').upper()} attacked {ev.target_id} ({dmg} dmg, {hp} HP left)"
        elif etype == "STRUCTURE_DESTROYED":
            sector = details.get("sector_id", "?")
            desc = f"DESTROYED {ev.target_id} in {sector}"
        elif etype == "STRUCTURE_REMOVED":
            desc = f"{(ev.actor_player_id or '?').upper()} removed {ev.target_id}"
        elif etype == "SECTOR_CONTROL_CHANGED":
            old = details.get("old_controller") or "uncontrolled"
            new = details.get("new_controller") or "uncontrolled"
            desc = f"Control changed: {ev.target_id} {old} -> {new}"
        elif etype == "UPKEEP_DEACTIVATION":
            ttype = details.get("target_type", "?")
            desc = f"{(ev.actor_player_id or '?').upper()} deactivated {ttype} {ev.target_id} (upkeep)"
        else:
            desc = f"{etype}"

        result.append({
            "heartbeat": hb,
            "type": etype,
            "player": ev.actor_player_id,
            "description": desc,
        })
    return result


# ---------------------------------------------------------------------------
# WebSocket endpoint: /ws/sim  (continuous open-world simulation)
# Also aliased as /ws/match for backward compat.
# ---------------------------------------------------------------------------


async def _run_sim(websocket: WebSocket):
    """Shared handler for /ws/sim and /ws/match."""
    await websocket.accept()

    paused = False
    step_event = asyncio.Event()
    speed_ms = 500

    try:
        # Wait for "start" message
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            if msg.get("action") == "start":
                break

        # Parse start params
        p1_name = msg.get("p1", "expansionist")
        p2_name = msg.get("p2", "economist")
        seed = int(msg.get("seed", 42))
        speed_ms = int(msg.get("speed_ms", 1000))

        # Resolve strategies
        try:
            strat_p1 = resolve_strategy(p1_name)
            strat_p2 = resolve_strategy(p2_name)
        except ValueError as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

        # Create match runner (max_heartbeats unused — we run indefinitely)
        runner = MatchRunner(
            strategy_p1=strat_p1,
            strategy_p2=strat_p2,
            seed=seed,
            max_heartbeats=999_999_999,
            p1_name=p1_name,
            p2_name=p2_name,
        )

        # Running stats accumulators
        total_structures_built = 0
        total_structures_destroyed = 0
        total_control_changes = 0
        hb = 0

        # Run heartbeats indefinitely until client disconnects
        while True:
            hb += 1

            # Check for control messages (non-blocking)
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=0.01
                    )
                    ctrl = json.loads(raw)
                    action = ctrl.get("action", "")
                    if action == "pause":
                        paused = True
                    elif action == "resume":
                        paused = False
                        step_event.clear()
                    elif action == "step":
                        step_event.set()
                    elif action == "speed":
                        speed_ms = int(ctrl.get("speed_ms", speed_ms))
                except asyncio.TimeoutError:
                    break

            # Wait while paused
            while paused:
                try:
                    raw = await websocket.receive_text()
                    ctrl = json.loads(raw)
                    action = ctrl.get("action", "")
                    if action == "resume":
                        paused = False
                        step_event.clear()
                        break
                    elif action == "step":
                        # Execute one heartbeat then stay paused
                        break
                    elif action == "speed":
                        speed_ms = int(ctrl.get("speed_ms", speed_ms))
                except WebSocketDisconnect:
                    return

            # Run one heartbeat
            hb_result = runner.step()

            # Update running stats from this heartbeat's events
            for ev in hb_result.events:
                if ev.event_type == "STRUCTURE_BUILT":
                    total_structures_built += 1
                elif ev.event_type == "STRUCTURE_DESTROYED":
                    total_structures_destroyed += 1
                elif ev.event_type == "SECTOR_CONTROL_CHANGED":
                    total_control_changes += 1

            # Build payload
            payload = {
                "type": "heartbeat",
                "data": {
                    "heartbeat": hb_result.heartbeat,
                    "map": {
                        "sectors": _build_sector_list(runner.state),
                    },
                    "players": {
                        "p1": _build_player_data(runner.state, P1, p1_name, hb_result.events),
                        "p2": _build_player_data(runner.state, P2, p2_name, hb_result.events),
                    },
                    "events": _build_events(hb_result.events, hb_result.heartbeat),
                    "stats": {
                        "total_heartbeats": hb,
                        "structures_built": total_structures_built,
                        "structures_destroyed": total_structures_destroyed,
                        "control_changes": total_control_changes,
                    },
                },
            }

            await websocket.send_json(payload)

            # Delay between heartbeats
            await asyncio.sleep(speed_ms / 1000.0)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


@app.websocket("/ws/sim")
async def ws_sim(websocket: WebSocket):
    await _run_sim(websocket)


@app.websocket("/ws/match")
async def ws_match(websocket: WebSocket):
    await _run_sim(websocket)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateGameRequest(BaseModel):
    players: list[str]
    seed: int | None = None
    ai_opponent: str | None = None  # e.g. "aggressor" — auto-plays p2 on heartbeat


class CreateGameResponse(BaseModel):
    game_id: str
    heartbeat: int
    players: list[str]
    sector_count: int


class ActionRequest(BaseModel):
    player_id: str
    action_type: str
    payload: dict = Field(default_factory=dict)
    priority: int = 0


class ActionResponse(BaseModel):
    accepted: bool
    action_id: str
    reason: str | None = None
    energy_cost: int = 0


class LoadGameRequest(BaseModel):
    path: str


class WorldJoinRequest(BaseModel):
    name: str
    gateway_id: str | None = None


class WorldLeaveRequest(BaseModel):
    player_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "active_games": len(games)}


# -- Create game -----------------------------------------------------------

@app.post("/games", response_model=CreateGameResponse)
def create_game(req: CreateGameRequest) -> CreateGameResponse:
    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    state = init_game(config=None, players=req.players, seed=seed)
    games[state.game_id] = state

    # Register AI opponent if requested
    if req.ai_opponent:
        try:
            ai_strat = resolve_strategy(req.ai_opponent)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # AI controls the last player (p2)
        ai_player = req.players[-1]
        game_ai[state.game_id] = (ai_player, ai_strat, random.Random(seed))

    return CreateGameResponse(
        game_id=state.game_id,
        heartbeat=state.heartbeat,
        players=list(state.players.keys()),
        sector_count=len(state.world.sectors),
    )


# -- Full game state -------------------------------------------------------

@app.get("/games/{game_id}")
def get_game(game_id: str) -> dict:
    state = _get_game(game_id)
    return _serialize(state)


# -- Player view -----------------------------------------------------------

@app.get("/games/{game_id}/player/{player_id}")
def get_player(game_id: str, player_id: str) -> dict:
    state = _get_game(game_id)
    view = get_player_view(state, player_id)
    if "error" in view:
        raise HTTPException(status_code=404, detail=view["error"])
    return view


# -- Submit action ----------------------------------------------------------

@app.post("/games/{game_id}/actions", response_model=ActionResponse)
def post_action(game_id: str, req: ActionRequest) -> ActionResponse:
    state = _get_game(game_id)

    try:
        action_type = ActionType(req.action_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action_type '{req.action_type}'",
        )

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    energy_cost = _compute_energy_cost(action_type, req.payload)

    action = Action(
        action_id=action_id,
        issuer_player_id=req.player_id,
        issuer_subagent_id=None,
        action_type=action_type,
        payload=req.payload,
        energy_cost=energy_cost,
        submitted_heartbeat=state.heartbeat,
        priority=req.priority,
    )

    result = submit_action(state, action)
    return ActionResponse(
        accepted=result.accepted,
        action_id=result.action_id,
        reason=result.reason,
        energy_cost=energy_cost,
    )


# -- Heartbeat -------------------------------------------------------------

@app.post("/games/{game_id}/heartbeat")
def post_heartbeat(game_id: str) -> dict:
    state = _get_game(game_id)

    # Auto-play AI opponent before resolving heartbeat
    ai_info = game_ai.get(game_id)
    if ai_info:
        ai_player, ai_strat, ai_rng = ai_info
        ai_actions = ai_strat(state, ai_player, ai_rng)
        for a in ai_actions:
            submit_action(state, a)

    result = run_heartbeat(state)
    return {
        "heartbeat": result.heartbeat,
        "events": _serialize(result.events),
    }


# -- Map overview -----------------------------------------------------------

@app.get("/games/{game_id}/map")
def get_map(game_id: str) -> dict:
    state = _get_game(game_id)
    sectors = {}
    for sid, sector in state.world.sectors.items():
        sector_structures = [
            {
                "structure_id": st.structure_id,
                "owner_player_id": st.owner_player_id,
                "structure_type": st.structure_type.value,
                "hp": st.hp,
                "active": st.active,
            }
            for st_id in sector.structure_ids
            if (st := state.structures.get(st_id)) is not None
        ]
        sectors[sid] = {
            "sector_id": sector.sector_id,
            "name": sector.name,
            "sector_type": sector.sector_type.value,
            "adjacent_sector_ids": sector.adjacent_sector_ids,
            "controller_player_id": sector.controller_player_id,
            "structures": sector_structures,
        }
    return {"planet_id": state.world.planet_id, "sectors": sectors}


# -- Save / Load ------------------------------------------------------------

@app.post("/games/{game_id}/save")
def post_save(game_id: str) -> dict:
    state = _get_game(game_id)
    path = f"saves/{game_id}.json"
    save_game(state, path)
    return {"saved": True, "path": path}


@app.post("/games/load")
def post_load(req: LoadGameRequest) -> dict:
    try:
        state = load_game(req.path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Save file not found: {req.path}")
    games[state.game_id] = state
    return {"loaded": True, "game_id": state.game_id, "heartbeat": state.heartbeat}


# ---------------------------------------------------------------------------
# Open World endpoints
# ---------------------------------------------------------------------------


def _get_world() -> GameState:
    """Return the open world state or raise 404."""
    if open_world_state is None:
        raise HTTPException(status_code=404, detail="Open world not created. POST /world/create first.")
    return open_world_state


@app.post("/world/create")
def create_world(seed: int = 42) -> dict:
    """Create persistent open world. Returns world stats."""
    global open_world_state
    open_world_state = init_open_world(seed)
    _ensure_saves_dir()
    save_game(open_world_state, open_world_save_path)
    stats = get_open_world_stats(open_world_state.world)
    return {
        "game_id": open_world_state.game_id,
        "seed": seed,
        "heartbeat": open_world_state.heartbeat,
        **stats,
    }


@app.post("/world/join")
def world_join(request: WorldJoinRequest) -> dict:
    """Join the open world. Returns credentials + spawn info."""
    state = _get_world()
    result = join_open_world(state, request.name, request.gateway_id)
    _ensure_saves_dir()
    save_game(state, open_world_save_path)
    return result


@app.post("/world/leave")
def world_leave(request: WorldLeaveRequest) -> dict:
    """Graceful leave. Structures become ruins."""
    state = _get_world()
    player = state.players.get(request.player_id)
    if player is None:
        raise HTTPException(status_code=400, detail=f"Player '{request.player_id}' not found")
    result = leave_open_world(state, request.player_id)
    _ensure_saves_dir()
    save_game(state, open_world_save_path)
    return result


@app.get("/world/state")
def world_state() -> dict:
    """Full world state as JSON."""
    state = _get_world()
    return _serialize(state)


@app.get("/world/state/{player_id}")
def world_player_state(player_id: str) -> dict:
    """Player-specific view."""
    state = _get_world()
    view = get_player_view(state, player_id)
    if "error" in view:
        raise HTTPException(status_code=404, detail=view["error"])
    return view


@app.post("/world/action")
def world_action(request: dict) -> dict:
    """Submit actions to the open world."""
    state = _get_world()

    player_id = request.get("player_id")
    action_type_raw = request.get("action_type")
    payload = request.get("payload", {})
    priority = request.get("priority", 0)

    if not player_id:
        raise HTTPException(status_code=400, detail="Missing player_id")
    if not action_type_raw:
        raise HTTPException(status_code=400, detail="Missing action_type")

    player = state.players.get(player_id)
    if player is None:
        raise HTTPException(status_code=400, detail=f"Player '{player_id}' not found")

    try:
        action_type = ActionType(action_type_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_type '{action_type_raw}'")

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    energy_cost = _compute_energy_cost(action_type, payload)

    action = Action(
        action_id=action_id,
        issuer_player_id=player_id,
        issuer_subagent_id=None,
        action_type=action_type,
        payload=payload,
        energy_cost=energy_cost,
        submitted_heartbeat=state.heartbeat,
        priority=priority,
    )

    result = submit_action(state, action)

    # Update player's last_active_heartbeat
    player.last_active_heartbeat = state.heartbeat

    # Auto-save
    _ensure_saves_dir()
    save_game(state, open_world_save_path)

    return {
        "accepted": result.accepted,
        "action_id": result.action_id,
        "reason": result.reason,
        "energy_cost": energy_cost,
    }


@app.post("/world/heartbeat")
def world_heartbeat() -> dict:
    """Manually trigger a heartbeat for the open world."""
    state = _get_world()
    apply_open_world_decay(state)
    result = run_heartbeat(state)
    season_result = check_season_boundary(state)
    _ensure_saves_dir()
    save_game(state, open_world_save_path)
    resp = {"heartbeat": result.heartbeat, "events": _serialize(result.events)}
    if season_result:
        resp["season"] = season_result
    return resp


@app.get("/world/leaderboard")
def world_leaderboard() -> list:
    """Current leaderboard."""
    state = _get_world()
    return compute_leaderboard(state)


@app.get("/world/season")
def world_season() -> dict:
    """Season info + time remaining."""
    state = _get_world()
    return get_current_season(state)


@app.post("/world/message")
def world_message(request: dict) -> dict:
    """Send diplomatic message."""
    state = _get_world()

    from_player_id = request.get("from_player_id")
    to_player_id = request.get("to_player_id")
    message = request.get("message")

    if not from_player_id or not to_player_id or not message:
        raise HTTPException(status_code=400, detail="Missing from_player_id, to_player_id, or message")

    if from_player_id not in state.players:
        raise HTTPException(status_code=400, detail=f"Player '{from_player_id}' not found")
    if to_player_id not in state.players:
        raise HTTPException(status_code=400, detail=f"Player '{to_player_id}' not found")

    result = send_message(state, from_player_id, to_player_id, message)

    # Auto-save
    _ensure_saves_dir()
    save_game(state, open_world_save_path)

    return result


@app.get("/world/messages/{player_id}")
def world_messages(player_id: str) -> list:
    """Read messages for a player."""
    state = _get_world()
    if player_id not in state.players:
        raise HTTPException(status_code=400, detail=f"Player '{player_id}' not found")
    return get_messages(state, player_id)


@app.get("/world/history")
def world_history(limit: int = 50, offset: int = 0) -> dict:
    """Event log (paginated)."""
    state = _get_world()
    events = state.event_log
    total = len(events)
    page = events[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "events": _serialize(page),
    }


# ---------------------------------------------------------------------------
# Open World WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/world")
async def ws_world(websocket: WebSocket):
    """Live heartbeat stream for viewers."""
    await websocket.accept()
    world_ws_clients.add(websocket)
    try:
        while True:
            # Keep connection alive; ignore client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        world_ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=5020, reload=True)
