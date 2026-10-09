# Pitchside — guidance for Claude

Pitchside is a **hackathon entry**. The goal is to win, so every piece of work should earn points on the judging criteria below and stay inside the hackathon rules. When a request is ambiguous, pick the option that scores better on these criteria, and say so.

Full plan: `docs/PLAN.md` (architecture, owners, timeline, scope tiers, demo storyboard, compliance matrix). Generator: `docs/GENERATOR.md`, data format: `docs/DATASET.md`.

## What we're building
An AI pipeline that turns **synthetic** football match events into explainable match intelligence: **ingest → interpret → explain → render → personalize**. Our angle is *one match, many broadcasts*. The same live match produces different experiences for a casual fan, an analyst, an academy player ("Learn mode", our differentiator) and a scout, in the viewer's language and focused on their favourite club or player.

## Hard rules (never break)
- **Synthetic data only.** The app runs on data from our generator. No real match footage, broadcast clips or scraped data. Real open datasets (StatsBomb, Metrica) may only be used to *calibrate* the generator, with attribution.
- **Rights to everything we use.** Before adding any third-party code, dataset, model, asset or video, check its licence and note it. Don't copy code from sources without a compatible licence.
- **Fictional clubs and players only.** Never use real club, player, league or broadcaster names, logos or likenesses.
- **Numbers come from code, words come from agents.** Every statistic shown to a viewer is computed by deterministic code. LLM output may only use facts it was given. Never let a model invent a stat.

## Judging criteria (20% each)
Check new work against these. If a task doesn't clearly help at least one of them, raise it before spending time on it.

1. **Technological implementation**: creative, optimised synthetic data; quality engineering (tests, CI, IaC, docs); strong use of the hero technologies: Microsoft Foundry, Microsoft Agent Framework, MCP / Azure MCP, GitHub Copilot Agent Mode, GitHub CLI/Actions, Azure App & AI Services, Azure databases, Fabric.
2. **Agentic design & innovation**: real multi-agent orchestration, our own MCP server, reflection (fact-checker), planning (Producer), agent-generated scenarios, human in the loop. Agents should do genuine work, not wrap one prompt.
3. **Real-world impact**: production-shaped (timed overlay JSON, latency and cost measured, deployable with `azd up`), meaningful for broadcasters, clubs, scouts and fans.
4. **User experience & presentation**: polished, intuitive UI with a balance of frontend and backend; a demo video that shows the value in the first 30 seconds.
5. **Category adherence**: every brief requirement is covered (see the compliance matrix in `docs/PLAN.md`): live ingest, stats (player tags, speed/distance thresholds, pass distance/accuracy/difficulty, ball and shot speed), explanations (control vs chaos, pressure, rhythm), narratives and recaps, personalisation, multi-language, synthetic datasets.

## How to work
- **Protect the demo.** Follow the scope tiers in `docs/PLAN.md`: finish the Must tier before Should, and Should before Could. Prefer a working thin slice end to end over a deep but disconnected component. Feature freeze is 3 days before the deadline.
- **Keep the brief's language visible.** Name features, docs and UI in the brief's terms (ingest, interpret, explain, render, personalize; key moments; overlays) so judges can map them to criteria.
- **Measure claims.** Back impact claims with numbers (validation report, latency, cost per match, eval scores). Don't write "realistic", "fast" or "production-ready" without evidence.
- **Be honest about limits.** Keep known gaps and trade-offs in the docs up to date (`docs/GENERATOR.md` has the pattern). Judges trust stated limits more than hidden ones.
- **Azure first** for cloud pieces, using the services in `docs/PLAN.md`. Every cloud dependency needs a local stand-in, so the app runs offline with `PITCHSIDE_LLM=template`.
- **Document as you go.** New components get a short doc in `docs/` (what works, how to run, trade-offs, limits). Note when GitHub Copilot or other AI tools were used to build something; it counts for the hero-technology criterion.

## Engineering conventions
- Python 3.12 via `uv` in `backend/`; React + Vite + TypeScript in `frontend/` (planned). Shared contracts live in `backend/pitchside/schemas.py`; change them deliberately and update the TS types and docs too.
- Commands (from repo root): `make gen`, `make batch`, `make tune ARGS="..."`, `make test`, `make lint`, `make fmt`. CI runs ruff, the formatter check and pytest; keep it green.
- Work on a branch and open a PR; don't commit directly to `main`.
- The generator is deterministic: the same seed and code produce the same match. When refactoring it, compare output before and after for a few seeds. After changing tuning constants in `sim.py`, run `make batch` and update the calibration numbers in the docs.
- Events contain their outcome at their start time `t`. Anything replaying events live must release them at `t + duration`.
- Treat anything produced by an LLM or a user (scenarios, profiles, prompts) as untrusted input and validate it (see `generator/scenarios.py`).
- Generated data in `data/matches/` is git-ignored; never commit large generated files.

## Before saying a task is done
- Tests and lint pass (`make test`, `make lint`).
- Which judging criterion it serves is clear, and the relevant doc is updated.
- Any third-party code, data or asset added is licensed and noted.
- Nothing shown to viewers relies on a number the LLM produced.
