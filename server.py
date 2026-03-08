"""HeartClaws game engine — FastAPI server."""

from __future__ import annotations

import asyncio
import json
import random
import uuid
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

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="HeartClaws", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store of active games
games: dict[str, GameState] = {}

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
        for st_id in sector.structure_ids:
            st = state.structures.get(st_id)
            if st is not None:
                structs.append({
                    "id": st.structure_id,
                    "type": st.structure_type.value,
                    "owner": st.owner_player_id,
                    "hp": st.hp,
                    "active": st.active,
                })
        has_metal = any(
            not n.depleted for n in sector.resource_nodes
        )
        sectors.append({
            "id": sector.sector_id,
            "type": sector.sector_type.value,
            "controller": sector.controller_player_id,
            "structures": structs,
            "has_metal": has_metal,
        })
    return sectors


def _build_player_data(state: GameState, player_id: str, strategy_name: str) -> dict:
    """Build player resource data for the viewer."""
    p = state.players[player_id]
    income = compute_player_income(state, player_id)
    upkeep = compute_player_upkeep(state, player_id)
    available = compute_player_available_energy(state, player_id) - p.energy_spent_this_heartbeat
    controlled = get_player_controlled_sectors(state, player_id)
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
    }


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
# WebSocket endpoint: /ws/match
# ---------------------------------------------------------------------------


@app.websocket("/ws/match")
async def ws_match(websocket: WebSocket):
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
        max_heartbeats = int(msg.get("max_heartbeats", 30))
        speed_ms = int(msg.get("speed_ms", 500))

        # Resolve strategies
        try:
            strat_p1 = resolve_strategy(p1_name)
            strat_p2 = resolve_strategy(p2_name)
        except ValueError as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

        # Create match runner
        runner = MatchRunner(
            strategy_p1=strat_p1,
            strategy_p2=strat_p2,
            seed=seed,
            max_heartbeats=max_heartbeats,
            p1_name=p1_name,
            p2_name=p2_name,
        )

        # Run heartbeats
        for hb in range(1, max_heartbeats + 1):
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

            # Build payload
            payload = {
                "type": "heartbeat",
                "data": {
                    "heartbeat": hb_result.heartbeat,
                    "map": {
                        "sectors": _build_sector_list(runner.state),
                    },
                    "players": {
                        "p1": _build_player_data(runner.state, P1, p1_name),
                        "p2": _build_player_data(runner.state, P2, p2_name),
                    },
                    "events": _build_events(hb_result.events, hb_result.heartbeat),
                },
            }

            await websocket.send_json(payload)

            # Check win conditions
            win = runner._check_win()
            if win is not None:
                winner, reason = win
                p1_stats = _collect_stats(runner.state, P1, p1_name, runner.all_events)
                p2_stats = _collect_stats(runner.state, P2, p2_name, runner.all_events)
                await websocket.send_json({
                    "type": "result",
                    "data": {
                        "winner": winner,
                        "reason": reason,
                        "heartbeats": hb,
                        "stats": {
                            "p1": {
                                "strategy": p1_name,
                                "sectors_controlled": p1_stats.sectors_controlled,
                                "structures_built": p1_stats.structures_built,
                                "structures_lost": p1_stats.structures_lost,
                                "attacks_made": p1_stats.attacks_made,
                                "total_energy_earned": p1_stats.total_energy_earned,
                                "final_metal": p1_stats.final_metal,
                            },
                            "p2": {
                                "strategy": p2_name,
                                "sectors_controlled": p2_stats.sectors_controlled,
                                "structures_built": p2_stats.structures_built,
                                "structures_lost": p2_stats.structures_lost,
                                "attacks_made": p2_stats.attacks_made,
                                "total_energy_earned": p2_stats.total_energy_earned,
                                "final_metal": p2_stats.final_metal,
                            },
                        },
                    },
                })
                return

            # Delay between heartbeats
            await asyncio.sleep(speed_ms / 1000.0)

        # Timeout — match ended without early win
        winner = _check_timeout_winner(runner.state)
        p1_stats = _collect_stats(runner.state, P1, p1_name, runner.all_events)
        p2_stats = _collect_stats(runner.state, P2, p2_name, runner.all_events)
        await websocket.send_json({
            "type": "result",
            "data": {
                "winner": winner,
                "reason": "timeout",
                "heartbeats": max_heartbeats,
                "stats": {
                    "p1": {
                        "strategy": p1_name,
                        "sectors_controlled": p1_stats.sectors_controlled,
                        "structures_built": p1_stats.structures_built,
                        "structures_lost": p1_stats.structures_lost,
                        "attacks_made": p1_stats.attacks_made,
                        "total_energy_earned": p1_stats.total_energy_earned,
                        "final_metal": p1_stats.final_metal,
                    },
                    "p2": {
                        "strategy": p2_name,
                        "sectors_controlled": p2_stats.sectors_controlled,
                        "structures_built": p2_stats.structures_built,
                        "structures_lost": p2_stats.structures_lost,
                        "attacks_made": p2_stats.attacks_made,
                        "total_energy_earned": p2_stats.total_energy_earned,
                        "final_metal": p2_stats.final_metal,
                    },
                },
            },
        })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateGameRequest(BaseModel):
    players: list[str]
    seed: int | None = None


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
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=5020, reload=True)
