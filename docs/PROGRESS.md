# Build progress log

A running log of the autonomous build, newest checkpoint last. Each
checkpoint is a commit. Items marked **YOU** need a human.

## Checkpoint 1: Azure foundation (2026-10-08)

**Subscription**: "Azure subscription 1" (`cbb2ef3b-ed42-4e42-9c51-99e5895ece87`),
tenant "Default Directory", owner `vmoose@outlook.com`. Offer `FreeTrial`, with
the spending limit **on**, so it can't bill past the $200 credit.

Done:

- **Local tools.** Installed user-level (no sudo):
  - `az` 2.91.0 via `uv tool`, on Python 3.13
  - `azd` 1.35.1, `bicep` 0.48.1 and `uv` 0.12.24 in `~/.local/bin`
  - `func` 4.15.2 via npm
- **Resource providers registered**: App, ContainerRegistry, EventHub,
  ServiceBus, EventGrid, DocumentDB, SignalRService, Web, Logic,
  CognitiveServices, Storage, KeyVault, OperationalInsights, Insights,
  ManagedIdentity, LoadTestService, AlertsManagement, Chaos,
  MachineLearningServices, PolicyInsights.
- **Resource group**: `rg-matchlens-dev` in `eastus2`.
- **Budgets** (subscription scope, emailing `vmoose@outlook.com`):
  `matchlens-100`, `matchlens-200` and `matchlens-300`. Each alerts at 80%
  actual, 100% actual and 100% forecast.
- **Region check for `eastus2`**:
  - Flex Consumption is available.
  - The model catalogue there has GPT-6 (`gpt-6-sol`/`astra`/`luna`,
    `gpt-6.1-sol`), GPT-5.4–5.6, Claude (Haiku/Sonnet/Opus 4.5–5.5, Fable 5),
    DeepSeek V4, Grok 4.x and Llama.

### Model quota on the free trial (eastus2, thousand TPM)

| Model | Quota |
|---|---|
| `gpt-5-mini` (GlobalStandard) | **500** |
| `gpt-4.1-mini` | 200 |
| `gpt-oss-120b` | 5000 |
| `grok-3`, `grok-3-mini`, `grok-4-fast-*` | 50 |
| `DeepSeek-R1` / `V3-0324` / `V3.2-Speciale` | 20 |
| `text-embedding-3-small` | 1000 |
| **Every GPT-5.4+, GPT-6 and Claude model** | **0** |

**Decision:** build on `gpt-5-mini`, with `gpt-4.1-mini` as the fallback
deployment. Claude needs a paid (non-credit) subscription. GPT-6 needs a quota
request, and free trials may not be able to file one.

### YOU (when back)

1. Try a quota request for `gpt-6-sol` and `gpt-6-astra` (GlobalStandard,
   eastus2). In the portal: Foundry → Quotas, or <https://aka.ms/oai/quotaincrease>.
   If it's blocked on the free trial, upgrade to pay-as-you-go (plan was by
   Oct 20 at the latest) and file it again.
2. Register every teammate before Oct 20, 12:00 PT.

## Checkpoint 2: core logic, tested locally (2026-10-08)

The product core runs and is tested without Azure: `uv run pytest`, 17 tests.

- **`matchlens.contracts`**: versioned Pydantic contracts for `MatchMetadata`,
  `MatchEvent`, `TrackingBatch`, `StreamMessage`, `Moment`, `EvidenceItem` and
  `OverlayCue`, plus the Web PubSub group names.
- **`matchlens.world`**: a fictional league of 8 clubs with seeded squads.
  Nothing real.
- **`matchlens.sim`**: a deterministic event engine plus 10 Hz tracking for 22
  players and the ball.
  - Event types: passes, carries, pressures, duels, tackles, fouls, cards,
    interceptions/recoveries, shots with xG and shot speed, saves, corners,
    throw-ins and subs.
  - Scripted stories: the away team presses high from 55′ to 72′.
  - Parameters are content-addressed (`params_version`).
  - Rough sanity check from six seeds:

    | Metric | Simulated | Real (approx.) |
    |---|---|---|
    | Passes per match | ~900–1,000 | ~950 |
    | Pass accuracy | 75–81% | ~80% |
    | Shots | 16–25 | ~25 |
    | Distance per outfield starter | 8.5–9.5 km | ~10–11 km |
    | Top speed | 8.6–9.5 m/s | — |

    Calibration against the licensed datasets is still to do; that's the
    `calibrate` job.
