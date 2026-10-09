"""Fit simulator parameters to licensed open-data targets and write a realism report.

    uv run python -m matchlens.calibrate.fit

Writes ``src/matchlens/sim/fitted_params.json`` (parameters + aggregate targets only) and
``docs/REALISM.md``. Event parameters are fitted on event-only simulations (fast); physical
parameters on short simulations with full 10 Hz tracking.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path

import numpy as np

from matchlens.calibrate.real import SOURCE, targets
from matchlens.sim.engine import MatchEngine
from matchlens.sim.params import DEFAULT_PARAMS, params_version

FITTED = Path("src/matchlens/sim/fitted_params.json")
REPORT = Path("docs/REALISM.md")
SPRINT_MPS = 25.2 / 3.6  # SkillCorner sprint threshold


# ---------------------------------------------------------------- synthetic statistics
def event_stats(params: dict, seeds: list[int]) -> dict:
    passes, comp, shots, lengths = [], [], [], []
    for s in seeds:
        eng = MatchEngine(s, params=params)
        eng.simulate_events()
        ps = [e for e in eng.events if e.type == "pass"]
        passes.append(len(ps))
        comp.append(sum(e.outcome == "complete" for e in ps) / len(ps))
        shots.append(sum(1 for e in eng.events if e.type == "shot"))
        lengths += [float(e.qualifiers["length_m"]) for e in ps]
    return {"passes_per_match": float(np.mean(passes)), "pass_completion": float(np.mean(comp)),
            "shots_per_match": float(np.mean(shots)), "pass_length_mean_m": float(np.mean(lengths)),
            "_pass_lengths": lengths}


def physical_stats(params: dict, seeds: list[int], period_min: float = 15) -> dict:
    p = {**params, "period_min": period_min}
    dist90, psv, sprints90 = [], [], []
    for s in seeds:
        eng = MatchEngine(s, params=p)
        eng.simulate_events()
        ts, _, pl, ids = eng.simulate_tracking()
        fps = eng.fps
        step = fps  # 1 s displacement -> speed, like a broadcast tracking feed
        for j, pid in enumerate(ids):
            if pid[1:] == "1":  # goalkeepers
                continue
            xy = pl[:, j, :]
            on = ~np.isnan(xy[:, 0])
            minutes = on.sum() / fps / 60
            if minutes < 0.8 * 2 * period_min:
                continue  # full-match outfield players only
            v = np.linalg.norm(xy[step:] - xy[:-step], axis=1)  # metres per second
            v = v[~np.isnan(v)]
            d = np.nansum(np.linalg.norm(np.diff(xy[::step], axis=0), axis=1))
            dist90.append(d / minutes * 90 / 1000)
            psv.append(float(np.percentile(v, 99.9)) * 3.6)
            xy1 = xy[::step]  # 1 Hz samples: a sprint is >= 25.2 km/h for at least one second
            v1 = np.linalg.norm(np.diff(xy1, axis=0), axis=1)
            v1 = v1[~np.isnan(v1)]
            fast = v1 >= SPRINT_MPS
            starts = int(np.sum(fast[1:] & ~fast[:-1]))
            sprints90.append(starts / minutes * 90)
    return {"distance_km_per90_median": float(np.median(dist90)), "psv99_kmh_median": float(np.median(psv)),
            "sprints_per90_median": float(np.median(sprints90)), "_psv99": psv, "_dist90": dist90}


def ks(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic (0 = identical distributions)."""
    a, b = np.sort(a), np.sort(b)
    grid = np.concatenate([a, b])
    return float(np.max(np.abs(np.searchsorted(a, grid, "right") / len(a) - np.searchsorted(b, grid, "right") / len(b))))


# ---------------------------------------------------------------- fitting
WEIGHTS = {"pass_completion": 6.0}  # a ratio near 0.8: weight it so 5 points of accuracy matter


def loss(stats: dict, tgt: dict, keys: list[str]) -> float:
    return sum(WEIGHTS.get(k, 1.0) * ((stats[k] - tgt[k]) / tgt[k]) ** 2 for k in keys)


