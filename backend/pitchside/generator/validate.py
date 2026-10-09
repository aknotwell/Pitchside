"""Validation report: does a generated match look like real football?

Single matches are checked against plausible ranges; batches of matches are checked
against the season averages in `benchmarks.py`.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from .benchmarks import TARGETS
from .sim import FPS, MatchSim

SPRINT_SPEED = 7.0  # m/s (25.2 km/h)
SPRINT_MIN_FRAMES = FPS  # sustained for at least one second


def match_stats(sim: MatchSim) -> dict:
    ev = sim.events
    types = Counter(e["type"] for e in ev)
    passes = [e for e in ev if e["type"] == "pass"]
    shots = [e for e in ev if e["type"] == "shot"]
    cards = Counter(e["outcome"] for e in ev if e["type"] == "card")
    complete = sum(e["outcome"] == "complete" for e in passes)
    home_passes = sum(e["team"] == "home" for e in passes)

    pos = np.stack(sim._rec_pos)
    speed = np.stack(sim._rec_speed)
    active = np.stack(sim._rec_active)
    period = np.array(sim._rec_period)
    live = np.array(sim._rec_live)

    step = np.linalg.norm(pos[1:] - pos[:-1], axis=2)
    ok = active[1:] & active[:-1] & (period[1:] == period[:-1])[:, None]
    step = np.where(ok, np.minimum(step, 1.2), 0.0)

    roster_idx = np.array(sim._rec_roster[1:])
    dist_by_pid: Counter = Counter()
    frames_by_pid: Counter = Counter()
    for ridx in np.unique(roster_idx):
        rows = roster_idx == ridx
        roster = sim.rosters[ridx]
        d = step[rows].sum(axis=0)
        f = ok[rows].sum(axis=0)
        for k in range(22):
            dist_by_pid[roster[k]] += float(d[k])
            frames_by_pid[roster[k]] += int(f[k])

    gk_ids = {p.id for t in sim.teams for p in t.players if p.position == "GK"}
    per90 = [
        dist_by_pid[pid] / 1000 / (frames_by_pid[pid] / FPS / 60) * 90
        for pid in dist_by_pid
        if pid not in gk_ids and frames_by_pid[pid] >= 60 * 60 * FPS
    ]
    team_km = [float(step[:, t * 11 : (t + 1) * 11].sum()) / 1000 for t in (0, 1)]

    fast = (speed >= SPRINT_SPEED) & active
    sprints = 0
    for k in np.flatnonzero(~sim.is_gk):
        run = 0
        for v in fast[:, k]:
            run = run + 1 if v else 0
            if run == SPRINT_MIN_FRAMES:
                sprints += 1
    match_minutes = len(sim._rec_t) / FPS / 60
    # Per 90 minutes of outfield playing time, so red cards don't skew the average.
    outfield_minutes = float(active[:, ~sim.is_gk].sum()) / FPS / 60

    values = {
        "goals": types["goal"],
        "shots": len(shots),
        "shots_on_target": sum(e["outcome"] in ("goal", "saved") for e in shots),
        "xg": round(sum(e["extra"]["xg"] for e in shots), 2),
        "passes": len(passes),
        "pass_accuracy": round(complete / max(len(passes), 1), 3),
        "corners": sum(e["extra"].get("play_pattern") == "corner" for e in passes),
        "fouls": types["foul"],
        "yellow_cards": cards["yellow"] + cards["second_yellow"],
        "red_cards": cards["red"] + cards["second_yellow"],
        "offsides": types["offside"],
        "tackles": sum(e["type"] == "tackle" and e["outcome"] == "won" for e in ev),
        "interceptions": types["interception"],
        "ball_in_play_min": round(float(live.sum()) / FPS / 60, 1),
        "distance_per_player_km": round(float(np.mean(per90)), 2) if per90 else 0.0,
        "team_distance_km": round(sum(team_km) / 2, 1),
    }
    extra = {
        "score": {"home": sim.score[0], "away": sim.score[1]},
        "possession_home": round(home_passes / max(len(passes), 1), 3),
        "match_minutes": round(match_minutes, 1),
        "sprints_per_outfield_player_90": round(sprints / max(outfield_minutes, 1) * 90, 1),
        "pressures": types["pressure"],
        "dribbles": types["dribble"],
        "carries": types["carry"],
        "events_total": len(ev),
    }
    return {"values": values, "extra": extra}


def single_match_report(sim: MatchSim) -> dict:
    stats = match_stats(sim)
    metrics = {}
    for key, value in stats["values"].items():
        tgt = TARGETS[key]
        lo, hi = tgt.single_match
        metrics[key] = {
            "label": tgt.label,
            "value": value,
            "season_avg": tgt.value,
            "plausible_range": [lo, hi],
            "ok": lo <= value <= hi,
        }
    passed = sum(m["ok"] for m in metrics.values())
    return {
        "match_id": sim.match_id,
        "metrics": metrics,
        "extra": stats["extra"],
        "summary": {"passed": passed, "total": len(metrics)},
    }


def batch_report(all_stats: list[dict]) -> dict:
    metrics = {}
    for key, tgt in TARGETS.items():
        vals = [s["values"][key] for s in all_stats]
        mean = float(np.mean(vals))
        dev = (mean - tgt.value) / tgt.value if tgt.value else 0.0
        metrics[key] = {
            "label": tgt.label,
            "mean": round(mean, 3),
            "std": round(float(np.std(vals)), 3),
            "target": tgt.value,
            "deviation": round(dev, 3),
            "ok": abs(dev) <= tgt.tolerance,
        }
    extras = {
        k: round(float(np.mean([s["extra"][k] for s in all_stats])), 3)
        for k in (
            "possession_home",
            "sprints_per_outfield_player_90",
            "pressures",
            "dribbles",
            "carries",
            "events_total",
        )
    }
    passed = sum(m["ok"] for m in metrics.values())
    return {
        "matches": len(all_stats),
        "metrics": metrics,
        "extra": extras,
        "summary": {"passed": passed, "total": len(metrics)},
    }


def format_batch(report: dict) -> str:
    lines = [f"{'Metric':<42}{'Sim mean':>10}{'Target':>10}{'Dev':>8}  ", "-" * 74]
    for m in report["metrics"].values():
        mark = "ok" if m["ok"] else "OFF"
        lines.append(
            f"{m['label']:<42}{m['mean']:>10.2f}{m['target']:>10.2f}{m['deviation']:>+8.0%}  {mark}"
        )
    lines.append("-" * 74)
    lines.append(
        f"{report['summary']['passed']}/{report['summary']['total']} metrics within tolerance "
        f"over {report['matches']} matches"
    )
    lines.append("Other: " + ", ".join(f"{k}={v}" for k, v in report["extra"].items()))
    return "\n".join(lines)