- **`matchlens.interpret`**: incremental, deterministic and idempotent.
  Duplicates are ignored, and a replay gives identical state.
  - Metrics: score, xG, shots, pass accuracy, final-third and box entries,
    possession, PPDA (whole match and rolling), turnovers, a momentum EWMA, a
    control-vs-chaos index, and distance/top speed/sprints from tracking.
  - **Template cues (hot path, no LLM):** goal, shot, card, substitution,
    top-speed player tag.
  - **Moments (warm/cold path):** goal, big chance, momentum swing, pressing
    surge, half time and full time. Each carries an evidence pack.
- **`matchlens.story`**: the storyteller as a **Microsoft Agent Framework 1.21
  workflow**: Editor → Explainer → Verifier → (retry ×1 | template fallback) →
  Director.
  - It makes one structured LLM call for all six persona × language variants
    (analyst/casual × en/es/ja).
  - The code verifier checks numbers, names, evidence keys, length and betting
    language.
  - The overlay budget allows at most one non-essential explanation per 75 s.
  - `FoundryExplainer` has a deployment fallback chain and a timeout.
  - **The checkpoint-resume test passes**: a workflow killed in the Director
    resumes on a new instance without calling the model again. That is the
    chaos-drill story.

Design change: one Event Hub, **`match-stream`**, carries events and tracking
for a match, keyed by `match_id`. A match then stays ordered on one consumer;
separate hubs could split a match across replicas.

## Checkpoint 3: deployed to Azure and verified end to end (2026-10-09)

**Live:**

| What | URL |
|---|---|
| Web | <https://wonderful-field-07003a80f.4.azurestaticapps.net> |
| API | <https://ca-api-bhdr37v3onmcs.thankfulglacier-9473c5b7.eastus2.azurecontainerapps.io> (`/api/health`, `/api/matches`) |
| Resource group | `rg-matchlens-dev` (eastus2); azd env `matchlens-dev` |

**Infrastructure** (`azd provision`, about 15 min the first time, mostly the
Container Apps environment):

- Foundry project with `gpt-5-mini` (100K TPM) and `gpt-4.1-mini` (50K TPM).
- Event Hubs Standard (`match-stream`, 4 partitions) and Service Bus Standard
  (`moments`, sessions).
- Cosmos DB serverless, Storage (no shared keys), Web PubSub (Free), ACR Basic.
- Container Apps:
  - `api` (external)
  - `interpreter`: KEDA on Event Hubs lag, managed identity
  - `storyteller`: KEDA on Service Bus depth, managed identity
  - `job-sim`: manual trigger
- Functions Flex `cue_publisher`, Static Web Apps, App Insights + Log Analytics
  (local auth off).
- Five user-assigned identities with least-privilege roles.

**Verified in the cloud:**

1. `POST /api/matches` starts an ACA Job execution, and the simulator streams
   into Event Hubs.
2. The interpreter turns that into Cosmos state, template cues, Service Bus
   moments and Web PubSub frames/state.
3. The storyteller runs the Agent Framework workflow on Foundry and writes
   verified cues to Cosmos.
4. The change feed triggers the Function, which pushes to Web PubSub and on
   to the browser.
5. A WebSocket client saw 573 frames, 120 state updates and 7 cues in 90 s;
   the browser showed 22 animated players, live stats and the agent recap
   panel "AI · verified".

**Model quality (gpt-5-mini, minimal reasoning):** 4/4 test moments passed the
code verifier first time, at 6–14 s each. The model fallback chain was
exercised: gpt-5-mini timed out on a recap, and gpt-4.1-mini delivered a
verified recap.

**Chaos drill, verified in the cloud** (06:13:55 → 06:14:11 UTC):

1. Producer → Chaos kills the storyteller replica mid-workflow (`os._exit`).
2. ACA restarts it.
3. The Service Bus session is reclaimed.
4. MAF restores from the Cosmos checkpoint and finishes in **51 ms** with six
   verified cues, **without calling the model again**.

