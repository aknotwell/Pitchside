# Reflex: answering the easy questions before the agent does

"Ask the match" is an Agent Framework agent that answers through the
match-data MCP server on Azure Functions. That answer is grounded, but it is
slow. A turn is one model call to choose a tool, an MCP call, and a second
model call to write the answer. The Functions Flex MCP endpoint takes
**1.8 to 26 s** per tool call when its instances are cold (measured
2026-10-25, three calls). Asking "What's the score?" on the live site took
**21 s**, and "Who is the fastest player?" took **5.3 s**.

Most fan questions are one of a dozen kinds, and the answer is already in the
match state document. Reflex is a fast path in front of the agent for those
questions.

```
            ┌─► Probe: 13 routing questions in ONE gpt-4.1-mini call ──┐
question ───┤                                                           ├─► decide ─┬─► Tier 0: code fills a template
            └─► read matchState from Cosmos (in parallel) ─────────────┘           ├─► Tier 1: one tool-free call,
                                                                                    │           numbers by slot
                                                                                    ├─► decline (betting)
                 MCP agent: starts on a pass, or after a 2.5 s head start ◄─────────┴─► pass
                 (cancelled if Reflex answers first; wins if it finishes first)
```

## The probe: a classifier made from a chat model

`src/matchlens/reflex/probe.py` turns a small chat model into a batched
classifier. Every question asks for one option letter. The probability of
each option comes from the **token logprobs** of that letter, not from the
model's own claim of confidence. It is not renormalised: probability on other
tokens, or on letters outside the top five alternatives, counts as doubt. Asking 1 question or 20 costs one round trip,
so Reflex asks everything a decision needs in a single request.

- **Checked choices.** Small models favour the first option they see. The
  routing and betting questions are asked twice in the same call, once forward
  and once reversed. It only counts when both orders pick the same option, and its
  confidence is the lower of the two.
- **Outcome-shaped fit checks.** For each capability Reflex asks: "Would a
  reply giving only *X*, and nothing else, fully answer the request?" This is
  what sends "Who is the slowest player?" and "Who scored the goal?" to the
  agent, even though they look like the fastest-player and score questions.
- **Untrusted text.** The question is collapsed to one line and JSON-quoted,
  and the system prompt says it is data. The probe only picks among options
  that code wrote, and its verdict never authorizes anything.
- **Any failure is a pass.** A timeout, a 429 or an unreadable answer sends
  the turn to the agent. It is never an error for the fan.

## Answer tiers

| Tier | What writes the answer | Capabilities |
|---|---|---|
| 0 | Code fills a localized template (en, es, ja) from the state document. No model writes a number. | score, xG, shots, possession, passing, pressing, fastest players, cards, last 10 minutes |
| 1 | One tool-free gpt-4.1-mini call writes the prose, but every club and number is a slot such as `{home}` or `{home.shots}` that code fills in. These send the turn to the agent: a digit, a number word ("three", "goalless", "no shots", 三, 同点), a club name the model wrote itself (full name or first word, any case), "the visitors" and similar, an unknown or malformed slot, a sentence that names one club with only the other club's numbers, a roster player outside the slots, or betting language. | "who has been better and why" overview |
| decline | Code | betting and odds |
| pass | The MCP agent, already running | everything else |

Both tiers read one value table, `slots(state, lang)`, and Tier 0 templates
are written in the same slot names. A capability only renders once its data exists. Top speeds must pass
15 km/h, PPDA must be computed, and the 10-minute window must be full.
Early in a match those questions go to the agent.

## The race

`POST /api/ask` starts Reflex first. The agent starts as soon as Reflex
passes, or after a 2.5 s head start if Reflex is still deciding. Whichever
gives a useful answer first wins, and the other is cancelled.

The head start is deliberate. An early version started the agent at the same
moment as Reflex. Review found that a cancelled agent still opens and closes a
full MCP session on the cold Functions instances, about 10 s of churn per
Reflex answer. With the head start, a Tier 0 answer usually never touches MCP.
A pass now costs the probe time (about 1.1 s) on top of a 5 to 21 s agent turn.

Reflex has an 8 s total budget and the SDK does not retry, so a 429 or a slow
model turns into a pass instead of a wait. Reflex never raises: a crash
inside it is logged and becomes a pass.

Every response carries `reflex` telemetry: outcome, capability, tier,
confidence, the pass reason, and the probe and total time. The player shows it
as a badge on each answer. The API also logs one `reflex outcome=...` line per
question to Application Insights.

## Measured

`scripts/reflex/bench.py` runs Reflex with the live model on a recorded match
state (`tests/data/match_state.json`). A pass is never counted as wrong, only
as lost coverage. A wrong answer means Reflex answered with a capability the
label does not accept, or answered a question labelled for the agent. The dev
set (50 questions) was used to tune the capability descriptions. The test set
(34 questions) was written after tuning and scored once.

| Set | Answerable answered | Wrong answers | Tier 0 p50 | Tier 1 p50 | Probe p50 | Labels changed on a rerun |
|---|---|---|---|---|---|---|
| dev, 50 | 31 / 37 (84%) | 2 / 33 | 1.28 s | 2.51 s | 1.16 s | 0 / 50 |
| **test, 34** | **21 / 24 (88%)** | **1 / 22** | **1.18 s** | **2.47 s** | **1.18 s** | **2 / 34** |

These numbers come after three review rounds: raw probabilities, slots for
every Tier 1 club and number, and a betting question checked in both orders.
The dev set was used for tuning. The test set was never tuned on. It was scored
after each review round, and the scores ranged from 83% to 92%. Most of the
difference is Tier 1. Each tightening of its check sends more overview
answers to the agent.

The wrong test answer was "Which team had more possession in the first
half?". Reflex gave the whole-match split, but the state has no per-half data.
The two wrong dev answers sent summary questions ("Who's on top right now?",
"Give me a quick summary") to the score template.

End to end through the local API on the shared Azure resources: Tier 0
answers arrived in **1.2 to 2.0 s** and the Tier 1 overview in **2.0 to 2.2 s**
(measured before the review fixes).
The same questions take **5 to 21 s** on the agent path.

![Ask panel with Reflex and agent answers](evidence/reflex-ask.png)

## Limits

- One transient spell of model timeouts during a bench run made 9 questions
  in a row pass. That is the designed behaviour (no retries), but it shows the
  fast path is only as steady as the shared deployment.
- Slots stop a Tier 1 answer from containing a number that isn't in the match
  state. The sentence check catches a club named with only the other club's
  numbers. It can't catch every misattribution: "{away} lead
  {home.goals}-{away.goals}" uses both sides and passes. Number words are a
  deny-list, so an unlisted phrase can slip through. Club and player names
  written in katakana are not checked. Tier 0 templates have none of these
  gaps. The agent that Tier 1 replaces has no number check at all.
- The probe is a real model call (~1.1 s), not a dedicated classifier. A
  logprob-capable model is required, so it runs on gpt-4.1-mini, not gpt-5-mini.
- The gpt-4.1-mini deployment allows 50 requests a minute. The storyteller
  fallback, the agent and Reflex all share it. Run the bench with `--rpm 30`
  (the default) or it will measure 429s.
- Reflex only knows what the state document holds. Questions about scorers,
  timelines, halves or single players go to the agent.

## Running it

```bash
uv run pytest tests/test_reflex.py                     # offline, fake model
uv run python scripts/reflex/bench.py --set dev        # live model, about 2 min per run (az login)
uv run python scripts/reflex/bench.py --set test --show
```