def coordinate_search(params: dict, knobs: dict[str, tuple[float, float]], evaluate, tgt: dict, keys: list[str],
                      rounds: int = 3) -> tuple[dict, dict]:
    best = deepcopy(params)
    best_stats = evaluate(best)
    best_loss = loss(best_stats, tgt, keys)
    print(f"  start loss {best_loss:.4f}")
    for r in range(rounds):
        for knob, (lo, hi) in knobs.items():
            cur = get(best, knob)
            step = (hi - lo) * (0.25 / (r + 1))
            for cand in (cur - step, cur + step):
                cand = min(hi, max(lo, cand))
                trial = set_(deepcopy(best), knob, cand)
                st = evaluate(trial)
                lo_ = loss(st, tgt, keys)
                if lo_ < best_loss:
                    best, best_stats, best_loss = trial, st, lo_
                    print(f"  round {r} {knob}={cand:.4f} -> loss {lo_:.4f}")
    return best, best_stats


def get(p: dict, knob: str) -> float:
    if knob.endswith("[0]") or knob.endswith("[1]"):
        name, i = knob[:-3], int(knob[-2])
        return float(p[name][i])
    return float(p[knob])


def set_(p: dict, knob: str, value: float) -> dict:
    if knob.endswith("[0]") or knob.endswith("[1]"):
        name, i = knob[:-3], int(knob[-2])
        lst = list(p[name])
        lst[i] = value
        if i == 0 and lst[0] > lst[1]:
            lst[1] = lst[0] + 0.1
        p[name] = lst
    else:
        p[knob] = value
    return p