The run shows as "resumed" in the producer console.

**Fixes found while deploying:**

- KEDA `identity` needs Container Apps API 2025-07-01.
- Reading an existing app's image needs its own module, or azd hits a
  circular dependency.
- The Web PubSub SDK double-encodes `str` payloads, so send dicts.
- A service-level azd hook didn't run, so the API URL is now injected by a
  project-level `predeploy` hook.
- Speed tags fired at kick-off.
- Shirt numbers had a gap.

## Checkpoint 4: scale evidence, MCP, Ask the match, operations, calibration (2026-10-09)

### New in the cloud

- **Match-data MCP server on Azure Functions.** Four MCP tool triggers
  (`list_matches`, `get_match_state`, `get_moments`, `get_overlays`) at
  `/runtime/webhooks/mcp`. Verified with raw JSON-RPC calls (`initialize`,
  `tools/list`, `tools/call`). `.vscode/mcp.json` wires it and the Azure MCP
  Server into GitHub Copilot agent mode.
- **Ask the match** (`POST /api/ask`, plus an "Ask" tab in the web app).
  - An Agent Framework agent (`gpt-4.1-mini`) answers through the MCP server
    using MAF's `MCPStreamableHTTPTool`.
  - Caps per answer: `max_function_calls 6`, `max_iterations 4`, 30 s, and
    600 output tokens.
  - Rate limits: 6 questions/min per client and 300/hour overall.
  - The MCP key is in **Key Vault**; the API reads it with its managed
    identity, and the postdeploy hook writes it.
- **Operations** (`infra/modules/ops.bicep`):
  - A multi-region availability test on `/api/health`, with a metric alert
    emailing `vmoose@outlook.com`.
  - A Logic App, **keep-live**, that starts a match every 10 minutes if none is
    live (managed identity → ARM). It is **disabled** unless `DEMO_MODE=true`.
- **Web PubSub upgraded to Standard_S1** (~$1.61/day). Free allows
  20K messages/day and saturated during load tests.
- **SLO queries** in `docs/kql/slo.kql`.

### Load test: 10 simultaneous matches at 6× (`scripts/loadtest.py`)

Evidence is in `docs/evidence/loadtest-20261009-0742.*`.

- **KEDA:** interpreter 1 → 4 replicas (Event Hubs lag); storyteller 0 → 4
  (Service Bus depth).
- **Server side** (Event Hubs enqueue → cue pushed to Web PubSub, 281 cues):

  | Path | p50 | p95 |
  |---|---|---|
  | Template (hot path) | **202 ms** | **364 ms** |
  | Agent | **10.2 s** | 58 s |

- **Client side** (2 watched matches): template p50 235 ms, p95 386 ms.

The agent tail is queueing while the storyteller cold-starts from zero. Demo
mode keeps one warm replica, and KEDA now scales on 1 message per replica
instead of 3.

**Bugs found by load testing (all fixed):**

1. The `az containerapp job start --env-vars` override **replaces** the job's
   env, so executions lost their config. Fix: start executions through ARM
   with the env merged.
2. Cue upserts replayed after a partition handover re-fired the change feed.
   Fix: cues are immutable and written with `create_item`; a 409 is a no-op.
3. The interpreter awaited Web PubSub publishes, and a saturated Web PubSub
   throttled the whole stream. Fix: bounded fire-and-forget ticks, coalesced
   to 1 Hz per match.
4. **An exception escaping `on_event_batch` silently stalled a partition**:
   ownership was still renewed, but nothing was consumed for 14 minutes. Fix:
   the batch handler never raises; added `on_error` and partition logs;
   consumers close on SIGTERM so ownership is released; ownership expiry
   shortened to 20 s.
5. Watchers on my home network timed out on the WebSocket handshake while
   Web PubSub showed no connection attempts. Fix: latency is now measured
   server side, from the Function, as well.

### Calibration (synthetic-data realism)

`python -m matchlens.calibrate.fit` downloads SkillCorner open data (MIT) into
`data/raw/` (gitignored), computes targets and fits the simulator by
coordinate search. It writes `src/matchlens/sim/fitted_params.json` and
`docs/REALISM.md`.

