"""Probability models the simulator uses to make decisions and roll outcomes.

All functions take positions in the acting team's attacking frame (goal at x=105).
These are the generator's *ground truth*; the intelligence engine computes its own
metrics from the output data and never reads these values.
"""

from __future__ import annotations

import math

import numpy as np

L, W = 105.0, 68.0
GOAL_Y = 34.0
GOAL_HALF_WIDTH = 3.66
GOAL_HEIGHT = 2.44
PENALTY_AREA_DEPTH = 16.5
PENALTY_AREA_HALF_WIDTH = 20.16
XT_SCALE = 0.45


def in_penalty_area(x: float, y: float) -> bool:
    """Is (x, y) inside the penalty area being attacked (attacking frame)?"""
    return x > L - PENALTY_AREA_DEPTH and abs(y - GOAL_Y) < PENALTY_AREA_HALF_WIDTH


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def xt(x: float, y: float) -> float:
    """Expected threat: rough probability that possession at (x, y) leads to a goal."""
    d = math.hypot(L - x, (GOAL_Y - y) * 1.25)
    return 0.004 + XT_SCALE * math.exp(-d / 13.0)


def goal_angle(x: float, y: float) -> float:
    """Angle (radians) the goal mouth subtends from (x, y)."""
    dx = max(L - x, 0.1)
    return abs(
        math.atan2(GOAL_Y + GOAL_HALF_WIDTH - y, dx) - math.atan2(GOAL_Y - GOAL_HALF_WIDTH - y, dx)
    )


def xg(x: float, y: float, pressure: float = 0.0, header: bool = False) -> float:
    """Shot quality from location, defensive pressure (0-1) and body part."""
    d = math.hypot(L - x, GOAL_Y - y)
    z = -1.0 + 1.5 * goal_angle(x, y) - 0.09 * d - 1.2 * pressure - (0.9 if header else 0.0)
    return sigmoid(z)


def pass_success(
    distance: float,
    lane: float,
    receiver_space: float,
    passing: int,
    lofted: bool,
    zone_penalty: float = 0.0,
) -> float:
    """Probability a pass reaches its target.

    lane: closest opponent distance to the passing line (m).
    receiver_space: closest opponent distance to the reception point (m).
    """
    z = (
        4.3
        - 0.06 * distance
        + 0.05 * (passing - 70)
        - (0.6 if lofted else 2.0) * math.exp(-lane / 2.0)
        - 3.5 * math.exp(-receiver_space / 1.5)
        - (0.9 if lofted else 0.0)
        - zone_penalty
    )
    return sigmoid(z)


def segment_distances(
    a: np.ndarray, b: np.ndarray, pts: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Distance from each point to segment a->b, and the projection parameter t in [0, 1]."""
    ab = b - a
    denom = float(ab @ ab) or 1e-9
    t = np.clip(((pts - a) @ ab) / denom, 0.0, 1.0)
    proj = a + t[:, None] * ab
    return np.linalg.norm(pts - proj, axis=1), t


def cone_blockers(x: float, y: float, pts: np.ndarray) -> int:
    """Number of points inside the triangle from (x, y) to the two goalposts."""
    if len(pts) == 0:
        return 0
    a = np.array([x, y])
    b = np.array([L, GOAL_Y - GOAL_HALF_WIDTH - 0.5])
    c = np.array([L, GOAL_Y + GOAL_HALF_WIDTH + 0.5])

    def cross(o: np.ndarray, p: np.ndarray, q: np.ndarray) -> np.ndarray:
        return (p[0] - o[0]) * (q[:, 1] - o[1]) - (p[1] - o[1]) * (q[:, 0] - o[0])

    d1, d2, d3 = cross(a, b, pts), cross(b, c, pts), cross(c, a, pts)
    neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
    pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    return int((~(neg & pos)).sum())
