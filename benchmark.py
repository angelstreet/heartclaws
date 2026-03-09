#!/usr/bin/env python3
"""HeartClaws AI Benchmark — Battle Royale.

Spawns N agents into an isolated game instance (never touches the persistent open world).
Each heartbeat, each agent reads its state, asks its LLM for actions, and submits them.
Results auto-report to Ranking of Claws.

Usage:
    python3 benchmark.py                    # 100 turns, all models
    python3 benchmark.py --turns 500        # 500 turns
    python3 benchmark.py --models claude,gpt4o  # specific models only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("benchmark")

# Silence noisy httpx request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HEARTCLAWS_API = os.environ.get("HEARTCLAWS_API", "http://localhost:5020")

# Load API keys from openclaw.json and secrets
def _load_secrets():
    keys = {}
    # 1. Load from openclaw.json (MiniMax, OpenRouter)
    oc_path = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(oc_path):
        with open(oc_path) as fh:
            oc = json.load(fh)
        # MiniMax key from env section
        for k, v in oc.get("env", {}).items():
            keys[k] = v
        # OpenRouter key from models.providers
        or_cfg = oc.get("models", {}).get("providers", {}).get("openrouter", {})
        if or_cfg.get("apiKey"):
            keys["OPENROUTER_API_KEY"] = or_cfg["apiKey"]
    # 2. Load from secrets/*.env
    secrets_dir = os.path.expanduser("~/.openclaw/secrets")
    for f in ["ai.env", "gemini.env", "google.env"]:
        path = os.path.join(secrets_dir, f)
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        if line.startswith("export "):
                            line = line[7:]
                        k, v = line.split("=", 1)
                        keys[k.strip()] = v.strip().strip("'\"")
    return keys

SECRETS = _load_secrets()

# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    name: str           # Display name
    model_id: str       # API model ID
    provider: str       # anthropic, openai, gemini, mistral
    api_key_env: str    # Key name in secrets


MODELS: dict[str, ModelConfig] = {
    # --- Paid / fast models (default benchmark lineup) ---
    "minimax-m25hs": ModelConfig(
        name="MiniMax M2.5 HS",
        model_id="MiniMax-M2.5-highspeed",
        provider="minimax",
        api_key_env="MINIMAX_API_KEY",
    ),
    "minimax-01": ModelConfig(
        name="MiniMax 01",
        model_id="minimax-01",
        provider="minimax",
        api_key_env="MINIMAX_API_KEY",
    ),
    "gpt4o-mini": ModelConfig(
        name="GPT-4o Mini",
        model_id="gpt-4o-mini",
        provider="openai",
        api_key_env="OPENAI_API_KEY",
    ),
    "codestral": ModelConfig(
        name="Codestral",
        model_id="codestral-latest",
        provider="mistral",
        api_key_env="CODESTRAL_API_KEY",
    ),
    "mistral-small": ModelConfig(
        name="Mistral Small",
        model_id="mistralai/mistral-small-3.1-24b-instruct",
        provider="openrouter",
        api_key_env="OPENROUTER_API_KEY",
    ),
    # --- Other paid models (add via --models) ---
    "gpt4o": ModelConfig(
        name="GPT-4o",
        model_id="gpt-4o",
        provider="openai",
        api_key_env="OPENAI_API_KEY",
    ),
    "grok": ModelConfig(
        name="Grok Code",
        model_id="x-ai/grok-code-fast-1",
        provider="openrouter",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "claude-sonnet": ModelConfig(
        name="Claude Sonnet 4",
        model_id="anthropic/claude-sonnet-4",
        provider="openrouter",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "minimax-direct": ModelConfig(
        name="MiniMax M1",
        model_id="MiniMax-M1-80k",
        provider="minimax",
        api_key_env="MINIMAX_API_KEY",
    ),
    "gemini-flash": ModelConfig(
        name="Gemini 2.0 Flash",
        model_id="gemini-2.0-flash",
        provider="gemini",
        api_key_env="GOOGLE_API_KEY",
    ),
}

# ---------------------------------------------------------------------------
# LLM call abstraction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an AI agent playing HeartClaws, a hex-grid strategy game. Respond ONLY with a JSON array of 1-5 actions. No explanation, just valid JSON.

Format: [{"action_type": "...", "payload": {...}}, ...]

ACTIONS:
- BUILD_STRUCTURE: {"sector_id": "H_x_y", "structure_type": "TYPE"}
- ATTACK_STRUCTURE: {"target_structure_id": "st_xxx"}
- SET_POLICY: {"target_player_id": "pN", "stance": "ALLY|NEUTRAL|HOSTILE"}
- TRANSFER_RESOURCE: {"target_player_id": "pN", "resource_type": "METAL|DATA|BIOMASS", "amount": N}
- SCAN_SECTOR: {"sector_id": "H_x_y"}
- REMOVE_STRUCTURE: {"structure_id": "st_xxx"}

STRUCTURE TYPES (energy_cost + metal + data + biomass):
- EXTRACTOR: 4E+6M — requires METAL resource node in sector → +3 metal/turn
- DATA_HARVESTER: 4E+4M+2D — requires DATA node → +3 data/turn
- BIO_CULTIVATOR: 4E+4M+3B — requires BIOMASS node → +3 biomass/turn
- TOWER: 4E+5M — no resource node required → claims uncontrolled sector
- REACTOR: 8E+10M — no resource node required → +8 energy/turn
- ATTACK_NODE: 6E+9M+1D | SHIELD_GENERATOR: 6E+8M+5B | TRADE_HUB: 7E+10M+3D | OUTPOST: 10E+15M+2D

BUILD RULES:
- Your game state includes "sector_details" — this shows ALL sectors you can build in (your controlled ones + adjacent uncontrolled ones) WITH their resource_nodes.
- Build EXTRACTOR in a sector with resource_nodes containing type="METAL"
- Build DATA_HARVESTER in a sector with resource_nodes containing type="DATA"
- Build BIO_CULTIVATOR in a sector with resource_nodes containing type="BIOMASS"
- Build TOWER in any sector where can_build_tower=true (adjacent, uncontrolled)
- You can build multiple structures per turn if you have resources

HOW TO PLAY (strictly follow this each turn):
STEP 1: Look at sector_details. For each sector in it:
  - If it has resource_nodes and you control it → BUILD matching extractor/harvester/cultivator
  - If can_build_tower=true → BUILD TOWER to expand territory
STEP 2: If you have spare resources, build REACTOR in your home sector (needs 10M)
STEP 3: Only SCAN if sector_details is empty — NEVER scan sectors already in sector_details

CRITICAL RULES:
- NEVER submit SCAN for a sector already listed in sector_details — you already have that info
- NEVER submit BUILD_STRUCTURE for a structure type that doesn't match the sector's resource_nodes
- Check "affordable_structures" to verify you can afford what you want to build
- If last turn had rejected actions, avoid repeating those mistakes

Respond ONLY with a valid JSON array."""


