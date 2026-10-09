# Pitchside

**One match, many broadcasts.**

Pitchside turns a live synthetic football match into explainable match intelligence, and a different broadcast for every viewer. It follows five stages: **ingest → interpret → explain → render → personalize**. The same match becomes:

- **Fan:** stakes, emotion and simple stats
- **Analyst:** pressure, pass difficulty and momentum
- **Learn mode:** coaching cues for academy players, including *Decision Review* ("what else was on?")
- **Scout:** follows one player all match and writes a report at full time

Each mode works in the viewer's language and focuses on their favourite club or player.

**Numbers come from code, words come from agents.** Every statistic shown to a viewer is computed by deterministic code. AI agents only explain facts they were given, and a fact-checker agent enforces that.

All data is **synthetic**: fictional clubs and players, simulated play. No real footage, broadcasts or scraped data are used.

## Status

| Area | State |
|---|---|
| Synthetic match generator | ✅ 10 Hz simulation of 22 players + ball, events, tracking, scenarios, calibration report ([docs/GENERATOR.md](docs/GENERATOR.md)) |
| Live ingest | ✅ Matches stream over WebSocket *while they are simulated*; events are released only once they have happened |
| Live pitch viewer | ✅ React canvas view of the match with scoreboard and clock |
| Intelligence engine, MCP server, agent crew, overlays, Azure deployment | 🚧 Planned, see [docs/PLAN.md](docs/PLAN.md) |

## Quickstart

**Requirements:** [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically), Node.js 20.19+ or 22.12+, and `make`.

```bash
make setup
```

### Watch a live match

Run the live server and the viewer in two terminals:

```bash
make live
```

```bash
make viewer
```

Then open <http://localhost:5173/?speed=4>. Match options go in the URL:

| Option | Default | Values |
|---|---|---|
| `home`, `away` | `ashenford`, `greywater` | `ashenford`, `calderbrook`, `greywater`, `kingsmere`, `lowmarsh`, `port-alder`, `ravenholm`, `sableport` |
| `seed` | `42` | 0 to 2147483647; the same seed replays the same match |
| `scenario` | `open` | `open`, `late-comeback`, `red-card-chaos`, `early-goal` |
| `speed` | `1` | up to `20` (× real time) |
| `half_length` | `45` | `1`–`45` minutes, handy for quick demos |

Example: <http://localhost:5173/?home=kingsmere&away=lowmarsh&scenario=late-comeback&speed=10>

### Generate match files

```bash
make gen SEED=7 HOME_CLUB=kingsmere AWAY_CLUB=ravenholm
```

```bash
make batch
```

`make gen` writes `meta.json`, `events.jsonl`, `tracking.jsonl.gz` and `validation.json` to `data/matches/<match_id>/`. `make batch` simulates 20 matches and compares their averages with reference values. The format is documented in [docs/DATASET.md](docs/DATASET.md).

### Develop

```bash
make test
```

```bash
make lint
```

`make fmt` fixes formatting. CI (GitHub Actions) runs ruff, the format check and pytest on every PR.

## Repository layout

```
backend/
  pitchside/schemas.py     shared data contracts (Pydantic)
  pitchside/generator/     synthetic match simulator, scenarios, validation
  pitchside/live/          LiveFeed + WebSocket server (/ws/match)
  tests/
frontend/                  React + Vite + TypeScript live viewer
docs/                      plan, generator and dataset docs
```

## Docs

- [docs/PLAN.md](docs/PLAN.md): architecture, agents, Azure services, scope and roadmap
- [docs/GENERATOR.md](docs/GENERATOR.md): how the simulator works, calibration, trade-offs and known limits
- [docs/DATASET.md](docs/DATASET.md): files, schemas, coordinates and event types

## Built with

Python (uv, NumPy, Pydantic, FastAPI, uvicorn), React, Vite and TypeScript. Planned on Azure: Microsoft Foundry, Microsoft Agent Framework, MCP, Container Apps, Event Hubs, Web PubSub, Cosmos DB and AI Translator. Third-party dependencies are permissively licensed (MIT, BSD or Apache 2.0).
