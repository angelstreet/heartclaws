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
import math
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
    timeout_s: int = 30 # Per-model LLM call timeout in seconds


MODELS: dict[str, ModelConfig] = {
    # --- Paid / fast models (default benchmark lineup) ---
    "minimax-m25hs": ModelConfig(
        name="MiniMax M2.5 HS",
        model_id="MiniMax-M2.5-highspeed",
        provider="minimax",
        api_key_env="MINIMAX_API_KEY",
        timeout_s=45,
    ),
    "minimax-01": ModelConfig(
        name="MiniMax 01",
        model_id="minimax-01",
        provider="minimax",
        api_key_env="MINIMAX_API_KEY",
        timeout_s=45,
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
        model_id="grok-code-fast-1",  # uses XAI_API_KEY (direct) or falls back to OpenRouter
        provider="xai",
        api_key_env="XAI_API_KEY",
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

SYSTEM_PROMPT = """You are an AI agent playing HeartClaws, a competitive hex-grid strategy game against other AI agents. Each turn you will call the submit_actions tool with your chosen actions. No explanation needed — just call the tool.

═══ ESCALATING ACTION COSTS ═══
Each action you submit in a single turn costs MORE energy than the last:
  Action 1: base cost × 1.0  ← always submit your most important action first
  Action 2: base cost × 1.5
  Action 3: base cost × 2.0  ← rarely worth it
  Action 4: base cost × 3.0
  Action 5: base cost × 5.0

IMPORTANT: "affordable_structures" shows what you can build as your FIRST action only (at 1.0×).
The system automatically rejects any action you cannot afford — you will NOT be penalized for
trying, but wasted turns slow you down. Only include actions you are confident are affordable.
STRATEGY: One good action beats three cheap ones. Thinking models that choose once win over
fast models that spam. Submit 1 action unless your energy surplus is very large.

═══ ENERGY SYSTEM ═══
energy.available = reserve + income - upkeep (capped by throughput). This is your REAL budget.
If upkeep > income + reserve: structures deactivate (highest upkeep first). Build REACTORs early.
affordable_structures = structures you can build RIGHT NOW (checks metal + data + biomass + energy at 1.0x multiplier).
If you submit action 2, its real cost is affordable_structures_cost × 1.5 — plan accordingly.

═══ ACTIONS ═══
- BUILD_STRUCTURE: {"sector_id": "H_x_y", "structure_type": "TYPE"}
- ATTACK_STRUCTURE: {"target_structure_id": "st_xxx"}
- SET_POLICY: {"target_player_id": "pN", "stance": "ALLY|NEUTRAL|HOSTILE"}
- TRANSFER_RESOURCE: {"target_player_id": "pN", "resource_type": "METAL|DATA|BIOMASS", "amount": N}
- SCAN_SECTOR: {"sector_id": "H_x_y"}
- REMOVE_STRUCTURE: {"structure_id": "st_xxx"}

═══ STRUCTURES (base energy + metal + data + biomass) ═══
- EXTRACTOR:        4E + 6M       — requires METAL node → +3 metal/turn
- DATA_HARVESTER:   4E + 4M + 2D  — requires DATA node → +3 data/turn
- BIO_CULTIVATOR:   4E + 4M + 3B  — requires BIOMASS node → +3 biomass/turn
- TOWER:            4E + 5M       — no node required → claims adjacent uncontrolled sector
- REACTOR:          8E + 10M      — no node required → +8 energy income/turn (KEY for scaling)
- ATTACK_NODE:      6E + 9M + 1D  — enables attacking enemy structures
- SHIELD_GENERATOR: 6E + 8M + 5B
- TRADE_HUB:        7E + 10M + 3D
- OUTPOST:          10E + 15M + 2D

═══ BUILD RULES ═══
- sector_details shows ALL sectors you can build in (controlled + adjacent uncontrolled) with resource_nodes
- EXTRACTOR → sector must have resource_nodes containing type="METAL"
- DATA_HARVESTER → type="DATA" node required
- BIO_CULTIVATOR → type="BIOMASS" node required
- TOWER → can_build_tower=true in sector (adjacent, uncontrolled)
- One structure per sector maximum

═══ HOW TO PLAY EACH TURN ═══
STEP 0: Check can_act. If false (energy=0) → respond [] and wait.
STEP 1: Check action_cost_multiplier. If >2.0, submitting more actions this turn is very expensive — stop unless you have large energy surplus.
STEP 2: Choose your SINGLE best action first (lowest opportunity cost at 1.0x).
  Priority: REACTOR (if affordable, always worth it) > resource node structures > TOWER expansion > SCAN
STEP 3: Only add a 2nd action if energy.available - first_action_cost × 1.5 still covers it.
STEP 4: NEVER submit a 3rd+ action unless energy.available is very large.

═══ HARD RULES — NEVER VIOLATE ═══
- ONLY build structures listed in affordable_structures — anything else is always rejected
- NEVER build a structure type already present in that sector
- NEVER scan sectors already visible in sector_details
- NEVER build military (ATTACK_NODE) while metal income < 9/turn
- NEVER submit action 2+ when action_cost_multiplier > 2.0 unless energy.available > 60

Use the submit_actions tool to submit your chosen actions."""

# ---------------------------------------------------------------------------
# Universal tool definition — same schema for all models, two wire formats
# ---------------------------------------------------------------------------

# The actions array schema — identical intent, two serialisations
_ACTIONS_SCHEMA = {
    "type": "array",
    "description": "0-5 actions to take this heartbeat. Empty array to wait.",
    "items": {
        "type": "object",
        "properties": {
            "action_type": {
                "type": "string",
                "enum": [
                    "BUILD_STRUCTURE", "SCAN_SECTOR", "ATTACK_STRUCTURE",
                    "SET_POLICY", "TRANSFER_RESOURCE", "REMOVE_STRUCTURE",
                ],
            },
            "payload": {"type": "object"},
        },
        "required": ["action_type", "payload"],
    },
    "maxItems": 5,
}

# OpenAI-compatible format (mistral, openai, openrouter, xai)
TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "submit_actions",
        "description": "Submit your actions for this heartbeat.",
        "parameters": {
            "type": "object",
            "properties": {"actions": _ACTIONS_SCHEMA},
            "required": ["actions"],
        },
    },
}
TOOL_CHOICE_OPENAI = {"type": "function", "function": {"name": "submit_actions"}}

