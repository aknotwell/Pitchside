"""Fit simulator parameters to licensed open-data targets and write a realism report.

    uv run python -m matchlens.calibrate.fit [--rounds 3] [--seeds 12] [--workers 16]

Writes ``src/matchlens/sim/fitted_params.json`` (parameters + aggregate targets only) and
``docs/REALISM.md``. The agent-based engine produces events and tracking together, so every
evaluation runs full matches (shortened periods, counts scaled to 90 minutes) in parallel.

Two kinds of target:

* **SkillCorner** (A-League 2024/25 open data, MIT): passes, pass completion, shots, pass
  length, distance, peak speed and sprints. These carry most of the weight.
* **Typical match values** (goals, fouls, cards, won tackles, throw-ins, ball in play): broad
  professional-football norms used as soft guard rails so the fit cannot buy SkillCorner
  accuracy with an absurd game. They are not from SkillCorner and the report says so.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from pathlib import Path

import numpy as np

from matchlens.calibrate.real import SOURCE, targets
from matchlens.contracts import HALF_LENGTH
from matchlens.sim.engine import MatchEngine
from matchlens.sim.params import DEFAULT_PARAMS, load_params, params_version

FITTED = Path("src/matchlens/sim/fitted_params.json")
REPORT = Path("docs/REALISM.md")
SPRINT_MPS = 25.2 / 3.6  # SkillCorner sprint threshold
PERIOD_MIN = 15.0

# Soft guard rails: (value, weight). Typical top-flight values per match, both teams.
NORMS = {
    "goals_per_match": (2.8, 1.0),
    "fouls_per_match": (23.0, 0.5),
    "cards_per_match": (3.8, 0.1),
    "tackles_won_per_match": (38.0, 0.4),
    "throw_ins_per_match": (40.0, 0.4),
    "ball_in_play_share": (0.57, 0.5),
    "header_share_of_shots": (0.17, 0.5),
    "clearances_per_match": (40.0, 0.5),
    "outside_box_shot_share": (0.38, 0.6),
}
SC_WEIGHTS = {"passes_per_match": 1.0, "pass_completion": 6.0, "shots_per_match": 1.0, "pass_length_mean_m": 1.0,
              "distance_km_per90_median": 1.0, "psv99_kmh_median": 1.0, "sprints_per90_median": 0.7}

KNOBS: dict[str, tuple[float, float]] = {
    "decision_s[1]": (0.8, 2.4),
    "carry_s[1]": (0.9, 2.6),
    "shot_bias": (0.05, 1.4),
    "long_shot_pref": (0.0, 0.15),
    "pass_error_m": (0.3, 1.8),
    "intercept_prob": (0.08, 0.5),
    "pass_len_penalty_per_m": (0.001, 0.02),
    "carry_keep_logit": (-1.0, 2.0),
    "tackle_rate_per_s": (0.05, 0.8),
    "foul_share_of_tackles": (0.05, 0.45),
    "card_share_of_fouls": (0.02, 0.3),
    "jog_max_mps": (3.5, 6.5),
    "hard_run_mps": (5.8, 7.4),
    "drift_m": (0.0, 8.0),
    "run_prob_per_s": (0.004, 0.05),
    "max_speed_mps[1]": (8.8, 10.4),
    "steer_gain": (0.5, 1.8),
    "clear_value": (0.0, 0.03),
    "take_on_prob": (0.05, 0.6),
    "press_effort": (0.5, 0.95),
    "header_chance": (0.05, 0.6),
    "crowd_tackle_boost": (0.0, 2.0),
    "turnover_weight": (0.2, 2.0),
    "box_space_shot_bonus": (0.0, 0.12),
    "long_pass_cost": (0.0, 0.03),
    "clear_cost": (0.0, 0.04),
    "pass_value_discount": (0.7, 1.4),
    "control_base": (0.8, 0.97),
    "box_carry_risk": (0.0, 0.35),
    "box_tackle_factor": (0.4, 1.6),
    "restart_scale": (0.7, 2.0),
}


# ---------------------------------------------------------------- one match
def match_stats(args: tuple[dict, int, float]) -> dict:
    params, seed, period_min = args

    class Logged(MatchEngine):
        def _step(self, t: float) -> None:
            super()._step(t)
            self.live_frames += self.state != "dead"

    eng = Logged(seed, params={**params, "period_min": period_min})
    eng.live_frames = 0
    ts, _, pl, ids = eng.simulate_tracking()
    ev = eng.events
    # SkillCorner counts player possessions ending in a pass: restart deliveries (throw-ins, goal kicks,
    # corners, free kicks, kick-offs) are not player possessions, so they are left out here too.
    all_passes = [e for e in ev if e.type == "pass"]
    ps = [e for e in all_passes if not e.qualifiers.get("set_piece")]
    count = lambda kind: sum(1 for e in ev if e.type == kind)  # noqa: E731
    out = {
        "passes": len(ps), "completion": float(np.mean([e.outcome == "complete" for e in ps])) if ps else 0.0,
        "pass_lengths": [float(e.qualifiers["length_m"]) for e in ps],
        "shots": count("shot"), "headers": sum(1 for e in ev if e.type == "shot" and e.qualifiers.get("body_part") == "head"),
        "goals": sum(1 for e in ev if e.type == "shot" and e.outcome == "goal"),
        "fouls": count("foul"), "cards": count("card"), "tackles": count("tackle"),
        "outside": sum(1 for e in ev if e.type == "shot" and not (
            (1 if e.team_id == "h" else -1) * (e.x or 0) > HALF_LENGTH - 16.5 and abs(e.y or 0) < 20.16)),
        "clearances": sum(1 for e in all_passes if e.qualifiers.get("kind") == "clearance"),
        "throw_ins": sum(1 for e in ev if e.type == "ball_out" and e.qualifiers.get("restart") == "throw_in"),
        "in_play": eng.live_frames / max(1, len(ts)), "minutes": 2 * period_min,
    }
    fps = eng.fps
    dist90, psv, sprints90 = [], [], []
    for j in range(len(ids)):
        if eng.players[j].position == "GK":
            continue
        xy = pl[:, j, :].astype(float)
        on = ~np.isnan(xy[:, 0])
        minutes = on.sum() / fps / 60
        if minutes < 0.8 * 2 * period_min:
            continue  # full-match outfield players only
        xy1 = xy[::fps]  # 1 Hz samples, like a broadcast tracking feed
        v1 = np.linalg.norm(np.diff(xy1, axis=0), axis=1)
        v1 = v1[~np.isnan(v1)]
        dist90.append(v1.sum() / minutes * 90 / 1000)
        psv.append(float(np.percentile(v1, 99.9)) * 3.6)
        fast = v1 >= SPRINT_MPS
        sprints90.append(int(np.sum(fast[1:] & ~fast[:-1])) / minutes * 90)
    out.update(dist90=dist90, psv=psv, sprints90=sprints90)
    return out


def evaluate(params: dict, seeds: list[int], pool: ProcessPoolExecutor, period_min: float = PERIOD_MIN) -> dict:
    rows = list(pool.map(match_stats, [(params, s, period_min) for s in seeds]))
    scale = 90 / (2 * period_min)
    m = lambda k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    pool_ = lambda k: [x for r in rows for x in r[k]]  # noqa: E731
    return {
        "passes_per_match": m("passes") * scale, "pass_completion": m("completion"),
        "shots_per_match": m("shots") * scale, "pass_length_mean_m": float(np.mean(pool_("pass_lengths"))),
        "distance_km_per90_median": float(np.median(pool_("dist90"))), "psv99_kmh_median": float(np.median(pool_("psv"))),
        "sprints_per90_median": float(np.median(pool_("sprints90"))),
        "goals_per_match": m("goals") * scale, "fouls_per_match": m("fouls") * scale, "cards_per_match": m("cards") * scale,
        "tackles_won_per_match": m("tackles") * scale, "throw_ins_per_match": m("throw_ins") * scale,
        "ball_in_play_share": m("in_play"),
        "header_share_of_shots": max(0.01, m("headers") / max(1e-9, m("shots"))),
        "clearances_per_match": m("clearances") * scale,
        "outside_box_shot_share": max(0.01, m("outside") / max(1e-9, m("shots"))),
        "_pass_lengths": pool_("pass_lengths"), "_psv99": pool_("psv"),
    }


# ---------------------------------------------------------------- fitting
def loss(st: dict, tgt: dict) -> float:
    total = sum(w * ((st[k] - tgt[k]) / tgt[k]) ** 2 for k, w in SC_WEIGHTS.items())
    total += sum(w * ((st[k] - v) / v) ** 2 for k, (v, w) in NORMS.items())
    return total


def get(p: dict, knob: str) -> float:
    if knob.endswith("]"):
        name, i = knob[:-3], int(knob[-2])
        return float(p[name][i])
    return float(p[knob])


def set_(p: dict, knob: str, value: float) -> dict:
    if knob.endswith("]"):
        name, i = knob[:-3], int(knob[-2])
        lst = list(p[name])
        lst[i] = value
        if i == 1 and lst[1] < lst[0] + 0.1:
            lst[1] = lst[0] + 0.1
        p[name] = lst
    else:
        p[knob] = value
    return p


def coordinate_search(params, evaluate_fn, tgt, rounds: int) -> tuple[dict, dict, float]:
    best = deepcopy(params)
    best_stats = evaluate_fn(best)
    best_loss = loss(best_stats, tgt)
    print(f"  start loss {best_loss:.4f}", flush=True)
    for r in range(rounds):
        for knob, (lo, hi) in KNOBS.items():
            cur = get(best, knob)
            step = (hi - lo) * (0.25 / (r + 1))
            for cand in (cur - step, cur + step):
                cand = round(min(hi, max(lo, cand)), 4)
                if cand == cur:
                    continue
                trial = set_(deepcopy(best), knob, cand)
                st = evaluate_fn(trial)
                lo_ = loss(st, tgt)
                if lo_ < best_loss:
                    best, best_stats, best_loss = trial, st, lo_
                    print(f"  round {r} {knob}={cand:.4f} -> loss {lo_:.4f}", flush=True)
                    break
    return best, best_stats, best_loss


def ks(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic (0 = identical distributions)."""
    a, b = np.sort(a), np.sort(b)
    grid = np.concatenate([a, b])
    return float(np.max(np.abs(np.searchsorted(a, grid, "right") / len(a) - np.searchsorted(b, grid, "right") / len(b))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--warm", action="store_true", help="start from the current fitted parameters")
    a = ap.parse_args()
    t0 = time.time()
    print("loading targets from", SOURCE, flush=True)
    real = targets()
    ev_t, ph_t = real["events"], real["physical"]
    tgt = {**{k: ev_t[k] for k in ("passes_per_match", "pass_completion", "shots_per_match", "pass_length_mean_m")},
           **{k: ph_t[k] for k in ("distance_km_per90_median", "psv99_kmh_median", "sprints_per90_median")}}

    fit_seeds = list(range(101, 101 + a.seeds))
    hold_seeds = list(range(901, 901 + a.seeds))
    with ProcessPoolExecutor(a.workers) as pool:
        print("fitting…", flush=True)
        start = load_params() if a.warm else deepcopy(DEFAULT_PARAMS)
        params, _, _ = coordinate_search(start, lambda p: evaluate(p, fit_seeds, pool), tgt, a.rounds)
        print("validating on held-out seeds…", flush=True)
        after = evaluate(params, hold_seeds, pool)
        before = evaluate(deepcopy(DEFAULT_PARAMS), hold_seeds, pool)
    params["version"] = "fit-skillcorner-agents"
    version = params_version(params)

    def agg(d: dict) -> dict:
        return {k: ([round(x, 3) for x in v] if isinstance(v, list) else round(v, 4)) for k, v in d.items()
                if not k.startswith("_")}

    FITTED.write_text(json.dumps({
        "params_version": version, "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": SOURCE, "targets": {"events": agg(ev_t), "physical": agg(ph_t)},
        "norms": {k: v for k, (v, _) in NORMS.items()}, "held_out": agg(after),
        "params": {k: v for k, v in params.items() if k not in ("script",)},
    }, indent=1) + "\n")

    def err(real_v: float, sim_v: float) -> str:
        return f"{100 * (sim_v - real_v) / real_v:+.0f}%"

    sc_rows = [
        ("Passes per match", "passes_per_match", "{:.0f}"),
        ("Pass completion", "pass_completion", "{:.1%}"),
        ("Shots per match", "shots_per_match", "{:.1f}"),
        ("Mean pass length (m)", "pass_length_mean_m", "{:.1f}"),
        ("Distance per 90, median (km)", "distance_km_per90_median", "{:.2f}"),
        ("Peak speed (PSV-99), median (km/h)", "psv99_kmh_median", "{:.1f}"),
        ("Sprints per 90, median", "sprints_per90_median", "{:.1f}"),
    ]
    norm_rows = [
        ("Goals per match", "goals_per_match", "{:.1f}"),
        ("Fouls per match", "fouls_per_match", "{:.0f}"),
        ("Yellow cards per match", "cards_per_match", "{:.1f}"),
        ("Tackles won per match", "tackles_won_per_match", "{:.0f}"),
        ("Throw-ins per match", "throw_ins_per_match", "{:.0f}"),
        ("Ball in play", "ball_in_play_share", "{:.0%}"),
        ("Headers, share of shots", "header_share_of_shots", "{:.0%}"),
        ("Clearances per match", "clearances_per_match", "{:.0f}"),
        ("Shots from outside the box", "outside_box_shot_share", "{:.0%}"),
    ]
    md = [
        "# Synthetic data realism report", "",
        f"Generated by `python -m matchlens.calibrate.fit` on {time.strftime('%Y-%m-%d', time.gmtime())} "
        f"(params `{version}`, {time.time() - t0:.0f} s).", "",
        f"**Reference:** {SOURCE}. A-League 2024/25: {ev_t['matches']} matches of dynamic events, plus season "
        f"physical aggregates for {ph_t['players']} outfield players. Raw files are downloaded at fit time and never "
        "committed. This report and `fitted_params.json` contain aggregates only.", "",
        "**Engine:** agent-based. Every 0.1 s the engine moves 22 players and the ball together; pressure, tackles, "
        "interceptions and chances come from positions, and each player's traits and role shape how they move and "
        "decide. See `src/matchlens/sim/engine.py`.", "",
        "**Method:** the simulator stays fully synthetic (a fictional league, seeded). Calibration tunes "
        f"{len(KNOBS)} engine parameters by coordinate search, minimising weighted squared relative error. Each "
        f"evaluation runs {a.seeds} matches with {PERIOD_MIN:.0f}-minute halves (counts scaled to 90 minutes). "
        "Numbers below are on **held-out seeds** that were never used in fitting.", "",
        "## Against SkillCorner", "",
        "| Metric | Real | Hand-set (before) | Calibrated (after) | Error after |", "|---|---|---|---|---|",
    ]
    for name, k, fmt in sc_rows:
        md.append(f"| {name} | {fmt.format(tgt[k])} | {fmt.format(before[k])} | **{fmt.format(after[k])}** | "
                  f"{err(tgt[k], after[k])} |")
    md += ["", "## Against typical match values (guard rails, not SkillCorner)", "",
           "| Metric | Typical | Hand-set (before) | Calibrated (after) |", "|---|---|---|---|"]
    for name, k, fmt in norm_rows:
        md.append(f"| {name} | {fmt.format(NORMS[k][0])} | {fmt.format(before[k])} | **{fmt.format(after[k])}** |")
    md += ["", "Distribution distance (two-sample Kolmogorov–Smirnov statistic, 0 = identical):", "",
           "| Distribution | Before | After |", "|---|---|---|",
           f"| Pass length | {ks(before['_pass_lengths'], ev_t['_pass_lengths']):.3f} | "
           f"**{ks(after['_pass_lengths'], ev_t['_pass_lengths']):.3f}** |",
           f"| Player peak speed | {ks(before['_psv99'], ph_t['_psv99']):.3f} | **{ks(after['_psv99'], ph_t['_psv99']):.3f}** |",
           "", "Definitions follow the source where possible: passes are player possessions ending in a pass (open play; "
           "restart deliveries such as throw-ins and goal kicks are not player possessions in SkillCorner either); a sprint "
           "is ≥ 25.2 km/h for at least one 1 Hz sample; peak speed is the 99.9th percentile of 1 s speeds per player "
           "(PSV-99 analogue).", "",
           "Known gaps: event definitions are not one-to-one with the source (for example, SkillCorner on-ball "
           "engagements vs our pressure events); there is no offside law; team shapes are rule-based rather than learned.",
           ""]
    REPORT.write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
