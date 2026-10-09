# Pipeline walkthrough: watch one match go through every stage

This guide follows one match through the five stages of the shared deployment
(`rg-matchlens-dev`, East US 2) and shows how to check each stage.

| # | Stage | What runs |
|---|---|---|
| 1 | Cloud environment | azd + Bicep provision everything and deploy the app |
| 2 | Generate a match | A Container Apps Job runs the simulator: events + 10 Hz tracking |
| 3 | Stream and interpret | Event Hubs → interpreter Container App: metrics, template cues, moments |
| 4 | Explain key moments | Service Bus → storyteller: Agent Framework workflow on Foundry, checked by a code verifier |
| 5 | Push live overlays | Cosmos DB change feed → Azure Function → Web PubSub → Static Web Apps |

The shared deployment already exists, so stage 1 is just a check. You don't need
Azure access for stages 2–5: everything below goes through the public API and
the web player.

## Setup

```bash
API=https://ca-api-bhdr37v3onmcs.thankfulglacier-9473c5b7.eastus2.azurecontainerapps.io
```

`curl` can call the API directly. The browser can't, because the API only
accepts the Static Web App's origin. To watch in a browser, use the
[hosted demo](https://wonderful-field-07003a80f.4.azurestaticapps.net) or run
the player locally through the Vite proxy (`web/vite.config.ts`):

```bash
cd web && npm ci
DEV_API_PROXY=$API npm run dev      # http://localhost:5173
```

> **Cost:** every match runs on the team's subscription (compute plus model
> tokens), and match starts are rate limited. Start one short match, not a
> batch. Use `scripts/loadtest.py` only when you agree it with the team.

## 1. Cloud environment

**Where:** `azure.yaml` maps the services to hosts. A single image serves api,
interpreter and storyteller; `MATCHLENS_SERVICE` picks the role
(`src/matchlens/services/run.py`). Every resource is defined in
`infra/main.bicep` and `infra/modules/*.bicep`. After `azd deploy`,
`scripts/postdeploy.sh` points the simulator and replay jobs at the new image
and stores the MCP key in Key Vault.

**Check:**

```bash
curl -s $API/api/health          # {"ok":true,"service":"matchlens-api",...}
```

## 2. Generate a match

**What happens:** `POST /api/matches` (`start_match` in
`src/matchlens/services/api.py`) starts an execution of the `job-sim-*`
Container Apps Job through ARM. The job runs
`src/matchlens/services/simulator.py`, which calls `simulate(seed, ...)` in
`src/matchlens/sim/engine.py` and publishes the event and tracking stream to
Event Hubs `match-stream`, with the match id as the partition key.

**Do it:** start a short match. 5-minute halves at 5× take about 2–3 minutes
including job start-up:

```bash
curl -s -X POST $API/api/matches -H 'content-type: application/json' \
  -d '{"speed":5,"periodMin":5,"seed":4242}'
# {"started":true,"execution":"job-sim-...","seed":4242,"speed":5.0,"periodMin":5.0}
```

You can also use **Producer → Start a match** in the player. It always uses
45-minute halves below 10× and 15-minute halves at 10× and above, so it runs
much longer.

**Determinism check (optional, offline):** the same seed produces the same
match on your laptop. The match id doesn't affect the result.

```bash
uv run python -c "
from matchlens.sim.engine import simulate
from matchlens.interpret.state import MatchInterpreter
r = simulate(4242, match_id='m04242-local', params={'period_min': 5})
it = MatchInterpreter('m04242-local'); cues = []
for m in r.stream(): cues += it.handle(m).cues
for c in cues: print(c.template, '|', c.text)
"
```

This matches the cloud match's **Data** cues only if the deployed image was
built from the same commit as your checkout. Engine changes alter the output.

## 3. Stream and interpret

**What happens:** the interpreter (`ca-interp-*`) reads Event Hubs and runs
`MatchInterpreter.handle()` (`src/matchlens/interpret/state.py`) for each
message. That updates the metrics (xG, possession, PPDA, momentum, physical
stats), writes `matchState` to Cosmos, and sends 2 Hz frames to Web PubSub. It
emits **template cues** (goal, shot, card, sub, speed tag) without an LLM, and
it raises **moments** (goal, big chance, momentum swing, pressing surge, half
time, full time), each with an evidence pack. KEDA scales the interpreter on
Event Hubs consumer lag.

**Check:**