Held-out results:

| Metric | Error vs real |
|---|---|
| Passes per match | +2% |
| Pass completion | −1% |
| Sprints per 90 | +1% |
| Peak speed | −4% |
| Distance per 90 | −13% |
| Shots | +12% |
| Pass length | +13% |
| Peak-speed KS distance | 0.84 → 0.44 |

Two model changes made this possible: off-ball drift and a sprint/jog movement
split. The simulator now loads the fitted parameters by default; the live
deployment uses them.

## State when I stopped (2026-10-09 ~08:40 UTC)

Everything is committed on branch `t3code/soccer-hackathon-infrastructure`.
Nothing is pushed, because pushing publishes to the public repo; that's your
call.

**Cost posture (idle):** scale-to-zero everywhere, keep-live off. The
always-on base costs about **$3/day**:

| Item | $/day |
|---|---|
| Web PubSub Standard | 1.61 |
| Event Hubs Standard | 0.72 |
| Service Bus Standard | 0.33 |
| Log Analytics, ACR, Cosmos/Storage | small |

Model tokens are cents per match. The free-trial credit covers it.

### YOU (when back)

1. **Push and open a PR** if you're happy: `git push -u origin t3code/soccer-hackathon-infrastructure`.
2. **Upgrade to pay-as-you-go before ~Oct 20**, then request GPT-6 quota.
   Claude then becomes possible too.
3. **Judging window (Oct 27 – Nov 10):** `azd env set DEMO_MODE true && azd provision`.
   This keeps one warm replica per app, sets always-ready Functions and turns
   on the keep-live Logic App.
4. **GitHub Actions:** run `azd pipeline config` once to create the OIDC
   federation and repository variables for `.github/workflows/deploy.yml`.
   This writes to GitHub and Azure AD.
5. **Name**: "MatchLens AI" is taken by another entry. Decide whether to rename.
6. Register teammates by **Oct 20, 12:00 PT**.

### Next build steps (suggested order)

1. Foundry **evaluation gate** in CI (groundedness and faithfulness on golden
   moments).
2. **Event Grid** lifecycle events (`FullTime`, `RecapReady`) → Logic App
   recap distribution.
3. **Azure Speech** audio recaps per language.
4. Chaos Studio broker faults (disable the Event Hub or queue for 60 s) as a
   second resilience drill.
5. Fabric RTI (only if a trial capacity is available).
6. Calibration round 2: IDSSE (CC BY 4.0) for tracking-level metrics; reduce
   the shots and pass-length gaps.
7. Polish the UI; record the **< 2 min video**.

## Checkpoint 5: evaluation gate and voiced recaps (2026-10-09)

- **Evaluation gate:** `scripts/evaluate.py`, documented in
  `docs/evidence/eval-*.md`.
  - Golden moments come from calibrated seeds, run through the real storyteller
    and scored with the Azure AI Evaluation SDK (Foundry `gpt-4.1-mini` judge,
    Entra auth).
  - Results:

    | Metric | Value | Threshold |
    |---|---|---|
    | Groundedness | **4.75/5** | ≥ 4 |
    | Coherence | **3.88/5** | ≥ 3.5 |
    | Agent-written and verified | **88%** | ≥ 85% |
    | Latency p50 / p95 | 9.4 s / 19.6 s | p95 ≤ 30 s |

  - Wired into `.github/workflows/deploy.yml` after deploy.
  - The fallback recap scored 3/5 for being bland, so the recap templates now
    name the clubs and add shots.
- **Voiced recaps:** Azure AI Speech, through the Foundry resource's Speech
  endpoint with an Entra token (`aad#resourceId#token`).
  - Voices: en Ava Multilingual, es Elvira, ja Nanami.
  - The storyteller voices the fan recap per language, uploads it to the
    private Blob container `audio`, and attaches `data.audio`.
  - The API streams `/api/audio/{match}/{file}.mp3` with a strict name regex.
    The recap panel has a player.
  - Verified: the Spanish half-time recap is 64 KB of MP3, served 200
    `audio/mpeg`.

## Checkpoint 6: lifecycle events and event-driven replays (2026-10-09)

