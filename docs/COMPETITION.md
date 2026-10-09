# Inside the Game: Developer Hackathon — competition brief

What the competition asks for, how it is judged, what it forbids, and how our
idea and the existing soccer-analytics SPEC fit it. The companion document,
[AZURE-INFRASTRUCTURE.md](AZURE-INFRASTRUCTURE.md), turns this into the Azure
build.

Researched 2026-10-08. The official rules are the source of truth; rule 11.4
says they override anything on the website or in marketing.

## 1. The event

| Item | Value |
|---|---|
| Name | Inside the Game: Developer Hackathon (the rules call it the "Microsoft Premier League Hackathon") |
| Sponsor | Microsoft. The Premier League helps judge but doesn't administer it |
| Website | <https://aka.ms/insidethegame> → `innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon` (sign-in required) |
| Official rules | <https://github.com/microsoft/insidethegamehackathon> (`OFFICIAL RULES.md`) |
| Learning playlists | `RESOURCE PLAYLISTS.md` in the same repo: GitHub Copilot, Foundry, Foundry Agents, Agentic Workflows, Data |
| Livestream | **Oct 14, 16:00–17:00 UTC**: "Build AI-powered soccer insights apps with AKS and ACA" (Brian Benz). Companion code: <https://github.com/bbenz/football-results-dashboard> |
| Cancelled | Oct 21 "…with cloud-native Azure Databases" (Azure SQL, PostgreSQL, Cosmos DB + vector search) |

### Dates (Pacific Time)

| Milestone | Date |
|---|---|
| Registration opens | Sep 29, 09:00 |
| Hacking / submission opens | Oct 6, 09:00 |
| **Registration closes** | **Oct 20, 12:00 (noon)**: every team member registers before this |
| **Submission deadline** | **Oct 27, 23:59** |
| Judging | Oct 27 – Nov 10 |
| Winners announced | By Nov 20 |

From today (Oct 8) we have **19 days** to build.

### Who can enter

- Individuals of the age of majority, teams of **up to 4**, or organisations.
  A team appoints one Representative who submits.
- Not eligible: residents of Brazil, Quebec, Russia, Crimea, Cuba, Iran, North
  Korea, Sudan, Syria or other OFAC-designated places. Also not eligible:
  employees of Microsoft or the Premier League, their families, and anyone
  involved in running the event.
- One person can be on several teams and enter individually too. Multiple
  submissions must be "substantially different".

## 2. The challenge (rule 4.1)

> Build AI-driven applications and agents that help transform **synthetic**
> football events into explainable match intelligence … real-time narratives,
> and automated match recaps suitable for studio, broadcast, or streaming
> scenarios, and flexible enough for fans in any market to tailor their
> experience.

### One pipeline, five stages

| Stage | What the brief asks |
|---|---|
| (a) **Ingest** | Synthetic match events *as they happen*: passes, shots, tackles, possession changes, pressure events, match metadata |
| (b) **Interpret** | Turn them into meaningful statistics, patterns and context |
| (c) **Explain** | Say *why* a moment matters, not only that it happened |
| (d) **Render** | Something that sits on screen alongside the match: timed, machine-readable output that can drive synchronised graphical overlays on a live feed |
| (e) **Personalize** | The analyst and the casual fan get two genuinely different experiences. Also favourite clubs and players, a player-focus mode, language, and a single metric a viewer cares about |

The rules add: "a Project that only generates metrics has no audience, and a
solution that only personalizes has nothing to personalize."

### Features the brief names

1. **Real-time match intelligence**:
   - player name tagging, with speed and distance indicators when thresholds are hit
   - pass distance, accuracy and difficulty
   - ball speed and shot speed
   - **auto-eventing** (a model over a video feed that generates those metrics automatically)
2. **Narrative generation**: key narratives, player-performance context, notable milestones, recaps.
3. **Explainability**: control vs chaos, tactical pressure changes, game rhythm.
4. **Data innovation**: create or extend synthetic, football-realistic datasets.
5. **Multi-language storytelling** for global studio and streaming.

## 3. Prizes and categories

A project can win **one Grand Prize and one Category Prize**. Judges may move a
project to a better-fitting category.