# Anthropic-compatible format (anthropic, minimax)
TOOL_ANTHROPIC = {
    "name": "submit_actions",
    "description": "Submit your actions for this heartbeat.",
    "input_schema": {
        "type": "object",
        "properties": {"actions": _ACTIONS_SCHEMA},
        "required": ["actions"],
    },
}
TOOL_CHOICE_ANTHROPIC = {"type": "tool", "name": "submit_actions"}


def _extract_openai_tool(data: dict) -> list[dict] | None:
    """Extract actions from an OpenAI-format tool_calls response."""
    msg = data.get("choices", [{}])[0].get("message", {})
    for tc in msg.get("tool_calls", []):
        if tc.get("function", {}).get("name") == "submit_actions":
            try:
                args = json.loads(tc["function"]["arguments"])
                return args.get("actions", [])
            except (json.JSONDecodeError, KeyError):
                pass
    return None


def _extract_anthropic_tool(data: dict) -> list[dict] | None:
    """Extract actions from an Anthropic-format tool_use response."""
    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "submit_actions":
            return block.get("input", {}).get("actions", [])
    return None


def _parse_text_fallback(text: str, model_name: str) -> list[dict]:
    """Last-resort: extract a JSON array from free-form text."""
    import re
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for match in reversed(list(re.finditer(r'\[.*?\]', text, flags=re.DOTALL))):
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if match:
        try:
            return [json.loads(match.group())]
        except json.JSONDecodeError:
            pass
    log.warning("%s tool call missing — text fallback also failed: %s", model_name, text[:120])
    return []


