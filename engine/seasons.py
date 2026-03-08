"""Phase OW-5: Seasons & Leaderboard."""

from __future__ import annotations

import random
from collections import defaultdict

from .config import (
    BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT,
    DATA_HARVESTER_DATA_PER_HEARTBEAT,
    EXTRACTOR_METAL_PER_HEARTBEAT,
    STRUCTURE_CATALOG,
)
from .enums import ResourceType, SectorType, StructureType
from .events import emit_event
from .models import Event, GameState, ResourceNode, next_id

SEASON_LENGTH = 2000  # heartbeats per season (~7 days)

# Leaderboard scoring weights
SCORE_WEIGHTS = {
    "territory": 0.30,
    "economy": 0.25,
    "military": 0.20,
    "longevity": 0.15,
    "influence": 0.10,
}


def _compute_player_income(state: GameState, player_id: str) -> float:
    """Compute total resource income per heartbeat for a player."""
    income = 0.0
    for structure in state.structures.values():
        if structure.owner_player_id != player_id or not structure.active:
            continue
        sector = state.world.sectors.get(structure.sector_id)
        if sector is None:
            continue

        if structure.structure_type == StructureType.EXTRACTOR:
            has_node = any(
                n.resource_type == ResourceType.METAL and not n.depleted
                for n in sector.resource_nodes
            )
            if has_node:
                income += EXTRACTOR_METAL_PER_HEARTBEAT

        elif structure.structure_type == StructureType.DATA_HARVESTER:
            has_node = any(
                n.resource_type == ResourceType.DATA and not n.depleted
                for n in sector.resource_nodes
            )
            if has_node:
                income += DATA_HARVESTER_DATA_PER_HEARTBEAT

        elif structure.structure_type == StructureType.BIO_CULTIVATOR:
            has_node = any(
                n.resource_type == ResourceType.BIOMASS and not n.depleted
                for n in sector.resource_nodes
            )
            if has_node:
                income += BIO_CULTIVATOR_BIOMASS_PER_HEARTBEAT

    return income


def _compute_player_influence(state: GameState, player_id: str) -> int:
    """Compute total influence across all sectors for a player."""
    total = 0
    for structure in state.structures.values():
        if structure.owner_player_id == player_id and structure.active:
            total += structure.influence
    return total


def compute_player_score(state: GameState, player_id: str) -> dict:
    """Compute multi-dimensional score for a player.

    Returns dict with territory, economy, military, longevity, influence, and composite.
    """
    player = state.players[player_id]

    # Territory: sectors controlled
    territory = sum(
        1 for sector in state.world.sectors.values()
        if sector.controller_player_id == player_id
    )

    # Economy: total resource income per heartbeat
    economy = _compute_player_income(state, player_id)

    # Military: structures destroyed - structures lost
    military = player.structures_destroyed - player.structures_lost

    # Longevity: consecutive heartbeats alive (heartbeats since spawn)
    longevity = state.heartbeat - player.spawn_heartbeat

    # Influence: total influence across all structures
    influence = _compute_player_influence(state, player_id)

    return {
        "player_id": player_id,
        "territory": territory,
        "economy": economy,
        "military": military,
        "longevity": longevity,
        "influence": influence,
        "composite": _compute_composite(territory, economy, military, longevity, influence),
    }


def _compute_composite(
    territory: int,
    economy: float,
    military: int,
    longevity: int,
    influence: int,
) -> float:
    """Compute weighted composite score normalized 0-100.

    Each dimension is scored as its raw value multiplied by its weight,
    then summed. The result is clamped to 0-100.
    """
    raw = (
        territory * SCORE_WEIGHTS["territory"]
        + economy * SCORE_WEIGHTS["economy"]
        + military * SCORE_WEIGHTS["military"]
        + longevity * SCORE_WEIGHTS["longevity"]
        + influence * SCORE_WEIGHTS["influence"]
    )
    return min(100.0, max(0.0, raw))


def compute_leaderboard(state: GameState) -> list[dict]:
    """Compute leaderboard for all alive players, sorted by composite score descending."""
    scores = []
    for player_id, player in state.players.items():
        if player.alive:
            scores.append(compute_player_score(state, player_id))
    scores.sort(key=lambda s: s["composite"], reverse=True)
    return scores


