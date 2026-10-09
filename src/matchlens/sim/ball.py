"""Ball physics: gravity, air drag, bounce and rolling resistance on grass.

A size-5 ball (0.43 kg, 0.22 m across) loses speed in the air to quadratic drag and on the
ground to rolling resistance (a constant part plus a part that grows with speed), and bounces
with roughly half its vertical speed. Kicks are solved backwards from where the ball should
arrive: a ground pass gets the launch speed that brings it to the target at a receivable pace;
a lofted ball gets the launch speed (at a chosen angle) whose first bounce lands on the target.
"""

from __future__ import annotations

import math

import numpy as np

G = 9.81
RADIUS = 0.11
AIR_K = 0.013  # quadratic drag, 0.5 * rho * Cd * A / m (1/m)
ROLL_C0 = 0.9  # constant rolling resistance on grass (m/s^2)
ROLL_C1 = 0.22  # speed-proportional rolling resistance (1/s)
RESTITUTION = 0.5  # vertical speed kept through a bounce
BOUNCE_KEEP = 0.75  # horizontal speed kept through a bounce


def step(pos: np.ndarray, vel: np.ndarray, dt: float, substeps: int = 2) -> None:
    """Advance a free ball in place (pos and vel are length-3: x, y, z)."""
    h = dt / substeps
    for _ in range(substeps):
        if pos[2] > 1e-3 or vel[2] > 1e-3:  # in the air
            sp = math.sqrt(vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2)
            vel[0] -= AIR_K * sp * vel[0] * h
            vel[1] -= AIR_K * sp * vel[1] * h
            vel[2] -= (G + AIR_K * sp * vel[2]) * h
            pos += vel * h
            if pos[2] <= 0.0:
                pos[2] = 0.0
                if vel[2] < -1.2:
                    vel[2] = -vel[2] * RESTITUTION
                    vel[0] *= BOUNCE_KEEP
                    vel[1] *= BOUNCE_KEEP
                else:
                    vel[2] = 0.0
        else:  # rolling
            pos[2] = 0.0
            vel[2] = 0.0
            sp = math.hypot(vel[0], vel[1])
            if sp < 1e-3:
                vel[0] = vel[1] = 0.0
                continue
            new = max(0.0, sp - (ROLL_C0 + ROLL_C1 * sp) * h)
            vel[0] *= new / sp
            vel[1] *= new / sp
            pos[0] += vel[0] * h
            pos[1] += vel[1] * h


def _ground_dist(v0: float, v1: float) -> tuple[float, float]:
    """Distance and time for a rolling ball to slow from v0 to v1 (dv/dt = -(c0 + c1 v))."""
    k = ROLL_C0 / ROLL_C1
    t = math.log((v0 + k) / (v1 + k)) / ROLL_C1
    return (v0 - v1) / ROLL_C1 - k * t, t


def ground_kick(length: float, arrive: float) -> tuple[float, float]:
    """Launch speed for a ground pass that is still moving at `arrive` m/s after `length` m, and its travel time."""
    lo, hi = arrive, 40.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if _ground_dist(mid, arrive)[0] < length:
            lo = mid
        else:
            hi = mid
    v0 = 0.5 * (lo + hi)
    return v0, _ground_dist(v0, arrive)[1]


def _carry(v0: float, angle: float, z0: float = 0.0, dt: float = 0.02) -> tuple[float, float]:
    """Horizontal distance and time to the first bounce for a kick at speed v0 and elevation angle."""
    p = np.array([0.0, 0.0, z0])
    v = np.array([v0 * math.cos(angle), 0.0, v0 * math.sin(angle)])
    t = 0.0
    while t < 8.0:
        sp = math.sqrt(v[0] ** 2 + v[2] ** 2)
        v[0] -= AIR_K * sp * v[0] * dt
        v[2] -= (G + AIR_K * sp * v[2]) * dt
        p += v * dt
        t += dt
        if p[2] <= 0.0 and v[2] < 0:
            return float(p[0]), t
    return float(p[0]), t


def lofted_kick(length: float, angle: float, z0: float = 0.0) -> tuple[float, float]:
    """Launch speed whose first bounce lands `length` m away at elevation `angle` (radians), and the flight time."""
    v0 = math.sqrt(max(1.0, length) * G / max(0.2, math.sin(2 * angle)))
    t = 0.0
    for _ in range(4):
        reach, t = _carry(v0, angle, z0)
        if reach <= 0:
            break
        v0 *= math.sqrt(length / reach)
    return min(v0, 30.0), t