| Prize | Per team member |
|---|---|
| Grand Prize, 1st | $4,000 e-gift card + Premier League match ticket (~$1,000) + $50 PL store voucher + social feature |
| Grand Prize, 2nd | $2,500 + match ticket + $50 voucher + social feature |
| Each category prize | $1,500 + $50 voucher + social feature |

Category prizes:

| Category | What judges look for |
|---|---|
| Best Use of Microsoft Foundry | Foundry speeds up development, orchestration and management of the AI |
| Best Enterprise Solution | Clear path to enterprise adoption: reliability, transparency, controls |
| Best Multi-Agent System | Agents with distinct roles, shared state, effective handoffs, failure recovery, an outcome one agent couldn't achieve. Copilot, Agent Framework, A2A or similar |
| **Best Azure Cloud Native Integration** ← **our target** | AKS, Azure Container Apps, Functions, Logic Apps and AI-optimized data platforms used to build scalable, resilient, intelligent solutions. Effective integration, **event-driven architecture, automation, operational excellence**, AI capabilities, measurable business impact |

## 4. Judging (rule 6)

**Stage one, pass/fail:** does it fit the theme, and does it reasonably use the
required technologies?

**Stage two, five criteria at 20% each:**

| Criterion | Questions |
|---|---|
| Technological Implementation | **Creative, optimised synthetic data creation**? Quality software practice (MS Learn, cloud best practices)? Effective use of the hero technologies: Foundry, Agent Framework, Azure MCP, GitHub Copilot Agent Mode, Fabric, GitHub SDK, GitHub CLI, Azure Apps and AI Services, Azure databases? Well-structured, documented, maintainable code? |
| Agentic Design & Innovation | Creative agentic patterns? Sophisticated orchestration, MCP integration or multi-agent collaboration? Novel AI? |
| Real-World Impact | Significant problem? Deployable to production? Impact on users and businesses? |
| User Experience & Presentation | Intuitive UX? Does the demo video communicate the value? **Balanced frontend and backend**? |
| Adherence to Category | Does it match the chosen category's description? |

Ties are broken on Technological Implementation first, then the next criterion
down. The rubric and the four categories match AI Dev Days 2026. Its winners
shared a pattern: specialist agents on a shared contract, deterministic numbers
with LLM explanations, visible reasoning and traces, real Azure deployment with
IaC and observability, and a judge-first README.

## 5. Submission requirements (rule 4.4)

- [ ] A **new** project, created after Sep 29 (rule 4.3). The SPEC is a design
      with no code yet; everything is built from scratch in this repo.
- [ ] An elevator pitch: what we built, **which Microsoft and Azure
      technologies**, what it does, what problem it solves.
- [ ] A demo video **under 2:00** on public YouTube, Vimeo, Facebook Video or
      Youku. It must show the project running, with **no third-party
      trademarks and no copyrighted music**.
- [ ] A **public** GitHub repo URL. `aknotwell/MatchLens` is already public.
- [ ] A working project judges can test free until Nov 10: a URL, with test
      credentials if private (rule 4.8). Judges may judge from the video alone.
- [ ] Everything in English.
- [ ] It must run "consistently on the platform for which it is intended" and
      "function as depicted in the video" (rule 4.3).

## 6. Data and IP rules, and what they mean for the SPEC

### The rules

- **4.7:** "Projects must use **synthetic, football-realistic** data." No
  footage or data is provided. Any footage you use must be licensed by you.
- **4.3 / 4.11:** any third-party SDK, API or data must be used within its
  licence, and the submission must not infringe anyone's IP.
- **7:** every entrant (not only winners) grants Microsoft and the Premier
  League a "worldwide, perpetual, irrevocable, royalty free … license to use
  such entry … for any non-commercial or **commercial** purpose".
- **11.8:** the rules grant no licence to Premier League or Microsoft marks.

### "We're not selling it, so is it commercial use?"

It doesn't matter that we aren't selling anything. Under rule 7 we license the
entry to Microsoft and the Premier League for commercial use, and under rules
4.6 and 4.11 we warrant that we have the right to. If any part of the entry is
derived from data that bans commercial use, we would be granting rights we
don't hold. (This is not legal advice.)

