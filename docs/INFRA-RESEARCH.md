# What a winning entry needs from its infrastructure

Research run of 2026-10-08 for the Microsoft x Premier League **Inside the
Game** hackathon. It covers what the judges reward, what competitors have
built, what past winners did, and current Azure facts. It updates
[COMPETITION.md](COMPETITION.md) and
[AZURE-INFRASTRUCTURE.md](AZURE-INFRASTRUCTURE.md); corrections are in §6.

Tags: **[V]** checked against an official source on 2026-10-08. **[I]** our
inference. **[E]** estimate.

## 1. Bottom line

1. **No competitor has built an event-driven pipeline on Azure.** We read 27
   competitor repos.
   - None runs Functions in code.
   - None has deployed a broker or scales on queue or stream lag.
   - None has load tests, chaos drills or SLOs.
   - One entry is live on Container Apps, but it is a single container.

   The Cloud Native category is open to whoever ships the pipeline and proves
   it under load and failure.
2. **Past winners won on visible, reproducible deployment, not service count.**
   The AI Dev Days 2026 Azure-category winner (NimbusIQ) had:
   - `azd up` driving Bicep built on Azure Verified Modules
   - Container Apps
   - managed identity everywhere
   - OpenTelemetry traces for every agent step
   - a README that sold the project in 60 seconds

   It had no CI/CD workflows and still won.
3. **Judges may judge from the video alone** (rule 4.8). Every infra claim
   needs a visible artefact: a chart, a trace, a dashboard or a live kill.
   Infra nobody can see scores nothing.
4. **The deployment has to stay up and open until Nov 10, 23:59 PT.** No IP
   allow-lists, no credentials-only access, no 26-second cold starts. Budget
   for always-on through the judging window.
5. **The design in AZURE-INFRASTRUCTURE.md is the right shape.** Six facts in
   it were wrong or costly, and they are now fixed (§6).

## 2. What the rules reward (unchanged since 2026-10-06 [V])

- **Category text** (§4.2): "Best demonstration of how Azure cloud-native
  services (AKS, Azure Container Apps, Functions, Logic Apps, and AI-optimized
  data platforms) are used to build scalable, resilient, and intelligent
  solutions. Winners should showcase effective integration of Azure services,
  event-driven architectures, automation, operational excellence, and AI
  capabilities to deliver measurable business impact."
- **Stage one** is pass/fail on theme and "required technologies". **Stage
  two** has five criteria at 20% each. Ties are broken on **Technological
  Implementation** first, which asks about "best practices in Cloud Apps" and
  hero technologies (Foundry, Agent Framework, Azure MCP, Copilot Agent Mode,
  Fabric, GitHub SDK/CLI, Azure Apps and AI Services, Azure databases).
- **Real-World Impact** asks: "Could this solution be deployed in a production
  environment?"
- **Fabric is optional.** It is one item in an "etc." list.
- **No Azure credits are offered.** §4.12 bars "financial or preferential
  support from the Sponsor", so we pay for our own subscription.
- Judges may move an entry to another category. One entry can win one Grand
  Prize and one category prize.
- The official repo has issues and discussions turned off. Rule changes would
  appear on the login-gated Innovation Studio page (§11.5). **Check it weekly.**

## 3. Competitors' infrastructure

| Repo | Azure in IaC/code | IaC | Live on Azure? | Event layer | Load/chaos |
|---|---|---|---|---|---|
| timmytrace/pl-insights-engine (.NET) | ACA (0–2), ACR, UAMI, Foundry, Speech, App Insights | Bicep | **Yes**, ACA, 26 s cold start | None | None |
| kaanoguzkan "MatchMind" (strongest overall) | ACA, SWA, Cosmos, Storage queue, SignalR, Key Vault, App Insights, budget | azd + Bicep | No, replay on GitHub Pages only | Functions/Event Grid/change feed "not built" | Model fault injector only |
| paulap887/matchdirector (.NET Aspire) | **Event Hubs**, Foundry, SignalR, App Insights, keyless | Aspire + azd | No | Event Hubs, emulator only so far | None |
| KumarPadigeri/inside-the-game | SWA now; plans Cosmos, ACA, Functions | None yet | SWA | Planned | None |
| tolga-ileri "Rely" | ACA ×2 (HTTP scale), ACR with admin keys | Bicep | No | None | None |
| abhiiiesh/MatchMind | ACA, Cosmos, OpenAI, Speech | Bicep | No | None | None. **Uses StatsBomb and real clubs**, a likely rules failure |
| helenapedro "MatchLens AI" | Foundry hosted agents (design only) | None | No | None | None. **Name collides with ours** |
| ~15 others | Little or no Azure | None | Vercel/Pages at most | None | None |