async def call_llm(client: httpx.AsyncClient, model: ModelConfig, state_json: str) -> list[dict]:
    """Call an LLM using the universal submit_actions tool and return its chosen actions."""
    api_key = SECRETS.get(model.api_key_env) or os.environ.get(model.api_key_env, "")
    if not api_key:
        if model.provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key and model.provider != "xai":
            log.warning("No API key for %s (%s) — skipping", model.name, model.api_key_env)
            return []

    user_msg = f"Game state:\n\n{state_json}\n\nCall submit_actions with your chosen actions."

    try:
        # ── Anthropic-compatible providers (anthropic, minimax) ──────────────
        if model.provider in ("anthropic", "minimax"):
            if model.provider == "anthropic":
                url = "https://api.anthropic.com/v1/messages"
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            else:
                url = "https://api.minimax.io/anthropic/v1/messages"
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

            resp = await client.post(url, headers=headers, json={
                "model": model.model_id,
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "tools": [TOOL_ANTHROPIC],
                "tool_choice": TOOL_CHOICE_ANTHROPIC,
                "messages": [{"role": "user", "content": user_msg}],
            }, timeout=model.timeout_s)
            data = resp.json()
            if resp.status_code >= 400:
                log.warning("%s API error %d: %s", model.provider, resp.status_code, data.get("error", data))
                return []
            actions = _extract_anthropic_tool(data)
            if actions is None:
                text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "")
                return _parse_text_fallback(text, model.name)
            return actions

        # ── OpenAI-compatible providers (openai, mistral, openrouter, xai) ──
        if model.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        elif model.provider == "mistral":
            base = "https://codestral.mistral.ai" if "codestral" in model.model_id else "https://api.mistral.ai"
            url = f"{base}/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        elif model.provider == "openrouter":
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "HTTP-Referer": "https://pikaai.me"}
        elif model.provider == "xai":
            if not api_key:
                log.warning("No XAI_API_KEY — falling back to OpenRouter for %s", model.name)
                url = "https://openrouter.ai/api/v1/chat/completions"
                or_key = SECRETS.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
                headers = {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json", "HTTP-Referer": "https://pikaai.me"}
                model = ModelConfig(model.name, f"x-ai/{model.model_id}", "openrouter", model.api_key_env, model.timeout_s)
            else:
                url = "https://api.x.ai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        elif model.provider == "gemini":
            # Gemini native API doesn't use OpenAI tool format — text fallback only
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model.model_id}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": user_msg}]}],
                    "generationConfig": {"maxOutputTokens": 1024},
                },
                timeout=model.timeout_s,
            )
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return _parse_text_fallback(text, model.name)
        else:
            log.warning("Unknown provider: %s", model.provider)
            return []

        resp = await client.post(url, headers=headers, json={
            "model": model.model_id,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "tools": [TOOL_OPENAI],
            "tool_choice": TOOL_CHOICE_OPENAI,
        }, timeout=model.timeout_s)
        data = resp.json()
        if resp.status_code >= 400:
            log.warning("%s API error %d: %s", model.provider, resp.status_code, data.get("error", data))
            return []
        actions = _extract_openai_tool(data)
        if actions is None:
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            return _parse_text_fallback(text, model.name)
        return actions

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
    # Use "available" (= reserve + income - upkeep, capped) — not raw reserve which ignores upkeep
    energy_available = energy.get("available", player.get("energy_reserve", 0))
    # COSTS: (metal, data, biomass, energy) — must check ALL four
    COSTS = {
        "EXTRACTOR":        (6, 0, 0, 4),
        "DATA_HARVESTER":   (4, 2, 0, 4),
        "BIO_CULTIVATOR":   (4, 0, 3, 4),
        "TOWER":            (5, 0, 0, 4),
        "REACTOR":          (10, 0, 0, 8),
        "ATTACK_NODE":      (9, 1, 0, 6),
        "SHIELD_GENERATOR": (8, 0, 5, 6),
        "TRADE_HUB":        (10, 3, 0, 7),
        "OUTPOST":          (15, 2, 0, 10),
    }
    affordable = [
        s for s, (m, d, b, e) in COSTS.items()
        if metal >= m and data >= d and biomass >= b and energy_available >= e
    ]

    # Trim state to reduce tokens — send only what matters
    compact_state = {
        "player": player,
        "resources": {"metal": metal, "data": data, "biomass": biomass},
        "energy": {"available": energy_available, "net_per_turn": net_energy, "income": income, "upkeep": upkeep},
        "affordable_structures": affordable,  # what you can build as action #1 (at 1.0x cost)
        "can_act": energy_available >= 1,     # False = submit [] and wait for energy to regenerate
        "opponents": [                         # other players visible in this game
            {"player_id": st["owner_player_id"], "sector_id": st["sector_id"]}
            for st in (state.get("structures") or {}).values()
            if st["owner_player_id"] != agent.player_id and st["structure_type"] == "SANCTUARY_CORE"
        ],
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

    # Ask LLM for actions — per-model timeout: slow/thinking models get more time
    t_llm = time.time()
    try:
        actions = await asyncio.wait_for(call_llm(client, agent.model, state_json), timeout=float(agent.model.timeout_s))
    except asyncio.TimeoutError:
        llm_ms = int((time.time() - t_llm) * 1000)
        log.warning("%s timed out after %dms — skipping turn", agent.model.name, llm_ms)
        agent.all_errors.append(f"[TIMEOUT] turn skipped after {llm_ms}ms")
        return
    llm_ms = int((time.time() - t_llm) * 1000)

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

    # Submit each action — pre-filter enforces resource + escalating energy limits so
    # nothing unaffordable ever reaches the server (0 server rejections guaranteed).
    ACTION_ENERGY = {"SCAN_SECTOR": 2, "SET_POLICY": 1, "TRANSFER_RESOURCE": 1, "ATTACK_STRUCTURE": 3}
    # Escalating multipliers: Nth action this heartbeat costs base × MULTIPLIERS[N-1]
    MULTIPLIERS = [1.0, 1.5, 2.0, 3.0, 5.0]
    spent_m, spent_d, spent_b, spent_e = 0, 0, 0, 0  # running totals this turn
    action_index = 0  # how many actions accepted so far this turn

    accepted = []
    rejected = []
    for action in actions[:5]:  # Max 5 per heartbeat
        action_type = ACTION_ALIASES.get(action.get("action_type", ""), action.get("action_type", ""))
        payload = action.get("payload", {})
        multiplier = MULTIPLIERS[min(action_index, len(MULTIPLIERS) - 1)]

        # Client-side pre-filter: enforce material, scaled energy, and resource node requirements
        if action_type == "BUILD_STRUCTURE":
            stype = payload.get("structure_type", "")
            sector_id = payload.get("sector_id", "")
            if stype:
                # Block if sector already has a structure of this type (one per sector per type rule)
                existing_in_sector = [
                    s.get("structure_type") for s in state.get("structures", {}).values()
                    if s.get("sector_id") == sector_id
                ]
                if stype in existing_in_sector:
                    msg = f"BUILD_STRUCTURE {stype}: sector {sector_id} already has one"
                    agent.last_rejections.append(msg)
                    agent.all_errors.append(f"[PRE] {msg}")
                    continue

                # Resource node requirement: EXTRACTOR/DATA_HARVESTER/BIO_CULTIVATOR need a matching node
                REQUIRED_NODE = {"EXTRACTOR": "METAL", "DATA_HARVESTER": "DATA", "BIO_CULTIVATOR": "BIOMASS"}
                required = REQUIRED_NODE.get(stype)
                if required:
                    sector_info = state.get("sector_details", {}).get(sector_id, {})
                    nodes = [n.get("type") for n in sector_info.get("resource_nodes", [])]
                    if required not in nodes:
                        msg = f"BUILD_STRUCTURE {stype}: sector {sector_id} has no {required} node"
                        agent.last_rejections.append(msg)
                        agent.all_errors.append(f"[PRE] {msg}")
                        continue
                m, d, b, e = next(
                    ((mv, dv, bv, ev) for s, (mv, dv, bv, ev) in COSTS.items() if s == stype),
                    (999, 999, 999, 999),
                )
                scaled_e = math.ceil(e * multiplier)
                rem_m, rem_d, rem_b, rem_e = metal - spent_m, data - spent_d, biomass - spent_b, energy_available - spent_e
                if rem_m < m or rem_d < d or rem_b < b or rem_e < scaled_e:
                    msg = f"BUILD_STRUCTURE {stype}: not affordable (need {m}M/{scaled_e}E have {rem_m}M/{rem_e}E)"
                    agent.last_rejections.append(msg)
                    agent.all_errors.append(f"[PRE] {msg}")
                    continue
                spent_m += m; spent_d += d; spent_b += b; spent_e += scaled_e

        # Block SCAN_SECTOR on sectors already visible in sector_details
        elif action_type == "SCAN_SECTOR":
            sid = payload.get("sector_id", "")
            if sid in state.get("sector_details", {}):
                msg = f"SCAN_SECTOR {sid}: already visible, skipping"
                agent.last_rejections.append(msg)
                agent.all_errors.append(f"[PRE] {msg}")
                continue

        # Block other actions when scaled energy would be exhausted
        if action_type in ACTION_ENERGY:
            cost_e = math.ceil(ACTION_ENERGY[action_type] * multiplier)
            if energy_available - spent_e < cost_e:
                continue  # silently skip
            spent_e += cost_e
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
            action_index += 1
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
            # xai falls back to OpenRouter if no direct key — allow it through
            if not api_key and model.provider != "xai":
                log.warning("No API key for %s — skipping", model.name)
                continue
            if not api_key and model.provider == "xai":
                or_key = SECRETS.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
                if not or_key:
                    log.warning("No XAI_API_KEY or OPENROUTER_API_KEY for %s — skipping", model.name)
                    continue
                log.info("%s: no XAI_API_KEY, will use OpenRouter fallback", model.name)
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