- **Event Grid topic `evgt-lifecycle-*`** (CloudEvents 1.0, local auth off).
  - The interpreter publishes `MatchLens.Match.MatchStarted`, `HalfTime` and
    `FullTime`, carrying the seed, params version, period length and score.
  - The `fulltime-to-replay` subscription delivers to the Storage queue
    `replay-requests`, using the topic's own system identity (Queue Message
    Sender role, no SAS).
- **Event-driven ACA Job `job-replay-*`**: scales from zero on queue length
  (KEDA `azure-queue` with managed identity).
  - It re-simulates the match from its seed. Determinism is tested: same
    event ids as the live match.
  - It joins every cue that aired and writes `replays/{match}.json.gz`
    (about 200 KB for a 20-minute match), plus a `runs` doc.
- **API:** `GET /api/replays` and `GET /api/replays/{id}` (gzip).
- **Web:** a "▶ Replays" group in the match picker, with 1/4/10/20× playback.
  Cues are scheduled exactly as they aired, for every persona and language,
  with audio. Verified in the browser: the Japanese fan view showed the
  agent-verified half-time recap with Japanese audio.
- **Bug found:** Event Grid writes Storage-queue messages Base64-encoded. The
  first job execution failed to parse one, and the message stayed invisible
  for 15 minutes. Fixed with a decoder (tested), failure logging and a 5-minute
  visibility timeout.

## Checkpoint 7: SLO workbook; Chaos Studio drill (partly working) (2026-10-09)

- **Azure Monitor workbook "MatchLens: live SLOs"** (`infra/workbooks/slo.json`,
  deployed by `ops.bicep`). It covers:
  - event → cue latency by path, and hot-path p50/p95/p99 tiles
  - active replicas per service
  - storyteller outcomes (agent, fallback, resumed)
  - workflow latency by moment kind
  - throughput
  - model failures, resilience events and replays

  All 10 KQL queries were validated against the workspace.
- **Chaos Studio** (`infra/modules/chaos.bicep`, `scripts/chaos_drill.py`): an
  Event Hubs target, the `ChangeEventHubState-1.0` capability, and an
  experiment that disables `match-stream`, waits 60 s, then re-enables it.
  - **Status: the experiment runs, but both state-change actions fail on the
    target with an empty reason.**
  - Tried so far:
    1. A continuous action. Rejected by the service: it's a discrete fault.
    2. Discrete actions with Event Hubs Data Owner.
    3. The same with Contributor scoped to the namespace.
  - Not yet tried: a newer preview API version, or the Service Bus
    `ChangeQueueState` fault.
  - The **ACA kill drill (checkpoint 3) is the verified resilience demo.**
- **Simulator rides out broker outages:** `send_with_retry` backs off for up to
  5 minutes, and the match catches up afterwards. That's useful whether or
  not Chaos Studio cooperates.
- **My home network** dropped DNS several times during long scripts. Cloud
  components were unaffected. The scripts tolerate these transient failures.

## Checkpoint 8: agent-based match engine (2026-10-09)

Feedback after watching matches: play was predictable, players had no individuality, every
move looked the same, the game froze while a player had the ball, and midfield carriers
went unpressed. Measured on the old engine: the ball sat still for more than 1.5 s for
**61%** of the match, and the carrier's nearest opponent was a median **8.9 m** away
(more than 10 m for 44% of the time). Events came first and movement was drawn on afterwards,
so nothing on the pitch affected what happened.

- **New engine** (`src/matchlens/sim/engine.py`): every 0.1 s the 22 players and the ball
  move together.
  - The carrier takes a first touch, dribbles or shields while deciding. They choose by
    weighing passes (with real lane-interception risk), carries (risk from every nearby
    opponent along the path), shots (xG with blockers), crosses, through balls,
    clearances and give-and-goes. A softmax tempered by vision and flair makes the choice.
  - Defenders: the nearest one closes the ball down, and closes tighter the longer the
    carrier has it. The next one covers the best short option, and the rest mark goal-side.
    The back line stays goal-side and narrows near the box.
  - Attackers show for the ball (two supporting spots, chosen for space), make runs in
    behind (by role) and drift away from markers.
  - Tackles, take-ons, fouls (penalties in the box), interceptions, aerial duels, headers,
    saves (catch, parry, corner), rebounds and loose-ball scrambles are all resolved from
    positions.
  - Set pieces have shapes (corners, penalties, walls at 9.15 m). Substitutions take off
    the most tired players. Trailing teams push and press late; leading teams sit deeper.
