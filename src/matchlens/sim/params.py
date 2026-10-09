"""Simulator parameters.

``DEFAULT_PARAMS`` are hand-set starting values. The calibration job replaces
them with values fitted to licensed open data (SkillCorner MIT, IDSSE CC BY,
Wyscout CC BY); only fitted parameters are ever committed.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

DEFAULT_PARAMS: dict[str, Any] = {
    "version": "hand-v0",
    "period_min": 45,
    # On-ball action choice
    "carry_prob": 0.17,
    "shot_logit_intercept": 2.3,
    "shot_logit_per_m": -0.21,
    "shot_max_dist_m": 32.0,
    # Pass completion: logistic-ish linear model, clipped
    "pass_base": 0.955,
    "pass_len_penalty_per_m": 0.0062,
    "pass_forward_penalty": 0.05,
    "pass_pressure_penalty": 0.15,
    "pass_target_len_m": 15.0,
    "pass_len_spread_m": 10.0,
    "ball_speed_mps": [11.0, 21.0],
    # Pressure and defending
    "pressure_base": 0.16,
    "pressure_high_zone_bonus": 0.12,
    "carry_loss_under_pressure": 0.2,
    "tackle_attempt_on_pressured_pass": 0.12,
    "foul_share_of_tackles": 0.3,
    "card_share_of_fouls": 0.13,
    "ball_out_share_of_failed_passes": 0.3,
    "interception_share_of_failed_passes": 0.3,
    # Shots: xG = sigmoid(a + b*dist + c*angle)
    "xg_a": -0.4,
    "xg_b": -0.16,
    "xg_c": 1.6,
    "shot_outcome_split": {"saved": 0.33, "off_target": 0.45, "blocked": 0.22},
    "corner_after_save": 0.35,
    "corner_after_block": 0.5,
    # Timing (seconds)
    "reaction_s": [1.0, 3.2],
    "restart_s": {"throw_in": [7, 14], "goal_kick": [14, 24], "free_kick": [16, 30], "corner": [18, 30], "kickoff": [45, 70], "sub": [25, 40]},
    "stoppage_s": [60, 240],
    # Physical
    "max_speed_mps": [7.4, 9.5],
    "max_accel_mps2": 4.2,
    "steer_gain": 1.1,
    "run_prob_per_s": 0.06,
    # Team strength and scripted stories
    "strength_sd": 0.03,
    "script": [
        {"from_min": 55, "to_min": 72, "team": "a", "press": 0.45, "tempo": 0.15, "label": "away high press"},
    ],
}


def params_version(params: dict[str, Any]) -> str:
    """Content-addressed version: same parameters, same id."""
    blob = json.dumps({k: v for k, v in params.items() if k != "version"}, sort_keys=True).encode()
    return f"{params.get('version', 'p')}-{hashlib.sha256(blob).hexdigest()[:10]}"


def load_params(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    params = deepcopy(DEFAULT_PARAMS)
    if overrides:
        params.update(overrides)
    return params