```bash
curl -s $API/api/matches                                   # new match on top, displayClock advancing
ID=<match id from the list, e.g. m04242-xxxxxxx>
curl -s "$API/api/matches/$ID"                             # full state document
curl -s "$API/api/matches/$ID/cues?persona=casual&lang=en" # template cues (source "template")
```

## 4. Explain key moments

**What happens:** moments go to Service Bus queue `moments`, with the match id
as the session id, so ordering is kept per match. The storyteller
(`ca-story-*`, scaled by KEDA on queue depth) runs the Agent Framework workflow
in `src/matchlens/story/workflow.py`:

1. The **Editor** applies the overlay budget.
2. The **Explainer** makes one structured Foundry call that returns every
   persona × language variant.
3. The **Verifier** (`src/matchlens/story/verifier.py`) rejects any number or
   name that isn't in the evidence pack.
4. The **Director** emits the cues.

If the model fails, the workflow falls back from `gpt-5-mini` to
`gpt-4.1-mini` and then to localized templates (`src/matchlens/story/templates.py`).
Each step saves a checkpoint to Cosmos DB.

**Check:**

```bash
curl -s $API/api/matches/$ID/moments   # moments + storyteller runs for this match
curl -s $API/api/ops                   # latest runs across all matches (the Producer run table)
```

Each run has these fields:

| Field | Meaning |
|---|---|
| `source` | `agent` (model-written and verified), `fallback` (template after a model failure), `duplicate` (already done before a crash), empty (skipped) |
| `ms` | Workflow time |
| `resumed_from` | Checkpoint id if the run resumed after a crash |
| `skipped` | Why the overlay budget dropped the moment |

In the player, cues marked **AI** arrive about 10 s after their moment. The
10 s broadcast delay hides that wait. **Why?** opens the evidence pack the
verifier checked.

**Chaos drill (optional):** `curl -s -X POST $API/api/chaos`, or **Producer →
Chaos drill**, arms a kill switch. The next workflow kills its storyteller
replica partway through. Container Apps restarts the replica and Service Bus
redelivers the moment. The run then shows `resumed_from` and finishes without
another model call.

## 5. Push live overlays

**What happens:** cues are written to the Cosmos `cues` container, which is
immutable and keyed by deterministic ids. The container's change feed triggers
`cue_publisher` in `functions/cue_publisher/function_app.py` (Functions Flex),
which publishes each cue to its Web PubSub group. Groups are per match, per
persona × language, plus an `all` group for language-neutral cues. The player
on Static Web Apps receives cues over a WebSocket; it doesn't poll.

**Check:**

- Open the match in the player and watch overlays appear without refreshing.
  Switch **Fan / Analyst** and **EN / ES / 日本語** to join different cue groups.
- `curl -s "$API/api/negotiate?matchId=$ID&persona=analyst&lang=ja"` returns
  the WebSocket URL and the groups a viewer joins. The URL contains an access
  token, so don't paste it anywhere.
- After full time, the interpreter publishes a `FullTime` event to Event Grid,
  which goes to a Storage queue and then the replay job. A minute or two later
  the match appears in `curl -s $API/api/replays`, and as **▶ Replay** in the
  player.

## Optional: watch from inside Azure

The API only shows each stage's output. To watch the services themselves, ask
the subscription owner for **Reader** on `rg-matchlens-dev`. Then install the
CLI (`brew install azure-cli`; you don't need azd or Docker for this) and run:

```bash
az login --tenant <team tenant>
RG=rg-matchlens-dev
az containerapp job execution list -n job-sim-bhdr37v3onmcs -g $RG -o table   # stage 2
az containerapp logs show -n ca-interp-bhdr37v3onmcs -g $RG --follow          # stage 3
az containerapp logs show -n ca-story-bhdr37v3onmcs -g $RG --follow           # stage 4
```

In the portal you can also see the incoming and outgoing message metrics for
Event Hubs `match-stream` and Service Bus `moments`. The **MatchLens: live
SLOs** workbook (`infra/workbooks/slo.json`) shows event-to-screen latency,
replicas per service, agent, fallback and resume outcomes, and replays.
Browsing Cosmos data needs a separate data-plane role, because local auth is
disabled; the API calls above cover the same data.

## Checklist

| Stage | Working looks like |
|---|---|
| 1 | `/api/health` returns `ok: true` |
| 2 | `POST /api/matches` returns `started: true` with an execution name |
| 3 | The match is listed with its clock advancing, and template cues appear within seconds of events |
| 4 | `/moments` runs show `source: agent`, and **AI** cues appear with a **Why?** evidence pack |
| 5 | Overlays appear live in the browser, and the match is listed in `/api/replays` after full time |