### What the open datasets allow (licences checked 2026-10-08)

| Dataset | Licence | Can we use it? |
|---|---|---|
| **SkillCorner open data**: 10 A-League 2024/25 matches, 10 fps broadcast tracking, dynamic events | **MIT** | **Yes**, for calibration, with credit. Keep raw files out of the repo |
| **IDSSE / DFL** (Bassek et al. 2025): 7 Bundesliga matches, official events + 25 Hz TRACAB tracking | **CC BY 4.0** (authorised by DFL) | **Yes**, for calibration. Credit DFL and cite the paper. The best source for synchronised tracking and events |
| **Wyscout public** (Pappalardo et al. 2019): ~1,900 matches of events, 2017/18 | **CC BY 4.0** | **Yes**, for event-rate, pass and shot distributions and an xT grid. Contains real names, so publish aggregates only |
| StatsBomb Open Data (+360) | Custom agreement; §1.2.2 bans commercial exploitation "of the data or any analysis derived from" it | **No**, not even for calibration |
| PFF FC World Cup 2022 | Terms on request, unverified | **No** |
| Metrica sample data | No licence, only "be responsible" | Local parser testing only; nothing shipped is derived from it |
| Impect open data | Bans commercial use | **No** |
| Google Research Football | Apache-2.0 + Unlicense engine | Allowed, but archived 2026-08-19, game-like stats, and kit textures named after real clubs |
| footballSimulationEngine (npm) | MIT | Allowed, but 2D game logic, not calibrated to real football |

### The decision: build our own calibrated simulator

1. **The live feed is 100% synthetic**: a simulator we write generates events
   and tracking. It never copies rows from a real provider.
2. **It is calibrated, not copied.** An offline job fits the simulator's
   parameters to distributions from SkillCorner (MIT), IDSSE (CC BY) and
   Wyscout (CC BY). Examples: pass lengths and angles, event rates per phase,
   shot locations and xG surface, speed and sprint profiles, pressure
   distances, possession durations. Only fitted parameters and aggregate
   statistics are committed, with a `NOTICE` and a datasheet crediting each
   source. Raw files are downloaded by script into a gitignored folder.
