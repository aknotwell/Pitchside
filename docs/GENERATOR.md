# Synthetic Match Generator

Status, design trade-offs, limitations and next steps for `backend/pitchside/generator/`.
For the output format (files, schemas, event types), see [DATASET.md](DATASET.md).

## What's working now

| Area | Status |
|---|---|
| Full 90-minute simulation | ✅ 10 Hz, both halves with stoppage time, ~10–15 s per match |
| Events | ✅ Passes, carries, dribbles, shots, saves, blocks, tackles, interceptions, clearances, recoveries, pressures, fouls, cards, offsides, subs, goals with assists, possession changes |
| Tracking | ✅ All 22 players + ball (x, y, z) every 0.1 s, with player speeds and a `live` flag for dead-ball time |
| Restarts | ✅ Kick-offs, throw-ins, goal kicks, corners, free kicks (incl. direct shots), penalties |
| Match flow | ✅ Fatigue, 3–5 substitutions per team, yellow / second yellow / red cards (team plays on with 10), injury stoppages, game-state tactics (chasing teams press and go direct) |
| Clubs | ✅ 8 fictional clubs, 4 formations, tactical sliders, fixed squads of 18 with position-based ratings |
| Scenarios | ✅ 4 built-in storylines + custom JSON; scripted goals and red cards with exact scorelines |
| Determinism | ✅ Same seed + same code version = identical match |
| Validation | ✅ Per-match plausibility report, and a multi-match batch report against reference averages: **16/16 metrics within tolerance** over 20 matches |
| Engineering | ✅ CLI (`pitchside-gen match` / `batch`), Makefile, 10 tests, ruff, GitHub Actions CI |

## Design trade-offs

### 1. Hybrid simulation instead of full physics or learned agents
Players aren't trained agents and the ball doesn't follow real physics. A **decision layer** (the player on the ball picks an action by scoring options) runs on top of a **movement layer** (everyone steers towards shifted formation positions).
- **Gained:** fast (seconds per match), explainable, easy to tune, deterministic.
- **Given up:** emergent tactics. Off-ball movement is rule-based (anchors, pressers, markers, runners), so teams don't build overloads or third-man runs or adapt their shape intelligently.

### 2. Statistical shortcuts to hit real-world averages
Some behaviour couldn't be made realistic from spatial rules alone within the time available, so explicit tuning knobs fill the gap. They're all named constants at the top of `sim.py`:

| Knob | What it stands in for |
|---|---|
| `FINAL_THIRD_PENALTY`, `BOX_PENALTY`, `CROSS_PENALTY` | Defensive density in the final third and box that the positioning model doesn't fully create |
| `SHOT_OPENING` | Only some touches near goal are real shooting chances (body shape, balance, angle) |
| `BOX_TACKLE_BOOST`, `BOX_FOUL_FACTOR` | Last-ditch defending in the box, and defenders avoiding penalties |
| `OFFSIDE_RISK` | Offside timing errors (partly a random call, not purely positional) |
| `DEFLECTION_CORNER`, `CORNER_FROM_BOX_DEFENDING` | Deflections that put the ball behind for corners |
| `INJURY_RATE`, `DEAD_TIME` | Treatment, time-wasting and set-up time, so ball-in-play time is realistic |

- **Gained:** aggregates that match real football (goals, xG, passes, cards, ball-in-play time…).
- **Given up:** some stats are right *on average* for partly artificial reasons. A corner or offside may not have a fully "physical" cause in the tracking data.

### 3. Outcomes are decided when an action starts
A pass's success, an interception point or a shot's result is rolled when the ball is played, then the flight plays out. A completed pass "homes" onto the receiver as they move.
- **Gained:** simple, consistent events.
- **Given up:** passes can bend slightly mid-flight, and each event already contains its outcome at its start time `t`. **Anything replaying events "live" must release an event at `t + duration`**, otherwise it leaks the future (e.g. a goal shown as the shot is struck).

### 4. Scripted scenarios override natural goals
When a scenario scripts goals, any other shot that would have gone in becomes a save, so the scoreline matches the script exactly.
- **Gained:** reliable demo storylines.
- **Given up:** scenario matches have slightly more saves than normal, and goals no longer track xG. A scripted goal only happens at the team's *next shot* after its minute; if that team doesn't shoot before full time, the goal never happens (unlikely, but no guarantee).

