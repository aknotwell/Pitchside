"""Simulator parameters.

``DEFAULT_PARAMS`` are hand-set starting values. The calibration job replaces
them with values fitted to licensed open data (SkillCorner MIT, IDSSE CC BY,
Wyscout CC BY); only fitted parameters are ever committed.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_PARAMS: dict[str, Any] = {
    "version": "agents-v1",
    "period_min": 45,
    # Decision making (seconds from receiving to the first decision; tempo and composure scale it)
    "decision_s": [0.3, 1.1],
    "carry_s": [0.6, 1.8],
    "decision_temp": 0.005,  # softmax temperature over option utilities; vision lowers it, flair raises it
    "carry_bias": -1.0,
    "turnover_weight": 1.0,  # how much players fear giving the ball away (higher: safer, shorter passing)
    "carry_keep_logit": 0.5,
    "pass_value_discount": 0.85,  # a pass or carry only sets up the next action; a shot is the end product
    "carry_value_discount": 0.75,
    "shot_bias": 0.7,
    "shot_max_dist_m": 30.0,
    "long_shot_pref": 0.03,
    "box_space_shot_bonus": 0.04,
    "box_carry_risk": 0.12,  # per defender within 6 m, extra risk of dribbling into the box
    "box_tackle_factor": 1.0,  # tackle rate in the box relative to elsewhere  # in the box, nobody in the way and room to shoot: take it
    # Passing
    "pass_base": 0.97,
    "pass_len_penalty_per_m": 0.004,
    "pass_pressure_penalty": 0.12,
    "pass_error_m": 1.2,
    "clear_value": 0.012,
    "clear_cost": 0.0,  # possession usually lost with a clearance
    "long_pass_cost": 0.0,
    "cross_bias": 0.0,  # extra pull towards crossing, scaled by the crosser's crossing trait  # utility cost per 10 m beyond 25 m (scaled down for players with vision)
    "close_while_travel": 0.3,  # share of a defender's top speed spent closing the receiver while a pass travels
    "support_m": [6.0, 12.0],  # how far supporting teammates show for the ball
    "receive_effort": 0.85,  # share of top speed when running onto a pass or a loose ball
    "pass_max_len_m": [22.0, 62.0],  # by vision, low -> high
    "intercept_reach_m": 1.1,
    "intercept_prob": 0.35,  # per 0.1 s step with the ball within reach
    "control_base": 0.95,
    # Defending
    "engage_m": 14.0,  # the nearest defender closes the ball down inside this radius (grows with pressing)
    "jockey_m": [1.0, 3.2],  # how tight the first defender stands off, high press -> low
    "tackle_rate_per_s": 0.3,
    "tackle_win_logit": 0.3,
    "take_on_prob": 0.35,  # per tick, when the carrier runs straight at a goal-side defender
    "nick_prob": 0.25,  # per tick, a defender within reach of a loose touch gets a toe to it
    "crowd_tackle_boost": 0.8,  # tackle rate multiplier per extra opponent around the ball
    "back_to_keeper": 0.5,
    "header_behind": 0.3,  # a defender heading a cross away near their own goal line puts it behind  # a defender who wins a long ball in their own third plays it first time to the keeper
    "shield_bias": 0.03,  # extra pull towards shielding under pressure, scaled by composure
    "press_effort": 0.7,  # share of top speed for the pressing defender (more for pressing teams)
    "take_on_logit": 0.0,  # extra edge for the defender when the carrier runs straight at them
    "box_foul_factor": 0.08,
    "header_chance": 0.25,  # a won cross in the box becomes a header at goal (scaled by heading)
    "foul_share_of_tackles": 0.22,
    "card_share_of_fouls": 0.13,
    # Shots: xG = sigmoid(a + b*dist + c*angle)
    "xg_a": -0.4,
    "xg_b": -0.16,
    "xg_c": 1.6,
    "shot_outcome_split": {"saved": 0.33, "off_target": 0.45, "blocked": 0.22},
    "corner_after_save": 0.35,
    "corner_after_block": 0.5,
    # Restarts (seconds the ball is dead)
    "restart_s": {"throw_in": [5, 11], "goal_kick": [11, 19], "free_kick": [14, 26], "corner": [16, 26],
                  "kickoff": [40, 60], "sub": [12, 22], "penalty": [45, 70]},
    "restart_scale": 1.0,
    "stoppage_s": [60, 240],
    # Physical
    "max_speed_mps": [7.4, 9.5],
    "max_accel_mps2": 4.2,
    "steer_gain": 1.1,
    "run_prob_per_s": 0.06,
    "run_s": [1.8, 3.5],  # how long a run in behind lasts
    "run_depth_m": [5.0, 13.0],  # how far beyond the defensive line a run in behind aims
    "run_release_s": 0.8,  # a runner holds the offside line until the passer is this close to playing
    "drift_m": 3.0,  # off-ball positional wander (Ornstein-Uhlenbeck amplitude, metres)
    "drift_tau_s": 15.0,
    "jog_max_mps": 5.0,
    "hard_run_mps": 99.0,  # cap on non-sprint hurrying (pressing, chasing); 99 = no cap
    "urgent_min_gap_m": 3.0,
    # Team strength and scripted stories
    "strength_sd": 0.03,
    "home_advantage": 0.0,  # home finishing edge (and a quarter of it in passing); away side loses the same
    "script": [
        {"from_min": 55, "to_min": 72, "team": "a", "press": 0.45, "tempo": 0.15, "label": "away high press"},
    ],
}


def params_version(params: dict[str, Any]) -> str:
    """Content-addressed version: same parameters, same id."""
    blob = json.dumps({k: v for k, v in params.items() if k != "version"}, sort_keys=True).encode()
    return f"{params.get('version', 'p')}-{hashlib.sha256(blob).hexdigest()[:10]}"


FITTED_PATH = Path(__file__).with_name("fitted_params.json")


def fitted_params() -> dict[str, Any] | None:
    """Parameters fitted by ``python -m matchlens.calibrate.fit`` (aggregates only, safe to commit)."""
    if not FITTED_PATH.exists():
        return None
    return json.loads(FITTED_PATH.read_text())["params"]


def load_params(overrides: dict[str, Any] | None = None, calibrated: bool = True) -> dict[str, Any]:
    params = deepcopy(DEFAULT_PARAMS)
    fitted = fitted_params() if calibrated else None
    if fitted:
        params.update({k: v for k, v in fitted.items() if k != "script"})
    if overrides:
        params.update(overrides)
    return params