- **Players** (`world.py`): roles such as poacher, target forward, false nine, inverted
  winger, overlapping full-back, deep-lying playmaker and ball-winner. Each has 19
  trait ratings from 1–99 and a preferred foot.
  **Teams** have styles: high press, possession, counter-attacking, direct, balanced and low
  block. Two new formations: 4-2-3-1 and 3-5-2. Everything is in the metadata.
- **Web:** a Squads tab shows formation, style, roles and each player's top traits.
  Clicking a player follows them on the pitch.
- **After:** the ball is still for more than 1.5 s for 34% of the match, almost all of it
  dead-ball restarts. A carrier is static only 9% of their time on the ball, mostly
  keepers holding a catch. The nearest opponent is a median **3.4 m** away (more than 10 m
  for 11% of the time). A full 90-minute match generates in about 8 s, against 36 s for
  the old engine.
- **Calibration** (`calibrate/fit.py`) was rewritten for full matches, run in parallel,
  with SkillCorner targets plus guard rails for typical goals, fouls, cards, throw-ins,
  ball in play and header share.
  - It took three fits, with engine fixes in between: too many clearances, penalties and
    headed chances, and flat-out sprints for every movement.
  - Three values were then hand-adjusted.
  - Held-out results, against real values:

    | Metric | Error |
    |---|---|
    | Passes | +3% |
    | Pass completion | −4% |
    | Shots | −2% |
    | Distance per 90 | +4% |
    | Pass length | +23% |
    | Sprints | +34% |
    | Peak speed | −8% |

    Goals are 2.4 per match, fouls 25 and headers 15% of shots. See `docs/REALISM.md`.
- **Tests:** individuality, the ball keeps moving while owned, and the carrier is engaged.
  22 pass.
- **Known gaps:** passes are still too long, there's no offside law, and team shapes are
  rule-based. A replay re-simulates from the seed, so replays of matches played on the old
  engine would come out differently. The stored replay blobs are self-contained and still
  play.

## Checkpoint 9: 3D pitch (2026-10-09)

- **3D pitch** (`web/src/components/Pitch3D.tsx`, `athlete.ts`), built with three.js only.
  - Every player is a procedurally rigged figure, posed each frame from the tracking data.
    Heading turns at a limited rate, facing the ball when standing or backpedalling. Stride
    length and cadence follow speed, with a walk blending into a run. The body leans into
    acceleration and banks into turns.
  - Catmull-Rom sampling between feed samples gives smooth 60 fps motion.
  - The stadium has a crowd in the clubs' colours, floodlights and goals with nets.
  - Cameras: broadcast, tactical, follow player, and free orbit.
  - `?lab=1` opens an athlete test scene.
- Top speeds no longer count half-time resets as running (capped at 40 km/h, with a test).

## Checkpoint 10: ball physics and match realism (2026-10-09)

Feedback: the ball didn't behave like a ball, players dribbled over the end line, carriers
under heavy pressure kept the ball too easily, defenders never played it first time back to
the keeper, there was no shielding, there were too few good shots, and long balls had no lift.

- **Ball physics** (`sim/ball.py`): gravity, quadratic air drag, a bounce (restitution
  about 0.5) and rolling resistance on grass.
  - Kicks are solved backwards. A ground pass gets the launch speed that arrives at a
    receivable pace. A lofted ball gets the launch speed whose first bounce lands on the
    target, at an angle that depends on the type of ball (cross, switch, clearance, goal kick).
  - Receivers meet high balls where they drop through head height. Headers and aerial
    duels happen there.
- **Dribbling** is touch by touch: the ball is pushed ahead and the carrier catches up.
  Heavier touches come when hurried, and defenders can nick an exposed ball.
- **Lines:** dribbling over a line now gives a throw-in, goal kick or corner.
- **Pressure:** tackle rates rise with each extra opponent around the ball.
- **Shielding:** shielding is preferred under pressure (scaled by composure). The ball is
  kept on the far side, a good shield is hard to get round, and the 3D model squats, arms
  out, with its back to the opponent.