async def call_llm(client: httpx.AsyncClient, model: ModelConfig, state_json: str) -> list[dict]:
    """Call an LLM and parse its action response."""
    api_key = SECRETS.get(model.api_key_env) or os.environ.get(model.api_key_env, "")
    if not api_key:
        # For Anthropic, try the env var that Claude Code uses
        if model.provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            log.warning("No API key for %s (%s) — skipping", model.name, model.api_key_env)
            return []

    user_msg = f"Here is your current game state:\n\n{state_json}\n\nRespond with a JSON array of 1-3 actions."

    try:
        if model.provider == "anthropic":
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model.model_id,
                    "max_tokens": 1024,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
                timeout=30,
            )
            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "[]")

        elif model.provider == "openai":
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model.model_id,
                    "max_tokens": 1024,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                },
                timeout=30,
            )
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")

        elif model.provider == "gemini":
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model.model_id}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": user_msg}]}],
                    "generationConfig": {"maxOutputTokens": 1024},
                },
                timeout=30,
            )
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "[]")
            )

        elif model.provider == "openrouter":
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://pikaai.me",
                },
                json={
                    "model": model.model_id,
                    "max_tokens": 1024,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                },
                timeout=60,
            )
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")

        elif model.provider == "minimax":
            # MiniMax Anthropic-compatible endpoint
            resp = await client.post(
                "https://api.minimax.io/anthropic/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model.model_id,
                    "max_tokens": 1024,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
                timeout=60,
            )
            data = resp.json()
            if resp.status_code >= 400:
                log.warning("MiniMax API error %d: %s", resp.status_code, data.get("error", data))
                return []
            # content may have thinking blocks before the text block
            text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "[]")

        elif model.provider == "mistral":
            base_url = "https://codestral.mistral.ai" if "codestral" in model.model_id else "https://api.mistral.ai"
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model.model_id,
                    "max_tokens": 1024,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                },
                timeout=30,
            )
            data = resp.json()
            if resp.status_code >= 400:
                log.warning("Mistral API error %d for %s: %s", resp.status_code, model.model_id, data)
                return []
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")

        else:
            log.warning("Unknown provider: %s", model.provider)
            return []

        # Parse JSON from response (handle markdown code blocks, thinking tags, prose)
        if not text:
            log.warning("%s returned empty content", model.name)
            return []
        text = text.strip()
        # Strip <think>...</think> tags from thinking models
        import re
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if not text:
            return []
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Model may have wrapped JSON in prose — extract first JSON array
        match = re.search(r'\[.*\]', text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Model returned a single object instead of array — wrap it
        match = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if match:
            try:
                return [json.loads(match.group())]
            except json.JSONDecodeError:
                pass
        log.warning("%s returned unparseable response: %s", model.name, text[:120])
        return []

    except json.JSONDecodeError as e:
        log.warning("%s returned invalid JSON: %s — raw: %s", model.name, e, (text or "")[:100])
        return []
    except Exception as e:
        log.warning("%s LLM call failed: %s", model.name, e)
        return []


# ---------------------------------------------------------------------------
# HeartClaws API helpers
# ---------------------------------------------------------------------------

async def hc_get(client: httpx.AsyncClient, path: str) -> dict | list | None:
    try:
        resp = await client.get(f"{HEARTCLAWS_API}{path}", timeout=10)
        return resp.json()
    except Exception as e:
        log.error("GET %s failed: %s", path, e)
        return None


async def hc_post(client: httpx.AsyncClient, path: str, data: dict) -> dict | None:
    try:
        resp = await client.post(f"{HEARTCLAWS_API}{path}", json=data, timeout=10)
        result = resp.json()
        # Convert HTTP error responses to a rejected result
        if resp.status_code >= 400:
            detail = result.get("detail", str(result))
            return {"accepted": False, "reason": detail}
        return result
    except Exception as e:
        log.error("POST %s failed: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Benchmark agent
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkAgent:
    model_key: str
    model: ModelConfig
    player_id: str | None = None
    sector_id: str | None = None
    gateway_id: str = ""
    actions_submitted: int = 0
    actions_failed: int = 0
    errors: int = 0
    last_rejections: list[str] = field(default_factory=list)
    all_errors: list[str] = field(default_factory=list)


async def join_agent(client: httpx.AsyncClient, agent: BenchmarkAgent, game_id: str) -> bool:
    """Join a benchmark game."""
    agent.gateway_id = hashlib.sha256(
        f"benchmark-{agent.model_key}-{agent.model.name}".encode()
    ).hexdigest()[:16]

    result = await hc_post(client, f"/games/{game_id}/join", {
        "name": f"[BM] {agent.model.name}",
        "gateway_id": agent.gateway_id,
        "model": agent.model.model_id,
    })
    if result and "player_id" in result:
        agent.player_id = result["player_id"]
        agent.sector_id = result.get("sector_id")
        log.info("Joined: %s as %s in %s", agent.model.name, agent.player_id, agent.sector_id)
        return True
    log.error("Failed to join: %s — %s", agent.model.name, result)
    return False


async def play_turn(client: httpx.AsyncClient, agent: BenchmarkAgent, game_id: str) -> None:
    """One turn: read state, ask LLM, submit actions."""
    if not agent.player_id:
        return

    # Get state
    state = await hc_get(client, f"/games/{game_id}/player/{agent.player_id}")
    if not state:
        agent.errors += 1
        agent.all_errors.append("GET player state failed")
        return

    # Compute what the player can afford this turn
    player = state.get("player", {})
    metal = player.get("metal", 0)
    data = player.get("data", 0)
    biomass = player.get("biomass", 0)
    energy = state.get("energy", {})
    income = state.get("income", 0)
    upkeep = state.get("upkeep", 0)
    net_energy = income - upkeep
    COSTS = {
        "EXTRACTOR": (6, 0, 0), "DATA_HARVESTER": (4, 2, 0), "BIO_CULTIVATOR": (4, 0, 3),
        "TOWER": (5, 0, 0), "REACTOR": (10, 0, 0), "ATTACK_NODE": (9, 1, 0),
        "SHIELD_GENERATOR": (8, 0, 5), "TRADE_HUB": (10, 3, 0), "OUTPOST": (15, 2, 0),
    }
    affordable = [s for s, (m, d, b) in COSTS.items() if metal >= m and data >= d and biomass >= b]

    # Trim state to reduce tokens — send only what matters
    compact_state = {
        "player": player,
        "resources": {"metal": metal, "data": data, "biomass": biomass},
        "energy": {"net_per_turn": net_energy, "income": income, "upkeep": upkeep},
        "affordable_structures": affordable,
        "controlled_sectors": state.get("controlled_sectors"),
        "sector_details": state.get("sector_details", {}),
        "structures": state.get("structures"),
        "leaderboard_rank": state.get("leaderboard_rank"),
    }
    state_json = json.dumps(compact_state, default=str)

    # Include rejection feedback from last turn so the model can learn
    if agent.last_rejections:
        state_json += "\n\nLAST TURN REJECTED ACTIONS (avoid repeating these mistakes):\n"
        for r in agent.last_rejections:
            state_json += f"- {r}\n"
    agent.last_rejections = []

    # Ask LLM for actions
    t_llm = time.time()
    actions = await call_llm(client, agent.model, state_json)
    llm_ms = int((time.time() - t_llm) * 1000)
    if llm_ms > 30000:
        log.warning("%s LLM call took %dms", agent.model.name, llm_ms)

    if not actions:
        log.debug("%s (%s): no actions returned", agent.model.name, agent.player_id)
        return

    # Normalize common action_type aliases LLMs hallucinate
    ACTION_ALIASES = {
        "SCAN": "SCAN_SECTOR",
        "BUILD": "BUILD_STRUCTURE",
        "ATTACK": "ATTACK_STRUCTURE",
        "REMOVE": "REMOVE_STRUCTURE",
        "TRANSFER": "TRANSFER_RESOURCE",
        "POLICY": "SET_POLICY",
    }

    # Submit each action
    accepted = []
    rejected = []
    for action in actions[:5]:  # Max 5 per heartbeat
        action_type = ACTION_ALIASES.get(action.get("action_type", ""), action.get("action_type", ""))
        payload = action.get("payload", {})
        result = await hc_post(client, f"/games/{game_id}/actions", {
            "player_id": agent.player_id,
            "action_type": action_type,
            "payload": payload,
        })
        if result and result.get("accepted"):
            agent.actions_submitted += 1
            # Include key payload fields for readability
            detail = ""
            if action_type == "BUILD_STRUCTURE":
                detail = f"[{payload.get('sector_id','?')} {payload.get('structure_type','?')}]"
            elif action_type == "SCAN_SECTOR":
                detail = f"[{payload.get('sector_id','?')}]"
            accepted.append(f"{action_type}{detail}")
        else:
            agent.actions_failed += 1
            reason = result.get("reason", "?") if result else "no response"
            rejected.append(f"{action_type}({reason})")
            agent.last_rejections.append(f"{action_type}: {reason}")
            agent.all_errors.append(f"{action_type}: {reason}")

    summary = ", ".join(accepted) if accepted else "none"
    if rejected:
        summary += " | REJECTED: " + ", ".join(rejected)
    log.info("%s (%s): %s", agent.model.name, agent.player_id, summary)


# ---------------------------------------------------------------------------
# Main benchmark loop
# ---------------------------------------------------------------------------

async def run_benchmark(model_keys: list[str], turns: int, session_name: str | None = None) -> None:
    """Run the benchmark in an isolated game instance. Never touches the persistent open world."""
    log.info("=" * 60)
    log.info("HeartClaws Benchmark — Battle Royale")
    log.info("Models: %s", ", ".join(model_keys))
    log.info("Turns: %d", turns)
    log.info("=" * 60)

    async with httpx.AsyncClient() as client:
        # Check server is alive
        health = await hc_get(client, "/health")
        if not health:
            log.error("HeartClaws server not reachable at %s", HEARTCLAWS_API)
            return
        log.info("Server OK")

        # Create isolated benchmark game (does NOT touch the persistent world)
        import random
        run_id = int(time.time()) % 100000
        seed = run_id  # Same number for game_id (bm_{seed}) and session name
        if not session_name:
            session_name = f"Benchmark {run_id}"
        result = await hc_post(
            client,
            f"/games/benchmark?seed={seed}&session_id=benchmark-{run_id}&session_name={session_name}",
            {},
        )
        if not result or "game_id" not in result:
            log.error("Failed to create benchmark game: %s", result)
            return
        game_id = result["game_id"]
        log.info("Created benchmark game %s — %d sectors, session=%s", game_id, result["sector_count"], session_name)

        # Create agents
        agents: list[BenchmarkAgent] = []
        for key in model_keys:
            model = MODELS.get(key)
            if not model:
                log.warning("Unknown model: %s — skipping", key)
                continue
            api_key = SECRETS.get(model.api_key_env) or os.environ.get(model.api_key_env, "")
            if not api_key and model.provider == "anthropic":
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                log.warning("No API key for %s — skipping", model.name)
                continue
            agents.append(BenchmarkAgent(model_key=key, model=model))

        if not agents:
            log.error("No agents with valid API keys. Aborting.")
            return

        # Join all agents to the benchmark game
        log.info("Joining %d agents...", len(agents))
        join_tasks = [join_agent(client, a, game_id) for a in agents]
        join_results = await asyncio.gather(*join_tasks)
        agents = [a for a, ok in zip(agents, join_results) if ok]

        if not agents:
            log.error("No agents joined successfully. Aborting.")
            return

        log.info("%d agents in game %s. Starting benchmark...", len(agents), game_id)
        log.info("-" * 60)

        # Main loop
        for turn in range(1, turns + 1):
            t0 = time.time()

            # All agents play concurrently
            await asyncio.gather(*[play_turn(client, a, game_id) for a in agents])

            # Trigger heartbeat
            hb = await hc_post(client, f"/games/{game_id}/heartbeat", {})
            hb_num = hb.get("heartbeat", "?") if hb else "?"

            # Parse ACTION_FAILED events from heartbeat — these are validation failures
            # that happen at resolution time (not caught at submission)
            hb_events = hb.get("events", []) if hb else []
            for ev in hb_events:
                if ev.get("event_type") == "ACTION_FAILED":
                    pid = ev.get("actor_player_id")
                    details = ev.get("details", {})
                    atype = details.get("action_type", "?")
                    reason = details.get("failure_reason", "?")
                    msg = f"{atype}: {reason}"
                    for a in agents:
                        if a.player_id == pid:
                            a.actions_failed += 1
                            a.last_rejections.append(msg)
                            a.all_errors.append(f"[HB] {msg}")

            elapsed = time.time() - t0

            # Progress log every 10 turns
            if turn % 10 == 0 or turn == 1:
                lb = await hc_get(client, f"/games/{game_id}/leaderboard")
                if lb and isinstance(lb, list):
                    top = ", ".join(
                        f"{e['player_id']}={e.get('composite', 0):.1f}"
                        for e in lb[:5]
                    )
                else:
                    top = "?"
                log.info(
                    "Turn %d/%d (HB %s) — %.1fs — Top: %s",
                    turn, turns, hb_num, elapsed, top,
                )

        # Final results
        log.info("=" * 60)
        log.info("BENCHMARK COMPLETE — %d turns in game %s", turns, game_id)
        log.info("=" * 60)

        lb = await hc_get(client, f"/games/{game_id}/leaderboard")
        stats = await hc_get(client, f"/games/{game_id}/stats")

        if lb and isinstance(lb, list):
            log.info("\nFinal Leaderboard:")
            log.info("  %-4s %-20s %-5s  %5s %5s %5s %5s %5s %5s %5s %5s",
                     "Rank", "Model", "ID", "Score", "Terr", "Econ", "Mil", "Infl", "Effic", "Trade", "Expan")
            log.info("  " + "-" * 90)
            for i, entry in enumerate(lb):
                pid = entry.get("player_id", "?")
                model_name = "?"
                for a in agents:
                    if a.player_id == pid:
                        model_name = a.model.name
                        break
                log.info(
                    "  #%-3d %-20s %-5s  %5.1f %5d %5.1f %5d %5d %5.1f %5d %5.1f",
                    i + 1, model_name, pid,
                    entry.get("composite", 0), entry.get("territory", 0),
                    entry.get("economy", 0), entry.get("military", 0), entry.get("influence", 0),
                    entry.get("efficiency", 0), entry.get("trade", 0), entry.get("expansion", 0),
                )

        log.info("\nAgent Stats:")
        for a in agents:
            log.info(
                "  %s (%s): %d actions submitted, %d failed, %d errors",
                a.model.name, a.player_id, a.actions_submitted, a.actions_failed, a.errors,
            )

        if stats:
            log.info(
                "\nGame %s: HB=%d, %d players, %d structures",
                game_id, stats["heartbeat"], stats["alive_players"],
                stats["total_structures"],
            )

        # Error summary — deduplicated with counts
        all_errors = []
        for a in agents:
            for e in a.all_errors:
                all_errors.append(f"{a.model.name}: {e}")
        if all_errors:
            from collections import Counter
            error_counts = Counter(all_errors)
            log.info("\nErrors (%d total):", len(all_errors))
            for err, count in error_counts.most_common():
                log.info("  [%dx] %s", count, err)
        else:
            log.info("\nNo errors.")


def _resolve_model(key: str) -> tuple[str, ModelConfig]:
    """Resolve a model key or OpenRouter model ID (e.g. 'meta-llama/llama-4-scout:free')."""
    if key in MODELS:
        return key, MODELS[key]
    # Treat as an OpenRouter model ID (provider/model-name format)
    if "/" in key:
        name = key.split("/")[-1].split(":")[0].replace("-", " ").title()
        model = ModelConfig(name=name, model_id=key, provider="openrouter", api_key_env="OPENROUTER_API_KEY")
        return key, model
    return key, None


def main():
    parser = argparse.ArgumentParser(description="HeartClaws AI Benchmark")
    parser.add_argument("--turns", type=int, default=100, help="Number of heartbeats to play")
    parser.add_argument(
        "--models", type=str, default="codestral,mistral-small",
        help=f"Comma-separated model keys or OpenRouter IDs (e.g. meta-llama/llama-4-scout:free). "
             f"Presets: {','.join(MODELS.keys())}",
    )
    parser.add_argument("--name", type=str, default=None, help="Session name shown in Ranking of Claws (e.g. 'Night Run 6 models')")
    args = parser.parse_args()

    model_keys = [m.strip() for m in args.models.split(",") if m.strip()]
    # Register any ad-hoc OpenRouter models
    for key in model_keys:
        if key not in MODELS:
            resolved_key, model = _resolve_model(key)
            if model:
                MODELS[resolved_key] = model
                log.info("Ad-hoc model registered: %s → %s", resolved_key, model.model_id)
            else:
                log.warning("Unknown model key: %s", key)
    asyncio.run(run_benchmark(model_keys, args.turns, session_name=args.name))


if __name__ == "__main__":
    main()