### 5. One calibration for all clubs
Tuning was done on mixed club pairings with a single set of constants.
- **Gained:** league-wide averages are right.
- **Given up:** team strength doesn't show strongly enough. Possession stays close to 50/50, and the stronger club doesn't dominate as it would in real football.

### 6. Simple storage format
Tracking is gzipped JSON Lines (~11 MB per match), not Parquet or Arrow.
- **Gained:** human-readable, streamable line by line, no extra dependencies.
- **Given up:** larger files and slower bulk loading for analytics.

### 7. Validated on averages only
The batch report compares **means** across matches with reference values.
- **Given up:** no checks yet on distributions (e.g. how often 0-0 or 5-3 happens), per-team splits, or relationships (e.g. does more possession mean more shots?). Tolerances for rare events (red cards ±100%) are loose because 20 matches is a small sample.

### 8. The internal ground truth stays hidden
The simulator's probabilities (pass success chance, true xG before outcome) are not written to the output. The `xg` field on shots is the only model value exported.
- **Gained:** the intelligence engine has to compute its own metrics, as it would with a real data feed.
- **Given up:** we can't directly score the engine's pass-difficulty model against the generator's truth (could be added as an optional debug export).

## Current limitations

**Stats that are off**
- Pressure events: ~950 per match, far above what data providers record. Ours fire every time a defender comes within 2.6 m.
- Sprints: ~6 per outfield player (≥ 25.2 km/h for 1 s), lower than real matches.
- Possession and dominance: see trade-off 5.
- **Reference values are placeholders**, not Premier League 2025-26 averages. Once the real numbers are in `benchmarks.py`, some stats may need re-tuning.

**Football that isn't modelled**
- Set pieces are simplified: no walls, corners only produce headers or clearances, no short corners or rehearsed routines.
- Goalkeepers don't come off their line, claim crosses spatially or distribute by throwing; they never get substituted.
- No aerial duels, handballs, VAR, advantage played after fouls, injuries that force a substitution, tactical substitutions or formation changes. Extra time and shoot-outs aren't modelled.
- Players have no body orientation or preferred foot.

**Tracking artefacts**
- A few teleports cause speed spikes: a restart taker who hasn't reached the ball after 8 s is moved onto it, a corner's target attacker snaps to the ball for the header, and players reset to kick-off positions at half-time. The validation report caps per-frame movement to ignore these; **the intelligence engine should do the same** (e.g. ignore speeds > 12 m/s).

**Tooling**
- `batch` runs matches one after another (~3.5 min for 20). The parallel calibration harness used during tuning isn't in the repo yet.
- Any change to the simulator changes every seed's match. `generator_version` in `meta.json` records which version produced a file.

## TODO

### Next up
- [ ] **Match data displayer**: replay a generated match at match speed (1x, with a speed-up option) and show the events as text lines as they happen, e.g. `23:41  Okafor (ASH) → Nakamura, 18 m pass, complete`. It should release each event at `t + duration` (see trade-off 3) and show the running score and clock. Later it can grow a 2D pitch view drawn from the tracking data. This becomes the base for the live overlay and commentary pipeline.
- [ ] Replace the placeholder reference values in `benchmarks.py` with Premier League 2025-26 season averages (and cite the source), then re-run `make batch` and re-tune.

### Realism
- [ ] Bring pressure counts in line with provider definitions (only count actual pressing actions).
- [ ] Raise sprint counts (more high-speed recovery and attacking runs).
- [ ] Make club strength matter more (possession, territory, chance quality).
- [ ] Richer set pieces: walls, short corners, near/far-post deliveries.
- [ ] Goalkeeper behaviour: sweeping, claiming crosses, throws.
- [ ] Remove tracking teleports (walk the taker to the ball; animate the header).

### Data and tooling
- [ ] Live replayer that publishes events at their release time to the ingest pipeline (local queue first, Azure Event Hubs later).
- [ ] Parallel `batch` and move the calibration harness into the repo (`scripts/tune.py`).
- [ ] Distribution and correlation checks in the validation report (scoreline frequencies, per-team splits).
- [ ] Optional Parquet export for tracking data.
- [ ] Optional debug export of the generator's ground truth (true pass probabilities) to test the engine's metrics.
- [ ] Seed search for scenarios ("find a seed where the away team wins 2-1 with a late goal") as an alternative to scripted goals.
- [ ] More scenarios for demos (derby, title decider, wonderkid debut).
