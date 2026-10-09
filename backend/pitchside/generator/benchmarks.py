"""Reference values the validation report compares against.

TARGETS are per-match averages (both teams combined unless noted). The current numbers
are approximate typical top-flight values and are placeholders: replace them with the
Premier League 2025-26 season averages once collected, and record the source below.

Source: TODO (Premier League 2025-26 season averages)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    value: float
    tolerance: float  # allowed relative deviation of a multi-match average, e.g. 0.15 = ±15%
    single_match: tuple[float, float]  # plausible range for one match
    label: str


TARGETS: dict[str, Target] = {
    "goals": Target(2.8, 0.20, (0, 9), "Goals"),
    "shots": Target(25.5, 0.15, (10, 45), "Shots"),
    "shots_on_target": Target(8.5, 0.20, (2, 20), "Shots on target"),
    "xg": Target(3.0, 0.20, (0.8, 6.5), "Expected goals (xG)"),
    "passes": Target(900, 0.12, (650, 1200), "Passes"),
    "pass_accuracy": Target(0.83, 0.05, (0.72, 0.91), "Pass accuracy"),
    "corners": Target(10.2, 0.25, (2, 22), "Corners"),
    "fouls": Target(21.0, 0.25, (8, 35), "Fouls"),
    "yellow_cards": Target(3.9, 0.30, (0, 10), "Yellow cards"),
    "red_cards": Target(0.1, 1.00, (0, 2), "Red cards"),
    "offsides": Target(3.4, 0.40, (0, 10), "Offsides"),
    "tackles": Target(33.0, 0.25, (15, 60), "Tackles won"),
    "interceptions": Target(17.0, 0.30, (5, 40), "Interceptions"),
    "ball_in_play_min": Target(56.0, 0.08, (48, 66), "Ball in play (min)"),
    "distance_per_player_km": Target(
        10.8, 0.10, (9.0, 12.5), "Distance per outfield player per 90 (km)"
    ),
    "team_distance_km": Target(112.0, 0.10, (95, 125), "Distance per team (km)"),
}
