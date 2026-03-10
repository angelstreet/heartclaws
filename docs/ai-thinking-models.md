# AI Thinking Models in HeartClaws

## Summary

HeartClaws is a turn-based hex-grid strategy game designed specifically to benchmark AI agents.
Each heartbeat, agents receive their game state as JSON and must return a JSON array of actions.
The game evaluates decision quality — territory, economy, military, influence — not speed.

The benchmark has been tested with two classes of models:

| Class | Example | Latency | Behaviour |
|---|---|---|---|
| Fast / code models | Codestral | ~1.5s | Returns clean JSON immediately |
| Thinking models | MiniMax M2.5 HS | 10–30s | Reasons internally, then outputs |

---

## The Thinking Model Problem

### What is a thinking model?

Thinking models (also called reasoning models) generate an internal chain-of-thought before
producing their final answer. Examples: MiniMax M2.5, DeepSeek R1, o1, Claude with extended
thinking. The reasoning is not supposed to appear in the output — only the final answer should.

### Why it matters for HeartClaws

HeartClaws requires a strict JSON array as output. Any text outside of valid JSON causes
the agent's turn to be lost silently. Thinking models fail in two distinct ways:

---

## Bug 1 — Thinking Leaks Into Response

**Symptom:**
```
MiniMax M2.5 HS returned unparseable response:
  "Looking at my state:
   - Energy: 15 available, but net_per_turn = 0
   - Resources: Metal 3, Data 132..."
```

**What happens:**
The model outputs its reasoning chain as plain text instead of (or before) the JSON array.
The response contains no valid JSON — the turn is lost entirely.

**Frequency:** ~10% of MiniMax M2.5 HS turns in testing (3/30 turns)

**Root cause:**
MiniMax's Anthropic-compatible endpoint sometimes returns the thinking block as a `text` content
block instead of a separate `thinking` block. When the model "reasons itself into confusion"
(contradictory constraints, empty affordable list, etc.) it outputs the reasoning as its final
answer rather than falling back to `[]`.

**Current mitigation:**
- Strip `<think>...</think>` tags
- Scan for the LAST valid JSON array in the full response (reasoning first, answer last)
- Still fails when the model produces no JSON array at all

**Not yet fixed:** When the model outputs only reasoning with no JSON, the turn is skipped.

---

## Bug 2 — Cannot Disable Thinking

**Symptom:**
MiniMax M2.5 **HighSpeed** takes 10–30s per turn vs Codestral's 1.5s.
The "HighSpeed" suffix refers to generation speed, not non-thinking mode.

**Root cause:**
Thinking is a core architectural feature of MiniMax M2.5. There is no API parameter to disable
it. Confirmed by MiniMax team (GitHub issue #68): *"Currently, turning off thinking is not
supported."*

OpenRouter does not document thinking control parameters for MiniMax.
Attempts to pass `budget_tokens` via the Anthropic-compatible endpoint are not supported.

**Impact on game fairness:**
- Codestral: ~1.5s/turn → high turn participation
- MiniMax M2.5 HS: 10–30s/turn → may miss turns if timeout exceeded

**Current mitigation:**
- Per-model timeout: MiniMax gets 45s (others get 30s), configurable via `ModelConfig.timeout_s`
- Game design uses escalating action costs (see below) so speed ≠ advantage

---

## Game Design Decision: Escalating Action Costs

To ensure thinking models can compete fairly against fast models, HeartClaws uses an
**escalating energy cost** mechanic within each heartbeat:

| Action # this turn | Multiplier |
|---|---|
| 1st | 1.0× base cost |
| 2nd | 1.5× base cost |
| 3rd | 2.0× base cost |
| 4th | 3.0× base cost |
| 5th | 5.0× base cost |

**Effect:** Spamming 3 cheap actions costs 4.5× base energy total. One precise action costs 1×.
A thinking model that submits one perfect action per turn competes economically with a fast model
submitting multiple actions. The economics reward decision quality, not volume.

**Result from 30-turn test (Codestral vs MiniMax M2.5 HS):**
```
#1  Codestral       18.5 pts — 1 territory,  12 econ, 49 influence — 24 actions
#2  MiniMax M2.5 HS 16.7 pts — 9 territory,  15 econ, 38 influence — 18 actions
```
MiniMax won on territory and economy despite submitting fewer actions and losing 3 turns to
unparseable responses. Competitive.

---

## Validation Architecture

All actions are validated at **submission time** (not just at heartbeat resolution).
This means agents receive immediate rejection with a clear reason instead of silent failure.

**Submit-time validation catches:**
- Wrong sector / no resource node for structure type
- Insufficient metal / data / biomass
- Insufficient energy (accounting for escalating multiplier and already-queued actions)
- Invalid action type / missing player

**Client-side pre-filter (benchmark.py):**
Before even submitting to the server, the benchmark applies a pre-filter using scaled energy
costs. This guarantees 0 server-side rejections regardless of what the AI outputs.

**Result:** In 100-turn solo test and 30-turn head-to-head — 0 server rejections.

---

## Open Issues / Next Steps

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | MiniMax leaks reasoning as response (~10% turns lost) | Medium | Partial mitigation |
| 2 | MiniMax thinking cannot be disabled via API | Low | Accepted (escalating costs compensate) |
| 3 | MiniMax occasionally reasons with 0 income/upkeep confusion | Medium | Open |
| 4 | Codestral builds REACTORs in same sector repeatedly (no variety) | Low | Prompt tuning needed |
| 5 | No multi-player competitive test yet (only 2-player) | Low | Needs more models |

### Recommended next steps

1. **Improve MiniMax response recovery** — when the model outputs no JSON, inject a follow-up
   message asking it to output only the JSON array and retry once before skipping the turn.

2. **Test more models** — run Mistral Small, GPT-4o Mini, and Claude Haiku in the same game
   to build a proper benchmark leaderboard.

3. **Fix Codestral's REACTOR loop** — the model builds REACTORs in the same sector (sector
   already has one, the build fails or is blocked). Prompt needs: "NEVER build a structure type
   that already exists in a sector."

4. **Add multi-sector awareness** — agents currently focus on one sanctuary sector. Add
   instructions to use TOWERs to expand and build resource structures in new sectors.
