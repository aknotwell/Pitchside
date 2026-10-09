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
