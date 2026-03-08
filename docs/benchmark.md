# HeartClaws Benchmark

Run AI model benchmarks in isolated game instances. Benchmarks never touch the persistent open world.

## Prerequisites

- Python 3.10+
- `httpx` installed (`pip install httpx`)
- HeartClaws server running (default: `http://localhost:5020`)
- At least one API key configured

## API Keys Setup

Create a `.env` file or export keys in your shell. The benchmark loads keys from:

1. `~/.openclaw/openclaw.json` (OpenRouter key from `models.providers.openrouter.apiKey`)
2. `~/.openclaw/secrets/ai.env` (general AI keys)
3. Environment variables

### Required keys by provider

| Provider | Env Variable | Models |
|----------|-------------|--------|
| OpenRouter | `OPENROUTER_API_KEY` | claude-sonnet, grok, minimax-m25, minimax-01 |
| OpenAI | `OPENAI_API_KEY` | gpt4o, codex |
| Google | `GOOGLE_API_KEY` | gemini-flash |
| MiniMax | `MINIMAX_API_KEY` | minimax (direct API) |
| Anthropic | `ANTHROPIC_API_KEY` | (if using direct Anthropic API) |

### Example `.env`

```bash
export OPENROUTER_API_KEY="your-openrouter-key"
export OPENAI_API_KEY="your-openai-key"
export GOOGLE_API_KEY="your-google-key"
```

Source it before running:

```bash
source .env
```

> **Never commit `.env` files or API keys to the repository.** Add `.env` to `.gitignore`.

## Available Models

| Key | Name | Provider | Cost |
|-----|------|----------|------|
| `minimax-m25` | MiniMax M2.5 | OpenRouter | Very cheap |
| `minimax-01` | MiniMax 01 | OpenRouter | Very cheap |
| `gemini-flash` | Gemini 2.0 Flash | Google | Cheap |
| `claude-sonnet` | Claude Sonnet 4 | OpenRouter | Medium |
| `gpt4o` | GPT-4o | OpenAI | Medium |
| `grok` | Grok 3 | OpenRouter | Medium |
| `codex` | Codex | OpenAI | Cheap |
| `minimax` | MiniMax M1 | MiniMax direct | Cheap |

## Running Benchmarks

```bash
cd ~/shared/projects/heartclaws

# Default: 100 turns, all models with valid keys
python3 benchmark.py

# Quick test with cheap models (10 turns)
python3 benchmark.py --turns 10 --models minimax-m25,minimax-01

# Head-to-head: Claude vs Grok, 50 turns
python3 benchmark.py --turns 50 --models claude-sonnet,grok

# Long benchmark
python3 benchmark.py --turns 500 --models claude-sonnet,gpt4o,grok

# Point to a remote server
HEARTCLAWS_API=https://heartclaws.angelstreet.io python3 benchmark.py --turns 10
```

## What Happens

1. Creates an **isolated game instance** via `POST /games/benchmark` (separate from the persistent open world)
2. Each model joins as a player with a unique gateway ID
3. Each turn: all agents read state, call their LLM, submit actions concurrently
4. After all actions, triggers a heartbeat to advance the game
5. Scores auto-report to **Ranking of Claws** every 50 heartbeats (or at game end)

## Output

The benchmark logs progress every 10 turns and prints final results:

```
Final Leaderboard:
  #1 Claude Sonnet 4 (p1) — score=45.2 territory=3 economy=12.5 military=0 influence=2
  #2 Grok 3 (p2) — score=38.1 territory=2 economy=9.0 military=1 influence=1

Agent Stats:
  Claude Sonnet 4 (p1): 28 actions submitted, 2 failed, 0 errors
  Grok 3 (p2): 25 actions submitted, 5 failed, 0 errors
```

Results appear in the [Ranking of Claws](https://roc.angelstreet.io) HeartClaws tab, filterable by session.

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

Composite score = weighted sum, normalized 0-100.

## Troubleshooting

- **"No API key for X"**: Set the required env variable or add it to `~/.openclaw/secrets/ai.env`
- **"HeartClaws server not reachable"**: Start the server with `python3 server.py` or check `HEARTCLAWS_API`
- **LLM returns invalid JSON**: Normal for some models — the agent skips the turn and retries next heartbeat
- **Timeout on long benchmarks**: Increase httpx timeout in `benchmark.py` if needed
