# Pitchside Synthetic Match Dataset

Every match is fully synthetic: fictional clubs, fictional players, simulated play. No real footage or data is used.

## Generating data

```bash
cd backend
uv run pitchside-gen match --seed 42 --home ashenford --away greywater      # one match
uv run pitchside-gen match --seed 7 --scenario late-comeback                 # scripted storyline
uv run pitchside-gen batch --seeds 1-20                                      # calibration report
```

Or from the repo root: `make gen`, `make batch`. A 90-minute match takes about 10 s to simulate plus a few seconds to write; `batch` runs matches in parallel on all CPU cores (`--workers N` to limit it). The same seed always produces the same match.

## How it's made

The generator is a 10 Hz simulation (`backend/pitchside/generator/sim.py`) with two layers:

- **Decisions:** the player on the ball weighs passing, carrying, dribbling, shooting and clearing. Each option is scored by expected threat gained against the risk of losing the ball, and the player's decision rating sets how close they get to the best choice. Outcomes are rolled from probability models (`models.py`):
  - pass success depends on distance, passing-lane and receiver pressure, lofted vs ground, zone and player passing;
  - xG depends on distance, angle, pressure, defenders in the shooting cone and headers.
- **Movement:** players steer towards formation anchors that shift with the ball, possession and team tactics. Some players have a job instead: the carrier, goal-side pressers, markers near the box, the pass receiver, runners in behind. Top speed comes from pace and fatigue, and acceleration is capped.

Events are recorded from the simulation, so every pass starts where the ball actually was, and ball, shot and player speeds can be recovered from the tracking data.

## Files

Each match is written to `data/matches/<match_id>/`:

| File | Contents |
|---|---|
| `meta.json` | `MatchMeta`: clubs, squads with ratings, formations, tactics, seed, scenario, final score |
| `events.jsonl` | One `Event` per line, in time order |
| `tracking.jsonl.gz` | One `TrackingFrame` per line at 10 fps (~56,000 frames, ~11 MB) |
| `validation.json` | Each metric checked against a plausible range for a single match |

Schemas are the Pydantic models in `backend/pitchside/schemas.py`.

### Coordinates and time
- Metres on a 105 × 68 pitch. Origin is the bottom-left corner of the home team's first-half defending end, so home attacks towards x = 105 in the first half and teams switch ends at half-time.
- `t` is seconds since kick-off and increases continuously across both periods. `clock` is the broadcast clock (`"45+2:13"`).

### Events

Common fields: `id, t, period, clock, type, team, player, possession, start, end, duration, outcome, extra`.

| Type | Outcome | Notable `extra` fields |
|---|---|---|
| `pass` | `complete` / `incomplete` / `out` / `offside` | `recipient, length, height (ground/high), kind (short/long/through_ball/switch/cross), speed (m/s), play_pattern, under_pressure` |
| `shot` | `goal` / `saved` / `blocked` / `off_target` | `xg, speed (m/s), body (foot/head), distance, play_pattern, under_pressure, end_z` |
| `carry` | — | (start, end, duration) |
| `dribble` | `complete` / `incomplete` | `past` |
| `tackle` | `won` / `lost` | `on` |
| `pressure` | — | `on` |
| `interception`, `clearance`, `block`, `ball_recovery`, `save` | — | |
| `foul` | — | `on` |
| `card` | `yellow` / `second_yellow` / `red` | |
| `offside`, `goal` | — | goal: `assist, shot, score` |
| `possession_change` | cause (`tackle`, `interception`, `throw_in`, …) | |
| `sub` | — | `off, on` |
| `period` | `start` / `end` | `score` on end |

`play_pattern` is one of `open_play, kick_off, throw_in, goal_kick, corner, free_kick, penalty`.

The simulator's internal probabilities (e.g. pass success chance) are deliberately **not** written out. The intelligence engine has to compute its own metrics from the data.

## Clubs

Eight fictional clubs with different styles (`teams.py`). Squads are generated from the club id, so they're the same in every match.

| Club | Formation | Strength | Style |
|---|---|---|---|
| Kingsmere City | 4-3-3 | 83 | High press, possession |
| Ashenford Athletic | 4-3-3 | 80 | High press, high tempo |
| Greywater Rovers | 4-2-3-1 | 77 | Balanced |
| Port Alder FC | 4-2-3-1 | 75 | Balanced |
| Calderbrook United | 4-3-3 | 74 | Quick tempo |
| Lowmarsh Albion | 3-5-2 | 72 | Low block, direct |
| Sableport Wanderers | 4-4-2 | 71 | Narrow, direct |
| Ravenholm Town | 4-4-2 | 70 | Low block, direct |

Match state also changes tactics: teams chasing the game after 60' press higher and play more directly, and teams protecting a lead after 70' sit deeper.

## Scenarios

Built-in: `open`, `late-comeback`, `red-card-chaos`, `early-goal` (`scenarios.py`). A scripted event is armed at its minute and happens at the next chance: a goal is the team's next shot, and a red card comes at the team's next challenge. When a scenario scripts goals, only scripted goals are scored, so the storyline's scoreline is exact. Everything else stays simulated. Custom scenarios are JSON:

```json
{"name": "derby-drama", "events": [{"minute": 89, "type": "goal", "team": "away"}]}
```

## Calibration

`pitchside-gen batch` compares multi-match averages with the reference values in `benchmarks.py`. The current reference values are **approximate typical top-flight figures, used as placeholders until the Premier League 2025-26 season averages are added.**

20-match batch (seeds 1–20, random club pairings):

| Metric (per match) | Sim | Reference |
|---|---|---|
| Goals | 2.85 | 2.8 |
| Shots / on target | 23.9 / 8.8 | 25.5 / 8.5 |
| xG | 3.19 | 3.0 |
| Passes / accuracy | 870 / 80% | 900 / 83% |
| Corners | 9.1 | 10.2 |
| Fouls / yellow / red | 17.1 / 3.9 / 0.2 | 21 / 3.9 / 0.1 |
| Offsides | 3.3 | 3.4 |
| Tackles won / interceptions | 37.7 / 21.9 | 33 / 17 |
| Ball in play | 56.2 min | 56 min |
| Distance per outfield player per 90 / per team | 9.9 km / 108 km | 10.8 km / 112 km |

All 16 metrics are within tolerance.

### Known gaps
- **Pressure events** (~950 per match) are far higher than event-data providers record, because ours fire whenever a defender comes within 2.6 m.
- **Sprints** (≥ 25.2 km/h for 1 s, about 6 per outfield player) are lower than real matches.
- **Possession** stays close to 50/50; stronger clubs don't dominate the ball as much as they would in real football.
- Set pieces are simplified (no wall, no near-post/far-post routines) and there's no VAR or added time tied to stoppages.