def get_current_season(state: GameState) -> dict:
    """Return current season info."""
    season = state.heartbeat // SEASON_LENGTH + 1
    season_start = (season - 1) * SEASON_LENGTH
    season_end = season * SEASON_LENGTH
    remaining = season_end - state.heartbeat
    progress = (state.heartbeat - season_start) / SEASON_LENGTH if SEASON_LENGTH > 0 else 0.0

    return {
        "season": season,
        "heartbeat": state.heartbeat,
        "season_start": season_start,
        "season_end": season_end,
        "remaining": remaining,
        "progress": progress,
    }


def check_season_boundary(state: GameState) -> dict | None:
    """Check if we just crossed a season boundary. Called each heartbeat.

    If heartbeat is exactly at a season boundary (heartbeat % SEASON_LENGTH == 0 and heartbeat > 0):
    1. Snapshot leaderboard
    2. Determine season rewards (rank #1 = win, top 3 = draw, rest = loss)
    3. Trigger a random world event
    4. Return season report dict

    Otherwise return None.
    """
    if state.heartbeat == 0 or state.heartbeat % SEASON_LENGTH != 0:
        return None

    season_number = state.heartbeat // SEASON_LENGTH  # season that just ended

    # 1. Snapshot leaderboard
    leaderboard = compute_leaderboard(state)

    # 2. Determine rewards
    results: list[dict] = []
    total_players = len(leaderboard)
    for rank_idx, entry in enumerate(leaderboard):
        rank = rank_idx + 1
        if rank == 1:
            outcome = "win"
        elif rank <= 3:
            outcome = "draw"
        else:
            outcome = "loss"
        results.append({
            "player_id": entry["player_id"],
            "rank": rank,
            "composite": entry["composite"],
            "outcome": outcome,
        })

    # Update ELO for all players
    if total_players > 0:
        field_avg_elo = sum(
            state.player_elo.get(e["player_id"], 1200) for e in leaderboard
        ) // total_players

        for result in results:
            pid = result["player_id"]
            current_elo = state.player_elo.get(pid, 1200)
            elo_change = compute_elo_change(
                current_elo, field_avg_elo, result["rank"], total_players
            )
            state.player_elo[pid] = current_elo + elo_change
            result["elo_change"] = elo_change
            result["new_elo"] = state.player_elo[pid]

    # 3. Trigger a random world event
    rng = random.Random(state.seed + state.heartbeat)
    event_types = ["SOLAR_STORM", "RESOURCE_SURGE", "DECAY_WAVE", "NEW_DEPOSITS", "RADIATION_BELT"]
    event_type = rng.choice(event_types)
    world_events = apply_world_event(state, event_type)

    # Build season report
    season_report = {
        "season": season_number,
        "heartbeat": state.heartbeat,
        "leaderboard": leaderboard,
        "results": results,
        "world_event": event_type,
        "world_event_details": [
            {"event_id": e.event_id, "event_type": e.event_type, "details": e.details}
            for e in world_events
        ],
    }

    # Store in history
    state.season_history.append(season_report)

    return season_report


def compute_elo_change(
    player_elo: int, field_avg_elo: int, score_rank: int, total_players: int
) -> int:
    """Standard ELO calculation.

    - K-factor: 32
    - Expected score = 1 / (1 + 10^((field_avg - player_elo) / 400))
    - Actual score: rank 1 = 1.0, top 25% = 0.75, top 50% = 0.5, bottom = 0.25
    - ELO change = K * (actual - expected)
    """
    K = 32

    # Expected score
    expected = 1.0 / (1.0 + 10 ** ((field_avg_elo - player_elo) / 400.0))

    # Actual score based on rank position
    if total_players <= 1:
        actual = 1.0
    elif score_rank == 1:
        actual = 1.0
    else:
        percentile = score_rank / total_players
        if percentile <= 0.25:
            actual = 0.75
        elif percentile <= 0.50:
            actual = 0.5
        else:
            actual = 0.25

    return round(K * (actual - expected))