3. **Realism is measured and shown.** A "realism report" compares synthetic and
   real distributions: KS distance, means against bands, and per-match
   sanity ranges like those in SPEC §8. It goes in the README and the video.
   That answers the first judging question ("optimized synthetic data
   creation") with evidence.
4. **Fictional world.** A fictional league, clubs, players, kits and crests that
   we draw ourselves. No "Premier", no lion, no PL purple. Disclaimers go in
   the README and at the start and end of the video. A swappable "brand pack"
   shows a licensed broadcaster could drop in official assets.

### How the SPEC carries over

| SPEC item | Hackathon status |
|---|---|
| Metric definitions (§6): possession, field tilt, entries, xT, VAEP, momentum, xG, passing networks, PPDA, counterpress, smoothing, physical bands, ball speed, tracking possession, pitch control, EPV, shape | **Reused** as the deterministic Interpret stage. They now run incrementally on a live stream instead of on a finished file |
| `kloppy` loaders (§4) | **Reused offline** to load SkillCorner/IDSSE/Wyscout for calibration |
| `socceraction`, `floodlight`, `mplsoccer`, `networkx`, LaurieOnTracking (all MIT/BSD) | **Reused**. Retrain xT, VAEP and xG on synthetic plus CC BY data. Regenerate LaurieOnTracking's EPV grid; its shipped grid came from Metrica |
| Canonical data model (§5) | **Reused** and extended into the event-stream contract (see the infra doc §4) |
| Golden tests and sanity ranges (§8) | **Reused** as realism gates for the simulator |
| StatsBomb, PFF, Metrica as data sources (§2) | **Dropped** |
| Non-goals "no live data, no web app, no database" (§1) | **Reversed**: the hackathon needs all three |
| "Lives in the Darwin repo" (§3) | **Changed**: everything lives in this public `MatchLens` repo |
| CV pipeline (§10, "later") | Optional stretch: auto-eventing on synthetic rendered video |

## 7. Our idea: a live explain layer

**One sentence:** a stream of synthetic match events and tracking goes in. Out
come timed, verified overlays that explain *why* each moment matters, written
differently for every viewer, running on an event-driven Azure platform.

| Stage | What MatchLens does |
|---|---|
| Ingest | The calibrated simulator plays a match in real time (or 1–60× faster) into Event Hubs |
| Interpret | Deterministic SPEC metrics, updated live: speeds and sprints, pass difficulty, ball and shot speed, xG, xT, momentum, field tilt, PPDA, a control-vs-chaos index. Detectors flag moments: momentum swings, pressing collapses, sprint thresholds, big chances |
| Explain | Agent Framework agents (Editor → Explainer → Verifier) write the *why* from an evidence pack. A code-controlled verifier rejects any number it can't trace to the match state |
| Render | A Director agent turns insights into a timed overlay-cue JSON (lower-thirds, player tags, pitch graphics). Cues are pushed over Web PubSub to a React overlay on a live 2D pitch, and a producer console can approve or retract them |
| Personalize | Analyst or casual depth, club-side framing, player-focus mode, single-metric mode, several languages, and audio recaps with Azure Speech |

**Why Cloud Native suits it:** it is an event-driven streaming system
(Event Hubs, KEDA-scaled Container Apps, Functions, Cosmos DB change feed,
Web PubSub, Event Grid, Logic Apps, Fabric Real-Time Intelligence). We can
measure the things that category rewards: scale, resilience, automation and
operational excellence.

### What competitors are already doing

At least 10 public competitor repos existed by Oct 7. The common template is a
multi-agent narrator with a fact-checker, personas, translation and momentum.
Two are worth noting:

- `kaanoguzkan/insidethegamehackathon` ("MatchMind") is far along: 5 Hz
  simulator, Agent Framework agents with a code-controlled verifier, a 25-tool
  MCP server, a React match centre and Bicep for ACA/Cosmos/SignalR. It is
  **not deployed** to Azure, has **no live model**, and the Functions/Event
  Grid pipeline is unbuilt.
- `helenapedro/inside-the-game` is also called **"MatchLens AI"** and targets
  Multi-Agent. Same name as ours, so consider renaming before submission.

**Where we can win:** a real, deployed, event-driven Azure system that we can
prove scales and recovers (load test, chaos drill, SLO dashboard). Add to that
a measurably realistic simulator calibrated from licensed data, and a polished
overlay experience. Doing the brief well is table stakes; Cloud Native depth
and synthetic-data realism set us apart.

## 8. Open questions for the team

1. Team roster (up to 4) and the Representative. Everyone must register by
   **Oct 20 noon PT**.
2. Product name: keep "MatchLens" despite the collision?
3. Which three languages for the demo? English plus two, ideally one
   non-Latin script.
4. Can the .edu Microsoft account start a Fabric trial? That decides whether
   Fabric RTI is free or paid capacity.
5. Who records and narrates the video? Human voice or an Azure Speech HD voice?

## Sources

- Official rules and playlists: <https://github.com/microsoft/insidethegamehackathon> (commit `9c6c996`, 2026-10-06)
- Reactor series: <https://developer.microsoft.com/en-us/reactor/series/S-1709/> · events [27536](https://developer.microsoft.com/en-us/reactor/events/27536/), [27555](https://developer.microsoft.com/en-us/reactor/events/27555/)
- Livestream reference app: <https://github.com/bbenz/football-results-dashboard>
- Licences: [SkillCorner](https://github.com/SkillCorner/opendata) (MIT) · [IDSSE](https://github.com/spoho-datascience/idsse-data) (CC BY 4.0) · [Wyscout repackage](https://github.com/koenvo/wyscout-soccer-match-event-dataset) (CC BY 4.0) · [StatsBomb agreement](https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf) · [Metrica](https://github.com/metrica-sports/sample-data)
- Competitor research (useful, partly unverified): <https://github.com/anontrenchpaper/ms-epl-hackathon>
- Competitor repos: [MatchMind](https://github.com/kaanoguzkan/insidethegamehackathon) · [MatchLens AI](https://github.com/helenapedro/inside-the-game) · [bikilath](https://github.com/bikilath/inside-the-game)
