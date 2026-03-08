"""HeartClaws game engine — FastAPI server."""

from __future__ import annotations

import random
import uuid
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine.config import ACTION_ENERGY_COSTS, BUILD_ENERGY_COSTS
from engine.engine import (
    get_player_view,
    init_game,
    load_game,
    run_heartbeat,
    save_game,
    submit_action,
)
from engine.enums import ActionType, StructureType
from engine.models import Action, GameState

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

    uvicorn.run("server:app", host="0.0.0.0", port=5013, reload=True)