def apply_world_event(state: GameState, event_type: str) -> list[Event]:
    """Apply a world event. Returns list of Event objects."""
    events: list[Event] = []

    if event_type == "SOLAR_STORM":
        # Double reactor output for next 200 heartbeats — store modifier
        expiry = state.heartbeat + 200
        state.world_events_active.append({
            "event_type": "SOLAR_STORM",
            "start_heartbeat": state.heartbeat,
            "expiry_heartbeat": expiry,
            "effect": "reactor_output_2x",
        })
        evt = emit_event(
            state, "WORLD_EVENT",
            details={
                "world_event": "SOLAR_STORM",
                "effect": "reactor_output_2x",
                "duration": 200,
                "expiry_heartbeat": expiry,
            },
        )
        events.append(evt)

    elif event_type == "RESOURCE_SURGE":
        # All extractors/harvesters/cultivators produce 2x for 100 heartbeats
        expiry = state.heartbeat + 100
        state.world_events_active.append({
            "event_type": "RESOURCE_SURGE",
            "start_heartbeat": state.heartbeat,
            "expiry_heartbeat": expiry,
            "effect": "resource_production_2x",
        })
        evt = emit_event(
            state, "WORLD_EVENT",
            details={
                "world_event": "RESOURCE_SURGE",
                "effect": "resource_production_2x",
                "duration": 100,
                "expiry_heartbeat": expiry,
            },
        )
        events.append(evt)

    elif event_type == "DECAY_WAVE":
        # All WASTELAND structures lose 5 HP
        for st_id, structure in list(state.structures.items()):
            sector = state.world.sectors.get(structure.sector_id)
            if sector and sector.sector_type == SectorType.WASTELAND:
                structure.hp -= 5
                evt = emit_event(
                    state, "WORLD_EVENT_DECAY",
                    target_id=st_id,
                    details={
                        "world_event": "DECAY_WAVE",
                        "damage": 5,
                        "new_hp": structure.hp,
                        "sector_id": structure.sector_id,
                    },
                )
                events.append(evt)
                # Destroy if HP <= 0
                if structure.hp <= 0:
                    sector_obj = state.world.sectors.get(structure.sector_id)
                    if sector_obj and st_id in sector_obj.structure_ids:
                        sector_obj.structure_ids.remove(st_id)
                    del state.structures[st_id]
                    evt_destroy = emit_event(
                        state, "WORLD_EVENT_DESTROYED",
                        target_id=st_id,
                        details={
                            "world_event": "DECAY_WAVE",
                            "sector_id": structure.sector_id,
                        },
                    )
                    events.append(evt_destroy)

    elif event_type == "NEW_DEPOSITS":
        # 4 random sectors gain new resource nodes (permanent)
        rng = random.Random(state.seed + state.heartbeat + 999)
        all_sector_ids = list(state.world.sectors.keys())
        rng.shuffle(all_sector_ids)
        resource_types = [ResourceType.METAL, ResourceType.DATA, ResourceType.BIOMASS]
        count = 0
        for sid in all_sector_ids:
            if count >= 4:
                break
            sector = state.world.sectors[sid]
            res_type = rng.choice(resource_types)
            node_id = next_id(state, "node")
            node = ResourceNode(
                node_id=node_id,
                resource_type=res_type,
                richness=rng.randint(3, 6),
            )
            sector.resource_nodes.append(node)
            count += 1
            evt = emit_event(
                state, "WORLD_EVENT_NEW_DEPOSIT",
                target_id=sid,
                details={
                    "world_event": "NEW_DEPOSITS",
                    "node_id": node_id,
                    "resource_type": res_type.value,
                    "richness": node.richness,
                    "sector_id": sid,
                },
            )
            events.append(evt)

    elif event_type == "RADIATION_BELT":
        # Random row of sectors becomes unbuildable for 50 heartbeats
        rng = random.Random(state.seed + state.heartbeat + 777)
        row = rng.randint(0, 7)
        expiry = state.heartbeat + 50
        affected_sectors = [
            f"H_{q}_{row}" for q in range(8)
            if f"H_{q}_{row}" in state.world.sectors
        ]
        state.world_events_active.append({
            "event_type": "RADIATION_BELT",
            "start_heartbeat": state.heartbeat,
            "expiry_heartbeat": expiry,
            "effect": "unbuildable",
            "affected_sectors": affected_sectors,
            "row": row,
        })
        evt = emit_event(
            state, "WORLD_EVENT",
            details={
                "world_event": "RADIATION_BELT",
                "effect": "unbuildable",
                "row": row,
                "duration": 50,
                "expiry_heartbeat": expiry,
                "affected_sectors": affected_sectors,
            },
        )
        events.append(evt)

    return events
