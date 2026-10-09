"""Write a simulated match to disk."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from pitchside.schemas import MatchMeta

from .sim import GENERATOR_VERSION, MatchSim


def match_meta(sim: MatchSim) -> MatchMeta:
    home, away = sim.teams
    return MatchMeta(
        match_id=sim.match_id,
        seed=sim.seed,
        competition="Pitchside Synthetic League",
        venue=f"{home.name} Stadium",
        home=home,
        away=away,
        scenario=sim.scenario.to_dict(),
        final_score={"home": sim.score[0], "away": sim.score[1]},
        generator_version=GENERATOR_VERSION,
    )


def iter_tracking_lines(sim: MatchSim):
    """Yield one JSON line per frame, listing only players on the pitch."""
    for i, t in enumerate(sim._rec_t):
        roster = sim.rosters[sim._rec_roster[i]]
        pos, speed, active = sim._rec_pos[i], sim._rec_speed[i], sim._rec_active[i]
        players = ",".join(
            f'{{"id":"{roster[k]}","x":{pos[k, 0]:.2f},"y":{pos[k, 1]:.2f},"speed":{speed[k]:.2f}}}'
            for k in range(22)
            if active[k]
        )
        b = sim._rec_ball[i]
        live = "true" if sim._rec_live[i] else "false"
        yield (
            f'{{"t":{t:.1f},"period":{sim._rec_period[i]},"live":{live},'
            f'"ball":[{b[0]:.2f},{b[1]:.2f},{b[2]:.2f}],"players":[{players}]}}\n'
        )


def write_match(sim: MatchSim, out_root: Path, tracking: bool = True) -> Path:
    out = Path(out_root) / sim.match_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "meta.json").write_text(match_meta(sim).model_dump_json(indent=2))
    with (out / "events.jsonl").open("w") as f:
        for ev in sim.events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    if tracking:
        with gzip.open(out / "tracking.jsonl.gz", "wt", compresslevel=6) as f:
            f.writelines(iter_tracking_lines(sim))
    return out
