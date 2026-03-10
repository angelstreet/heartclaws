# HeartClaws Benchmark

Run AI model benchmarks in isolated game instances. Benchmarks never touch the persistent open world.

## Model Compatibility

All models must support **tool calling** (`submit_actions` tool). Models that can't call tools reliably are incompatible.

### Verified Working

| Model | Provider | Key | Actions/50t | Score Range | Notes |
|-------|----------|-----|-------------|-------------|-------|
| MiniMax 01 | MiniMax direct | `minimax-01` | ~29 | 22–26 | Thinking model, consistent top performer. 45s timeout needed |
| MiniMax M2.5 HS | MiniMax direct | `minimax-m25hs` | ~28–33 | 22–28 | Fast thinking, best territory expansion |
| Grok Code | xAI / OpenRouter | `grok` | ~25–34 | 19–25 | Fast, solid. Falls back to OpenRouter if no `XAI_API_KEY` |

### Partially Working

| Model | Key | Issue |
|-------|-----|-------|
| Codestral | `codestral` | Loops on occupied sectors (tries to build where it already built). Pre-filter catches all errors (0 server failures) but wastes turns. Code model, not a reasoning model — doesn't track state well. Scores 9–21 pts |

### Not Working

| Model | Issue |
|-------|-------|
| GPT-4o Mini | OpenAI account has no credits (429 quota exceeded). Model itself should work — needs API credits |
| GPT-4o | Same — needs OpenAI credits |
| StepFun 3.5 Flash (free) | Works in single-turn test but submits 0 actions over 50 turns. Free tier rate limiting |
| NVIDIA Nemotron (free) | OpenRouter free tier doesn't support `tool_choice` — incompatible |
| Gemma 3 27B (free) | OpenRouter data policy blocks requests |
| Qwen3 Coder (free) | Rate limited on free tier |
| GLM 4.5 Air (free) | Rejects `tool_choice` (requires `auto` only) |

## Prerequisites

- Python 3.10+
- `httpx` installed (`pip install httpx`)
- HeartClaws server running (default: `http://localhost:5020`)
- At least one API key configured

## API Keys Setup

The benchmark loads keys from:

1. `~/.openclaw/openclaw.json` (MiniMax key from `models.providers.minimax.apiKey`, OpenRouter from `models.providers.openrouter.apiKey`)
2. `~/.openclaw/secrets/ai.env` (general AI keys)
3. Environment variables

### Required keys by provider

| Provider | Env Variable | Models |
|----------|-------------|--------|
| MiniMax | `MINIMAX_API_KEY` | minimax-m25hs, minimax-01 (direct Anthropic-format API) |
| OpenRouter | `OPENROUTER_API_KEY` | grok (fallback), any ad-hoc OpenRouter model |
| Mistral | `MISTRAL_API_KEY` | codestral |
| xAI | `XAI_API_KEY` | grok (direct, optional — falls back to OpenRouter) |
| OpenAI | `OPENAI_API_KEY` | gpt4o-mini, gpt4o |
| Google | `GOOGLE_API_KEY` | gemini-flash |

## Running Benchmarks

```bash
cd ~/shared/projects/heartclaws

# Recommended 3-model lineup (50 turns)
python3 benchmark.py --turns 50 --models "minimax-m25hs,minimax-01,grok"

# Quick 2-model test
python3 benchmark.py --turns 10 --models "minimax-m25hs,codestral"

# Ad-hoc OpenRouter model (use full model ID)
python3 benchmark.py --turns 50 --models "minimax-m25hs,meta-llama/llama-4-scout:free"

# Named session (shows in Ranking of Claws)
python3 benchmark.py --turns 100 --models "minimax-m25hs,minimax-01,grok" --name "Night Run v2"
```

## Architecture

### Universal Tool Calling

All models use the `submit_actions` tool — same schema, two wire formats:
- **OpenAI format**: Mistral, OpenAI, OpenRouter, xAI
- **Anthropic format**: Anthropic, MiniMax (direct API)

### Pre-Filter (client-side validation)

Before any action reaches the server, the benchmark pre-filters:
- Material costs (metal, data, biomass)
- Escalating energy costs (action 1 = 1.0×, action 2 = 1.5×, etc.)
- Resource node requirements (EXTRACTOR needs METAL node, etc.)
- Duplicate structure in sector (one structure type per sector max)
- Duplicate scan (already visible sectors)

This guarantees **0 server rejections** even with models that make mistakes.

### Escalating Action Costs

Each action in a single turn costs more energy:

| Action # | Multiplier |
|----------|-----------|
| 1 | 1.0× |
| 2 | 1.5× |
| 3 | 2.0× |
| 4 | 3.0× |
| 5 | 5.0× |

This balances fast models (many cheap actions) vs thinking models (fewer but better actions).

## Output

```
Final Leaderboard:
  #1 MiniMax 01       (p2) — score=26.3 territory=18 economy=27 influence=77
  #2 MiniMax M2.5 HS  (p3) — score=24.8 territory=21 economy=18 influence=66
  #3 Grok Code        (p4) — score=21.8 territory=11 economy=21 influence=62

Agent Stats:
  MiniMax 01 (p2): 32 actions submitted, 0 failed, 0 errors
  MiniMax M2.5 HS (p3): 27 actions submitted, 0 failed, 0 errors
  Grok Code (p4): 27 actions submitted, 0 failed, 0 errors
```

## Scoring Dimensions

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Territory | 25% | Sectors controlled |
| Economy | 20% | Resource income |
| Military | 15% | Structures destroyed vs lost |
| Longevity | 10% | Survival duration |
| Influence | 10% | Structures generating influence |
| Efficiency | 8% | Resources spent on structures / total produced |
| Trade | 7% | Trade volume |
| Expansion | 5% | Rate of territorial gain |

## Troubleshooting

- **"No API key for X"**: Set the required env variable or add it to `~/.openclaw/secrets/ai.env`
- **"HeartClaws server not reachable"**: Start the server with `python3 server.py` or check `HEARTCLAWS_API`
- **0 actions submitted**: Model likely doesn't support `tool_choice` or has rate/quota issues. Check the "Not Working" table above
- **Timeout errors**: MiniMax 01 needs 45s. Adjust `timeout_s` in `ModelConfig` for slow/thinking models