- **Back to the keeper:** a defender who wins a long ball in their own third with the
  keeper free plays it first time back to them (50% of the time).
- **Corners:** defenders sometimes head crosses behind for a corner.
- **Shooting:** players shoot with a clear sight of goal in the box. Long shots happen,
  driven by the long-shot trait. Bodies in the way block shots. Defenders recover at
  full effort to stay in front of a runner near goal, so attackers no longer walk the ball in.
- **3D view:** the ball rises and bounces, players swing a kicking leg (with their
  preferred foot) when they strike it, and the live feed runs at 5 Hz instead of 2 Hz.
  At 2 Hz, passes looked like they braked near players. Replays store ball height.
- **Calibration** (32 held-out matches):

  | Measure | Result | Real or typical |
  |---|---|---|
  | Shots per match | 26 | 26 |
  | Goals per match | about 3 (2.6 and 3.5 on two sets) | 2.8 |
  | Passes per match | 778 | 804 |
  | Pass completion | 87% | 83% |
  | Ball in play | 55% | 57% |
  | Headers, share of shots | 11% | |
  | Shots from outside the box | 26% | |
  | First-time passes back to the keeper | about 2 per match | |

  Remaining gaps are in `docs/REALISM.md`.

## Checkpoint 11: passing realism (2026-10-09)

After checkpoint 10, passes were about 30% too long, completion was about 4 points high, and
sprints were about a third over. The causes were too many clearances and floated switches,
and supporting players standing too far away.

- **A long pass has a cost.** Each 10 m beyond 25 m costs utility (less for players with
  vision). Defenders also keep closing the receiver while the ball travels.
- **A clearance has a cost.** It usually gives the ball back, so players only clear when
  nothing sensible is on.
- **Shorter support:** supporting players show 6–12 m away (was 9–14 m), so short
  passes appear.
- **Ambition and errors:** players take more ambitious options with less fear of losing the
  ball. Defenders cover more of the passing lane, and first touches miscontrol a little
  more often.
- **Effort:** receivers and loose-ball chasers run at 85% of top speed, not 90%.
- **Fit and checks:** re-fitted, then hand-tuned on 64 unseen matches.

| Measure | Now | Real or typical |
|---|---|---|
| Passes per match | 910 (+13%) | 804 |
| Pass completion | 82.5% | 83.4% |
| Mean pass length | 18.3 m (+12%) | 16.3 m |
| Shots per match | 23–26 | 26 |
| Goals per match | 3.0–3.2 | 2.8 |
| Sprints per 90 | 8.5 | 8.5 |
| Distance per 90 | 10.4 km | 10.3 km |
| Fouls per match | 23 | 23 |
| Yellow cards per match | 3.3 | 3.8 |
| Clearances per match | 46 | 40 |
| Headers, share of shots | 12% | 17% |
| Shots from outside the box | 42% | 38% |

## Checkpoint 12: like-for-like passing, final realism pass (2026-10-09)

- **Like-for-like pass definition.** SkillCorner counts *player possessions ending in a
  pass*. Restart deliveries (throw-ins, goal kicks, corners, free kicks, kick-offs) are not
  player possessions there, so the comparison now leaves them out too, about 100 per match.
  All passes still appear in the event stream and the stats.
- **Longer, rarer runs in behind.** The new `run_s` setting controls run length.
- **Retuning:** a stronger long-pass cost, slightly faster top speeds and a rebalanced shot mix.

On 64 held-out matches (`docs/REALISM.md`):

| Measure | Now | Real or typical |
|---|---|---|
| Passes per match | 810 (+1%) | 804 |
| Pass completion | 82.3% | 83.4% |
| Mean pass length | 16.8 m (+3%) | 16.3 m |
| Shots per match | 24.4 (−7%) | 26.3 |
| Distance per 90 | 10.23 km | 10.30 km |
| Sprints per 90 | 8.7 | 8.5 |
| Peak speed | 26.2 km/h (−8%) | 28.3 km/h |
| Goals per match | 2.9 | 2.8 |
| Fouls per match | 24 | 23 |
| Yellow cards per match | 3.6 | 3.8 |
| Headers, share of shots | 12% | 17% |
| Shots from outside the box | 36% | 38% |