def main() -> None:
    t0 = time.time()
    print("loading targets from", SOURCE)
    real = targets()
    ev_t, ph_t = real["events"], real["physical"]

    params = deepcopy(DEFAULT_PARAMS)
    ev_keys = ["passes_per_match", "pass_completion", "shots_per_match", "pass_length_mean_m"]
    ev_knobs = {"reaction_s[1]": (2.0, 5.0), "pass_base": (0.9, 1.05), "pass_len_penalty_per_m": (0.002, 0.008),
                "pass_pressure_penalty": (0.05, 0.2), "shot_logit_intercept": (1.5, 3.5),
                "pass_target_len_m": (10.0, 20.0), "pass_len_spread_m": (6.0, 16.0)}
    fit_seeds = list(range(101, 113))
    print("fitting event parameters…")
    params, ev_s = coordinate_search(params, ev_knobs, lambda p: event_stats(p, fit_seeds), ev_t, ev_keys)

    ph_keys = ["distance_km_per90_median", "psv99_kmh_median", "sprints_per90_median"]
    params.update({"jog_max_mps": 5.0, "urgent_min_gap_m": 8.0, "drift_m": 4.0, "drift_tau_s": 15.0})
    ph_knobs = {"drift_m": (0.0, 12.0), "drift_tau_s": (5.0, 30.0), "jog_max_mps": (3.5, 6.5),
                "max_speed_mps[1]": (8.0, 9.8), "max_speed_mps[0]": (6.8, 8.6), "run_prob_per_s": (0.005, 0.1),
                "steer_gain": (0.4, 1.8)}
    print("fitting physical parameters…")
    params, ph_s = coordinate_search(params, ph_knobs, lambda p: physical_stats(p, [201, 202, 203]), ph_t, ph_keys, rounds=3)

    # Held-out validation on fresh seeds
    print("validating on held-out seeds…")
    ev_v = event_stats(params, list(range(901, 921)))
    ph_v = physical_stats(params, [911, 912, 913])
    base_ev = event_stats(DEFAULT_PARAMS, list(range(901, 921)))
    base_ph = physical_stats(DEFAULT_PARAMS, [911, 912, 913])
    params["version"] = "fit-skillcorner"
    version = params_version(params)

    def agg(d: dict) -> dict:
        return {k: ([round(x, 3) for x in v] if isinstance(v, list) else round(v, 4)) for k, v in d.items()
                if not k.startswith("_")}

    FITTED.write_text(json.dumps({
        "params_version": version, "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": SOURCE, "targets": {"events": agg(ev_t), "physical": agg(ph_t)},
        "params": {k: v for k, v in params.items() if k not in ("script",)},
    }, indent=1) + "\n")

    rows = [
        ("Passes per match", ev_t["passes_per_match"], base_ev["passes_per_match"], ev_v["passes_per_match"], "{:.0f}"),
        ("Pass completion", ev_t["pass_completion"], base_ev["pass_completion"], ev_v["pass_completion"], "{:.1%}"),
        ("Shots per match", ev_t["shots_per_match"], base_ev["shots_per_match"], ev_v["shots_per_match"], "{:.1f}"),
        ("Mean pass length (m)", ev_t["pass_length_mean_m"], base_ev["pass_length_mean_m"], ev_v["pass_length_mean_m"], "{:.1f}"),
        ("Distance per 90, median (km)", ph_t["distance_km_per90_median"], base_ph["distance_km_per90_median"], ph_v["distance_km_per90_median"], "{:.2f}"),
        ("Peak speed (PSV-99), median (km/h)", ph_t["psv99_kmh_median"], base_ph["psv99_kmh_median"], ph_v["psv99_kmh_median"], "{:.1f}"),
        ("Sprints per 90, median", ph_t["sprints_per90_median"], base_ph["sprints_per90_median"], ph_v["sprints_per90_median"], "{:.1f}"),
    ]

    def err(real_v, sim_v):
        return f"{100 * (sim_v - real_v) / real_v:+.0f}%"

    ks_len_base = ks(base_ev["_pass_lengths"], ev_t["_pass_lengths"])
    ks_len = ks(ev_v["_pass_lengths"], ev_t["_pass_lengths"])
    ks_psv_base = ks(base_ph["_psv99"], ph_t["_psv99"])
    ks_psv = ks(ph_v["_psv99"], ph_t["_psv99"])
    md = [
        "# Synthetic data realism report", "",
        f"Generated by `python -m matchlens.calibrate.fit` on {time.strftime('%Y-%m-%d', time.gmtime())} "
        f"(params `{version}`, {time.time() - t0:.0f} s).", "",
        f"**Reference:** {SOURCE}. A-League 2024/25: {ev_t['matches']} matches of dynamic events, plus season "
        f"physical aggregates for {ph_t['players']} outfield players. Raw files are downloaded at fit time and never "
        "committed. This report and `fitted_params.json` contain aggregates only.", "",
        "**Method:** the simulator stays fully synthetic (a fictional league, seeded). Calibration only tunes its "
        "parameters by coordinate search, minimising squared relative error on the targets below. Event parameters "
        "are fitted on 12 seeds; physical parameters on 3 seeds with 10 Hz tracking. Numbers below are on **held-out "
        "seeds** that were never used in fitting.", "",
        "| Metric | Real | Hand-set (before) | Calibrated (after) | Error after |", "|---|---|---|---|---|",
    ]
    for name, r, b, a, fmt in rows:
        md.append(f"| {name} | {fmt.format(r)} | {fmt.format(b)} | **{fmt.format(a)}** | {err(r, a)} |")
    md += ["", "Distribution distance (two-sample Kolmogorov–Smirnov statistic, 0 = identical):", "",
           "| Distribution | Before | After |", "|---|---|---|",
           f"| Pass length | {ks_len_base:.3f} | **{ks_len:.3f}** |",
           f"| Player peak speed | {ks_psv_base:.3f} | **{ks_psv:.3f}** |", "",
           "Definitions follow the source where possible: passes are player possessions ending in a pass; a sprint "
           "is ≥ 25.2 km/h; peak speed is the 99.9th percentile of 1 s speeds per player (PSV-99 analogue).", "",
           "Known gaps: event definitions are not one-to-one with the source (for example, SkillCorner "
           "on-ball engagements vs our pressure events), and team shape is rule-based rather than learned.", ""]
    REPORT.write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
