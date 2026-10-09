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