## Checkpoint 13: running, pressing and duel realism (2026-10-09)

- **Routine hurrying is capped at a working pace** (`hard_run_mps`): pressing, chasing and
  getting onto a pass. Only genuine sprints reach a player's top speed: runs in behind,
  recovery runs near goal, and defenders racing a ball played in behind them. Top speeds
  are now realistic without inflating the sprint count.
- **Deeper runs in behind** (`run_depth_m`), fewer and cleaner challenges, more headed
  chances, slightly lower chance quality, shorter restart waits.

On 64 held-out matches (`docs/REALISM.md`), every SkillCorner measure is within 9%:

| Measure | Now | Real |
|---|---|---|
| Passes per match | 878 (+9%) | 804 |
| Pass completion | 82.0% | 83.4% |
| Shots per match | 27.7 (+5%) | 26.3 |
| Mean pass length | 15.5 m | 16.3 m |
| Distance per 90 | 10.61 km | 10.30 km |
| Peak speed | 27.2 km/h (−4%) | 28.3 km/h |
| Sprints per 90 | 8.6 | 8.5 |

Against typical match values:

| Measure | Now | Typical |
|---|---|---|
| Goals per match | 2.5 (2.8 on a second set of 64) | 2.8 |
| Fouls per match | 21 | 23 |
| Yellow cards per match | 3.1 | 3.8 |
| Tackles won per match | 39 | 38 |
| Throw-ins per match | 42 | 40 |
| Ball in play | 60% | 57% |
| Clearances per match | 36 | 40 |
| Shots from outside the box | 39% | 38% |
| Headers, share of shots | 12% | 17% |

## Checkpoint 14: crossing, duels and pace (2026-10-09)

- **Crossing:** wide players with good crossing look for the box (`cross_bias`). That
  brings headed chances to a realistic share.
- **On the ball:** players are slightly longer on the ball and carry it a little more,
  so passes per match match the real data.
- **Duels:** fewer tackles attempted, keeping tackles won at a typical level.

On 64 held-out matches, every SkillCorner measure is within 5%:

| Measure | Now | Real |
|---|---|---|
| Passes per match | 840 | 804 |
| Pass completion | 82.0% | 83.4% |
| Shots per match | 25.4 | 26.3 |
| Mean pass length | 15.5 m | 16.3 m |
| Distance per 90 | 10.44 km | 10.30 km |
| Peak speed | 27.1 km/h | 28.3 km/h |
| Sprints per 90 | 8.6 | 8.5 |

Against typical match values:

| Measure | Now | Typical |
|---|---|---|
| Goals per match | 2.7 | 2.8 |
| Fouls per match | 24 | 23 |
| Yellow cards per match | 3.9 | 3.8 |
| Tackles won per match | 40 | 38 |
| Throw-ins per match | 40 | 40 |
| Ball in play | 59% | 57% |
| Headers, share of shots | 20% | 17% |
| Clearances per match | 35 | 40 |
| Shots from outside the box | 37% | 38% |

## Checkpoint 15: live pipeline verified against the engine (2026-10-09)

- **Method:** the engine is deterministic, so a live cloud match can be checked event for
  event by re-simulating its seed offline.
- **Bug found (PR #26):** two of three live matches showed about half their passes and a
  missing goal. When KEDA scaling or a redeploy moved an Event Hubs partition between
  interpreter replicas, a replica that later re-claimed it kept stale in-memory state. It
  resumed from the newer checkpoint, lost everything processed in between, and overwrote
  the state doc.
- **Fix:** drop in-memory state for a partition's matches on release or claim, then reload
  the latest snapshot. A regression test was added.
- **Verified:** three full-length matches at 6×, with the interpreter **restarted
  mid-match** so every partition changed hands. Live pass counts equal the offline
  re-simulation exactly in all three (928, 899 and 955). Averages: 27 shots, 832
  open-play passes (real 804) and about 81% completion. One match finished 2–6, a
  high-scoring outlier; across 64 matches the average is 2.7 goals.