**Rivals in our category:** KumarPadigeri says it targets Cloud Native and
plans to deploy Oct 13–19. paulap887 is the only one wiring Event Hubs.
timmytrace is the only one already live.

**Table stakes in the field:**

- A seeded simulator.
- "Numbers from code, words from AI".
- A narrator agent plus a code verifier.
- Personas and languages.
- One or two ACA apps with Bicep.
- CI running lint, tests and evals.

**White space, which nobody has done:**

- Functions in working code.
- A deployed broker.
- KEDA scaling on lag.
- Event Grid, Service Bus or the Cosmos change feed.
- Web PubSub, Fabric RTI or Logic Apps.
- Load tests, chaos drills, SLOs and alerting.
- Resumable agent workflows.

## 4. What past winners did

| Event / category | Winner | Infra that mattered |
|---|---|---|
| AI Dev Days 2026, **Best Azure Integration** (our category's predecessor) | [NimbusIQ](https://github.com/lukemurraynz/NimbusIQ) | `azure.yaml` with 3 ACA services, AVM Bicep, managed identity + least-privilege RBAC in Bicep, OTel on every agent and MCP call, azd hooks (role grants, Foundry agent setup, a deployment-gate script), Playwright + k6, a "validate-no-mocks" CI script, `.drawio` diagrams. Entered as Multi-Agent/Enterprise; **judges moved it to Azure** |
| AI Dev Days 2026, Grand Prize | [PRism](https://github.com/soni-ishan/PRism) | ACA, Functions (timer/Event Grid/HTTP), AI Search, Content Safety, OTel→App Insights, Bicep, 4 GitHub Actions workflows. README opens with the video, **live URL** and diagram. One memorable 0–100 score |
| Agents League 2026 (round 1) | WAIA, CertPrep | Praised for "end-to-end automated Bicep deployment", OTel visible in Foundry, a three-tier fallback so it "runs reliably even without Azure credentials", and hundreds of tests |

**Patterns that go with winning [I]:**

- One-command deploy at the top of the README.
- ACA as the compute layer, with Functions for events.
- Secure-by-default vocabulary: managed identity, RBAC in IaC, Key Vault.
- Traces you can show.
- A live URL plus a fallback that needs no credentials.
- Deterministic plus LLM hybrid, with a human approving actions.
- Each Azure service visibly earning its place.
- One headline metric.

**Patterns behind losing entries [V/I]:**

- A missing video or an unlisted repo. These were the top disqualifiers,
  "screened out on a technicality" (Agents League post).
- A README that doesn't sell in two minutes.
- Agents that are implied rather than shown.
- Azure hidden behind generic words.
- A demo that needs keys or quota the judge doesn't have.

**Copy NimbusIQ's README order:**

1. Category line.
2. Video and live URL.
3. Screenshot.
4. Architecture diagram.
5. Comparison table.
6. Hero Technologies table.
7. `azd up` quick start.
8. List of provisioned resources.
9. Project structure.

## 5. The infrastructure a winning entry needs

### P0: without these we don't win the category

| Requirement | Concretely | What the judge sees |
|---|---|---|
| Event-driven backbone, deployed | Simulator (ACA job) → **Event Hubs Standard** → `interpreter` (ACA, **KEDA on Event Hubs lag**) → **Service Bus sessions** → `storyteller` (ACA, KEDA on queue depth) → **Cosmos change feed** → **Functions Flex** cue publisher → **Web PubSub** → browser | The architecture diagram, plus a live trace from one simulator event to an overlay with a single `trace_id` |
| Functions doing real work | The cue publisher (Cosmos trigger → Web PubSub output binding) and the **match-data MCP server** (MCP tool triggers, which appear GA [V]) | No competitor has Functions in code |
| Scale proof | **Azure Load Testing** (or a k6 job on ACA): 20 simulator jobs at 10× speed. Chart replicas against p95 event→overlay latency | One chart in the video and README |
| Resilience proof | (a) Kill the `storyteller` replica mid-match; the MAF `CosmosCheckpointStorage` workflow resumes. (b) A **Chaos Studio** fault disables the Event Hub or Service Bus queue for 60 s; the backlog drains when it returns. Chaos Studio **cannot target ACA** [V], so (a) goes through our own fault endpoint or `az containerapp revision restart` | 10 seconds of video: a red tile, then green, and the overlays never miss their slot thanks to the broadcast delay |
| Operational excellence | OpenTelemetry (Azure Monitor distro, `gen_ai.*` conventions) → App Insights. An **SLO workbook**: p95 template cue < 1 s, p95 verified cue < 8 s, fallback rate, verifier rejects, tokens and $ per match. An alert → Event Grid → Logic App → GitHub issue | The SLO workbook and a GitHub issue opened by the system |
| Automation and IaC | `azd up` from an empty subscription. Bicep built on AVM. `azd pipeline config` (GitHub OIDC, no secrets [V]). CI: lint, tests, contract tests, golden-match replay, `bicep build`, deploy, smoke test, **Foundry eval gate** | README badge plus "`azd up` in N minutes" |
| Keyless security | One user-assigned identity per service with least-privilege roles. Local auth off on Event Hubs, Service Bus, Cosmos, Storage, Foundry and App Insights. ACR admin off. Deploy by digest | The "provisioned resources and identities" table in the README |
| Judge-proof availability | Public URL until Nov 10, **no IP allow-list**. `minReplicas=1` on `api`/`interpreter`/`storyteller` and always-ready=1 on the cue publisher during judging. Per-client rate limits and a per-visitor token budget. A **static replay mirror** (SWA) as a fallback if Azure has a bad day | A judge clicks the link and a match is already playing |
| Template tier | Facts (goals, cards, sprints, speeds) never wait for an LLM. If model quota or a model fails, the demo still works | Overlays land on time even with the model-health switch off |

### P1: strong differentiators, worth building

- **Measurable business impact table in the README:** overlays per match,
  p95 latency, $ per match, languages served, false claims blocked by the
  verifier, and replicas at peak.
- **Logic Apps Standard** on Event Grid `FullTime`/`RecapReady`: sends recaps
  out. It is named in the category text.
- **Foundry hosted agent "Ask the match"** (hosted agents are GA [V]), with
  Toolbox pointing at our MCP server. It covers the Foundry hero technology
  without putting Foundry on the hot path.
- **ACA revision blue/green with traffic split.** Show it in the README; the
  reference app measured a 12 s rollback [V].
- **Copilot agent mode** using our MCP server via `.vscode/mcp.json`, plus the
  Azure MCP Server. This covers two hero technologies at almost no cost.

### P2: only if ahead of schedule

- **Fabric RTI** (Eventstream → Eventhouse → dashboard + Activator). It is a
  hero technology and the category's "AI-optimized data platform", but it is
  optional. Trial is F4/F64 for 60 days on a work/school tenant; otherwise
  about $0.36/hr, paused when idle.
- AKS Automatic running the same images, as a portability proof. About
  $7–10/day; one demo day only.
- Serverless GPU auto-eventing. **Not in East US 2.**
- APIM AI gateway. **Skip it**: the token-limit policy needs Basic v2 at about
  $150/month.

## 6. Corrections to the earlier design (now applied)

| Earlier assumption | Fact [V] | Change made |
|---|---|---|
| Event Hubs Capture for the archive | Capture costs **$0.10/hr extra** (~$73/month) on Standard | Off by default; the interpreter writes the log to Blob/Cosmos |
| Chaos drill via Chaos Studio | **No Chaos Studio target for ACA.** Event Hubs, Service Bus, Cosmos and Key Vault faults are supported | App-level kill for ACA; Chaos Studio for broker faults |
| GPT-6 tiers deployable on day 1 | GPT-6 and gpt-5.5 **need a quota request** below Tier 5/6 subscriptions | Request on day 1; build on gpt-5.4-mini/nano |
| APIM AI gateway as a stretch | Token limits and circuit breaker are **not on Consumption** | Downgraded to "probably skip" |
| Foundry hosting is beta | Hosted agents are **GA**; the Python hosting package is still a prerelease | Text updated |
| `api` with IP restrictions | Rule 4.8 needs unrestricted judge access until Nov 10 | Rate limits instead |
| Copilot for students is paused | It is now a separate **Copilot Student** plan; sign-ups reopened gradually from 2026-06-17 | Text updated |
| Serverless GPU as a stretch | Not offered in East US 2 | Noted: Sweden Central only |

**Confirmed as assumed:**

- ACA KEDA scalers for Event Hubs and Service Bus with managed identity.
  Configure in Bicep or CLI, not the portal.
- The ACA Consumption grant: 180k vCPU-s, 360k GiB-s and 2M requests per
  month.
- Flex Consumption always-ready and Python 3.10–3.14. Every trigger except
  Blob works on Flex.
- Web PubSub Standard at $1.61/unit/day.
- Event Hubs Basic allows only one consumer group.
- Cosmos serverless change feed and vector search (both GA).
- Foundry workflows retire on 2026-12-01.
- `agent-framework` 1.21.0, plus `agent-framework-azure-cosmos` (beta) for
  `CosmosCheckpointStorage`.
- `azure-ai-projects` 2.8.0.
- Azure for Students: $100 credit; the free account: $200 for 30 days.

**From the reference app** (bbenz/football-results-dashboard):

- It rejected Agent Framework because it had no per-answer tool-call cap.
- **We need our own caps:** MAF middleware limiting tool calls, output tokens
  and wall time per moment.
- It used plain Bicep and scripts, not azd. Our azd choice is still fine.

## 7. Running through the judging window

- **Uptime:** the stack must be up from Oct 27 to Nov 10 (15 days).
- **Estimated always-on cost [E]:**

  | Item | $/day |
  |---|---|
  | Event Hubs Standard 1 TU | ~0.72 |
  | Service Bus Standard | ~0.33 |
  | Web PubSub Standard | 1.61 |
  | Three ACA apps at min 1 | ~1–4 |
  | Functions always-ready | ~1–2 |
  | Cosmos serverless, logs, ACR | ~1–2 |
  | **Total** | **~$6–10** |

  That is **~$90–150 for the window**, plus tokens from judge traffic. This
  fits the $300+ budget, but only if Fabric and AKS stay off.
- **Simulator always running:** a scheduled ACA job starts a fresh match every
  N minutes, so a judge always lands on a live match. It runs at 1× for
  realism and 5× on a "highlights" channel.
- **Watchdog:** an availability test in App Insights, an alert, and a Logic
  App that restarts the simulator job and opens an issue. This is the
  "operational excellence" story, running for real.
- **Freeze:** infra changes stop on Oct 25. After submission, change nothing
  except through the pipeline, with a rollback ready.

## 8. Day-1 checklist (Oct 8–9)

1. Create a pay-as-you-go subscription with a budget alert (50/80/100%). Use
   students' $100 subscriptions only as personal sandboxes. They have 15 Flex
   cores per region and no batch quota.
2. Register resource providers. Check availability in `eastus2`:
   - Flex Consumption (`az functionapp list-flexconsumption-locations`)
   - Foundry hosted agents
   - GPT-6/5.x models
   - Web PubSub
   - Load Testing
3. **Request GPT-6 Sol/Astra quota now.**
4. Write the `azd` skeleton with AVM modules. Run `azd pipeline config` for
   OIDC.
5. Decide the name. "MatchLens AI" is already taken by another entry.
6. Register every teammate before **Oct 20, 12:00 PT**.
7. Watch the **Oct 14, 16:00 UTC** livestream (AKS and ACA, Brian Benz) for
   any judge-relevant hints.

## Sources

- Rules: <https://github.com/microsoft/insidethegamehackathon> (`OFFICIAL RULES.md`, HEAD 9c6c996)
- Reference app: <https://github.com/bbenz/football-results-dashboard>
- AI Dev Days winners: <https://techcommunity.microsoft.com/blog/azure-events/announcing-the-ai-dev-days-hackathon-winners/4513528> · NimbusIQ blog <https://luke.geek.nz/azure/nimbusiq/>
- Agents League winners: <https://techcommunity.microsoft.com/blog/azuredevcommunityblog/agents-league-meet-the-winners/4507503> · <https://techcommunity.microsoft.com/blog/educatordeveloperblog/%F0%9F%8F%86-agents-league-celebrating-the-builders-who-made-agents-battle-for-glory/4538007>
- ACA scaling: <https://learn.microsoft.com/azure/container-apps/scale-app> · billing: <https://learn.microsoft.com/azure/container-apps/billing> · GPU: <https://learn.microsoft.com/azure/container-apps/gpu-serverless-overview>
- Flex Consumption: <https://learn.microsoft.com/azure/azure-functions/flex-consumption-plan> · MCP bindings: <https://learn.microsoft.com/azure/azure-functions/functions-bindings-mcp>
- Event Hubs tiers: <https://learn.microsoft.com/azure/event-hubs/compare-tiers> · Event Grid tiers: <https://learn.microsoft.com/azure/event-grid/choose-right-tier>
- Chaos Studio faults: <https://learn.microsoft.com/azure/chaos-studio/chaos-studio-fault-providers>
- Foundry GA status and retirements: <https://learn.microsoft.com/azure/foundry/concepts/general-availability> · model quotas: <https://learn.microsoft.com/azure/foundry/openai/quotas-limits>
- Agent Framework checkpoints: <https://learn.microsoft.com/agent-framework/workflows/checkpoints>
- Fabric trial: <https://learn.microsoft.com/fabric/fundamentals/fabric-trial>
- APIM token limit: <https://learn.microsoft.com/azure/api-management/llm-token-limit-policy>
- azd pipelines: <https://learn.microsoft.com/azure/developer/azure-developer-cli/configure-devops-pipeline>
- Copilot Student: <https://github.blog/changelog/2026-06-17-copilot-individual-plan-sign-ups-are-reopening>
- Prices: Azure Retail Prices API (`prices.azure.com`), East US 2, 2026-10-08
