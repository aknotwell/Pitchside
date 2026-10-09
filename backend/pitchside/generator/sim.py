"""Match simulator.

Two layers run together at 10 Hz:

* Decision layer: the player on the ball chooses an action (pass, carry, dribble, shot,
  clearance) by weighing expected threat against the risk of losing the ball. Outcomes
  are rolled from the probability models in `models.py`.
* Movement layer: every player steers towards a target given by formation anchors,
  shifted by ball position, possession and tactics, with role overrides (carrier,
  pressers, receiver, runners). Speed and acceleration are capped by pace and fatigue.

Events are read off the simulation, so every pass starts where the ball actually was
and every speed can be recovered from the tracking data.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import numpy as np

from pitchside.schemas import Team

from . import models as m
from .formations import FORMATIONS
from .scenarios import Scenario

GENERATOR_VERSION = "0.1.0"

FPS = 10
DT = 1.0 / FPS
L, W = m.L, m.W
SIDES = ("home", "away")

# Movement
ACCEL = 4.5  # m/s^2
DRIFT_DECAY = 0.01
DRIFT_NOISE = 0.3
FOCUS_SMOOTHING = 0.06  # how fast the team block follows the ball (per step)
SHAPE_GAIN = 0.75  # approach speed per metre of distance for players holding shape
ROLE_GAIN = 1.2  # ... and for players with a job (carrier, pressers, receiver, chasers)

# Decision tuning knobs, calibrated against the validation report (`pitchside-gen batch`)
TEMPERATURE = 0.012  # softmax temperature for action choice (lower = more "optimal" players)
TIMER_SCALE = 3.2  # how long players take on the ball before acting
SETTLE_SPEED = 0.4  # share of top speed while a player settles the ball
LOSS_WEIGHT = 2.0  # how much players fear losing the ball
SHOT_BIAS = -0.015  # utility offset for shooting (negative = keep the ball for a better chance)
MIN_SHOT_XG = 0.025
SHOT_OPENING = 0.37  # chance a touch in range offers a shooting chance
BLOCKER_XG_FACTOR = 0.55  # xG multiplier per defender in the shooting cone
CARRY_BIAS = 0.0
DRIBBLE_BIAS = -0.05
RUN_RATE = 0.004  # chance per step that an attacker starts a run in behind

# Defending
MARKING_ZONE = 30.0  # m from own goal where defenders mark attackers
TACKLE_RATE = 0.0028  # tackle attempts per step while within 1.3 m of the carrier
BOX_TACKLE_BOOST = 8.0  # defenders commit more around their own box
BEATEN_STUN = 0.7  # seconds a beaten defender needs to recover
FOUL_SHARE = 0.80  # share of tackle attempts that are fouls
BOX_FOUL_FACTOR = 0.02  # defenders are careful inside their own box
YELLOW_RATE = 0.20  # per foul
STRAIGHT_RED_RATE = 0.001  # per foul
INTERCEPT_LANE = 1.2  # a failed pass is intercepted if a defender is this close to its line (m)
CORNER_FROM_BOX_DEFENDING = 0.5  # chance a defensive touch in the box goes behind for a corner
DEFLECTION_CORNER = 0.5  # chance a ball an attacker sends over the goal line took a deflection
OFFSIDE_RISK = 0.30  # chance a forward pass to an attacker level with the last defender is offside

# Pass difficulty the spatial model doesn't capture (logit penalties)
FINAL_THIRD_PENALTY = 0.9
BOX_PENALTY = 1.3
CROSS_PENALTY = 1.0

# Dead-ball durations (s): uniform ranges
DEAD_TIME = {
    "throw_in": (12, 22),
    "goal_kick": (22, 38),
    "corner": (30, 45),
    "free_kick": (28, 50),
    "offside": (20, 35),
    "penalty": (60, 90),
    "goal": (60, 90),
}
SUB_DELAY = 25.0
INJURY_RATE = 0.25  # chance a foul leaves a player needing treatment
INJURY_DELAY = (30, 90)

HELD, FLIGHT, LOOSE, DEAD = "held", "flight", "loose", "dead"
_PITCH = np.array([L, W])


@dataclass
class Flight:
    start: np.ndarray
    end: np.ndarray
    t0: float
    duration: float
    height: float
    on_arrive: Callable[[], None]
    home_to: int | None = None
    out_ok: bool = False  # True for balls meant to leave the pitch (shots off target)


@dataclass
class Restart:
    kind: str
    team: int
    at: np.ndarray
    until: float
    taker: int | None = None


@dataclass
class TeamState:
    press_height: float
    tempo: float
    width: float
    directness: float
    subs_plan: list[float] = field(default_factory=list)
    subs_made: int = 0


class MatchSim:
    def __init__(
        self,
        home: Team,
        away: Team,
        seed: int,
        scenario: Scenario | None = None,
        half_length: float = 45.0,
    ) -> None:
        self.teams = (home, away)
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.scenario = scenario or Scenario(name="open")
        self.half_length = half_length
        self.match_id = f"{home.id}-v-{away.id}-s{seed}"

        self.players = {p.id: p for t in self.teams for p in t.players}
        self.bench = [list(home.bench), list(away.bench)]

        # Slot arrays: 0-10 home, 11-21 away
        self.slot_pid: list[str] = list(home.starting_xi) + list(away.starting_xi)
        self.slot_label: list[str] = []
        self._lines: list[str] = []
        self.anchor = np.zeros((22, 2))
        for team, t in enumerate(self.teams):
            for i, slot in enumerate(FORMATIONS[t.formation]):
                k = team * 11 + i
                self.anchor[k] = (slot.x, slot.y)
                self.slot_label.append(slot.position)
                self._lines.append(slot.line)
        self.slot_line = np.array(self._lines)
        self._idx = (np.arange(11), np.arange(11, 22))
        # Goalkeepers always occupy slot 0 of each team (checked when formations load) and
        # are never substituted or sent off; use this mask rather than slot arithmetic.
        self.is_gk = np.zeros(22, dtype=bool)
        self.is_gk[[0, 11]] = True
        self._is_def = self.slot_line == "def"
        self._is_mid = self.slot_line == "mid"
        self._is_att = self.slot_line == "att"
        self.r: dict[str, np.ndarray] = {}
        self._load_ratings()

        self.pos = np.zeros((22, 2))
        self.vel = np.zeros((22, 2))
        self.active = np.ones(22, dtype=bool)
        self.stamina = np.ones(22)
        self.stun_until = np.zeros(22)
        self.drift = np.zeros((22, 2))
        self.yellow: set[str] = set()
        self.runs: dict[int, tuple[np.ndarray, float]] = {}

        self.tac = [TeamState(**t.tactics.model_dump()) for t in self.teams]
        self.base_tac = [t.tactics for t in self.teams]
        for ts in self.tac:
            n = int(self.rng.integers(3, 6))
            ts.subs_plan = sorted(self.rng.uniform(55, 86, n).round(0).tolist())

        self.score = [0, 0]
        self.force_goal = [0, 0]
        self.force_red = [0, 0]
        self._scenario_queue = sorted(self.scenario.events, key=lambda e: e.minute)

        # Ball
        self.ball = np.array([L / 2, W / 2, 0.0])
        self.loose_vel = np.zeros(2)
        self.mode = DEAD
        self.holder: int | None = None
        self.flight: Flight | None = None
        self.focus = self.ball[:2].copy()
        self.restart: Restart | None = None
        self.poss_team = 0
        self.poss_id = 0
        self.last_touch = 0

        # Carrier bookkeeping
        self.timer = 0.0
        self.carry_start = np.zeros(2)
        self.carry_t0 = 0.0
        self.carry_target = np.zeros(2)
        self.driving = False
        self.pressed_by: set[int] = set()
        self.pending_miss: dict | None = None
        self.last_complete: tuple[str, int, float, int] | None = (
            None  # passer, recipient slot, t, poss
        )

        # Clock
        self.frame = 0
        self.t = 0.0
        self.period = 0
        self.period_start = 0.0
        self.dir = [1, -1]

        # Output
        self.events: list[dict] = []
        self._rec_t: list[float] = []
        self._rec_period: list[int] = []
        self._rec_live: list[bool] = []
        self._rec_ball: list[np.ndarray] = []
        self._rec_pos: list[np.ndarray] = []
        self._rec_speed: list[np.ndarray] = []
        self._rec_active: list[np.ndarray] = []
        self._rec_roster: list[int] = []
        self.rosters: list[list[str]] = [list(self.slot_pid)]

    # ------------------------------------------------------------------ setup helpers

    def _load_ratings(self, slots: range | list[int] = range(22)) -> None:
        names = (
            "pace",
            "passing",
            "dribbling",
            "finishing",
            "defending",
            "decisions",
            "stamina",
            "goalkeeping",
        )
        if not self.r:
            self.r = {n: np.zeros(22) for n in names}
        for k in slots:
            ratings = self.players[self.slot_pid[k]].ratings
            for n in names:
                self.r[n][k] = getattr(ratings, n)
        self.vmax = 6.6 + 2.6 * (self.r["pace"] - 50) / 50  # m/s, ~9.2 m/s for pace 100

    # ------------------------------------------------------------------ frames of reference

    def to_att(self, team: int, xy: np.ndarray) -> np.ndarray:
        """Absolute <-> team attacking frame (the transform is its own inverse)."""
        if self.dir[team] == 1:
            return np.asarray(xy, dtype=float)
        return _PITCH - xy

    to_abs = to_att

    @staticmethod
    def team_of(slot: int) -> int:
        return slot // 11

    def team_slots(self, team: int) -> np.ndarray:
        idx = self._idx[team]
        return idx[self.active[idx]]

    # ------------------------------------------------------------------ events

    def minute(self) -> float:
        elapsed = self.t - self.period_start
        return (self.period - 1) * self.half_length + elapsed / 60.0

    def clock(self, t: float | None = None) -> str:
        """Broadcast clock at match time `t` in the current period (default: now)."""
        elapsed = (self.t if t is None else t) - self.period_start
        reg = self.half_length * 60
        if elapsed <= reg:
            total = int((self.period - 1) * reg + elapsed)
            return f"{total // 60:02d}:{total % 60:02d}"
        extra = int(elapsed - reg)
        return f"{int(self.half_length * self.period)}+{extra // 60}:{extra % 60:02d}"

    @staticmethod
    def _pt(xy) -> dict | None:
        if xy is None:
            return None
        return {
            "x": round(float(np.clip(xy[0], -2, L + 2)), 2),
            "y": round(float(np.clip(xy[1], -2, W + 2)), 2),
        }

    def _event(
        self,
        type_: str,
        team: int | None = None,
        slot: int | None = None,
        start=None,
        end=None,
        outcome: str | None = None,
        duration: float = 0.0,
        **extra,
    ) -> dict:
        ev = {
            "id": f"{self.match_id}-{len(self.events) + 1:05d}",
            "t": round(self.t, 2),
            "period": self.period,
            "clock": self.clock(),
            "type": type_,
            "team": SIDES[team] if team is not None else None,
            "player": self.slot_pid[slot] if slot is not None else None,
            "possession": self.poss_id,
            "start": self._pt(start),
            "end": self._pt(end),
            "duration": round(duration, 2),
            "outcome": outcome,
            "extra": extra,
        }
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------------ main loop

    def run(self) -> MatchSim:
        for _ in self.steps():
            pass
        return self

    def steps(self) -> Iterator[int]:
        """Simulate the match one frame at a time, yielding each recorded frame's index.

        `run()` drains this in one go; the live server steps it at match speed. While
        paused at a yield, `self.t` is the time of the frame just recorded.
        """
        stoppage = [int(self.rng.integers(1, 5)), int(self.rng.integers(3, 8))]
        for period in (1, 2):
            self._start_period(period)
            end_t = self.period_start + self.half_length * 60 + stoppage[period - 1] * 60
            while self.t < end_t:
                self._step()
                self._record()
                yield len(self._rec_t) - 1
                self.frame += 1
                self.t = self.frame * DT
            self._event(
                "period", outcome="end", score={"home": self.score[0], "away": self.score[1]}
            )

    def _start_period(self, period: int) -> None:
        self.period = period
        self.period_start = self.t
        self.dir = [1, -1] if period == 1 else [-1, 1]
        kick_team = 0 if period == 1 else 1
        for team in (0, 1):
            for k in self.team_slots(team):
                self.pos[k] = self.to_abs(team, self._kickoff_shape(k))
        self.vel[:] = 0
        self.flight = None
        self._event("period", outcome="start")
        self._set_dead("kick_off", kick_team, np.array([L / 2, W / 2]), delay=1.0)

    def _kickoff_shape(self, k: int) -> np.ndarray:
        x, y = self.anchor[k]
        return np.array([min(x * 0.62, 50.0), y])

    def _step(self) -> None:
        self._scenario_tick()
        if self.frame % (60 * FPS) == 0:
            self._update_game_state()
        targets, factor, gain = self._targets()
        self._move(targets, factor, gain)

        if self.mode == HELD:
            self._step_held()
        elif self.mode == FLIGHT:
            self._step_flight()
        elif self.mode == LOOSE:
            self._step_loose()
        else:
            self._step_dead()

    def _record(self) -> None:
        self._rec_t.append(round(self.t, 1))
        self._rec_period.append(self.period)
        self._rec_live.append(self.mode != DEAD)
        self._rec_ball.append(self.ball.copy())
        self._rec_pos.append(self.pos.astype(np.float32))
        self._rec_speed.append(np.linalg.norm(self.vel, axis=1).astype(np.float32))
        self._rec_active.append(self.active.copy())
        self._rec_roster.append(len(self.rosters) - 1)

    # ------------------------------------------------------------------ scenario & game state

    def _scenario_tick(self) -> None:
        while self._scenario_queue and self._is_due(self._scenario_queue[0].minute):
            ev = self._scenario_queue.pop(0)
            team = SIDES.index(ev.team)
            if ev.type == "goal":
                self.force_goal[team] += 1
            elif ev.type == "red_card":
                self.force_red[team] += 1

    def _is_due(self, minute: float) -> bool:
        """Has the match reached a scripted minute? Minutes after half-time belong to the
        second half: during first-half stoppage the clock passes 45, but "minute 46" in a
        script means the second half, not 45+1."""
        if minute > self.half_length and self.period == 1:
            return False
        return self.minute() >= minute

    def _update_game_state(self) -> None:
        minute = self.minute()
        for team in (0, 1):
            base = self.base_tac[team]
            ts = self.tac[team]
            diff = self.score[team] - self.score[1 - team]
            ts.press_height, ts.tempo, ts.directness = (
                base.press_height,
                base.tempo,
                base.directness,
            )
            if diff < 0 and minute > 60:
                urgency = min(1.0, (minute - 60) / 25) * (1 if diff == -1 else 1.3)
                ts.press_height = min(1.0, base.press_height + 0.25 * urgency)
                ts.tempo = min(1.0, base.tempo + 0.2 * urgency)
                ts.directness = min(1.0, base.directness + 0.25 * urgency)
            elif diff > 0 and minute > 70:
                ts.press_height = max(0.0, base.press_height - 0.2)
                ts.tempo = max(0.0, base.tempo - 0.15)

    # ------------------------------------------------------------------ movement layer

    def _offside_line(self, team: int) -> float:
        """Second-last opponent's x in `team`'s attacking frame (never inside own half)."""
        opp = self.team_slots(1 - team)
        xs = np.sort(self.to_att(team, self.pos[opp])[:, 0])[::-1]
        second = xs[1] if len(xs) > 1 else L
        return max(float(second), L / 2)

    def _targets(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        tgt = self.pos.copy()
        factor = np.full(22, 0.8)
        gain = np.full(22, SHAPE_GAIN)
        raw_focus = self.flight.end if self.mode == FLIGHT and self.flight else self.ball[:2]
        self.focus += (raw_focus - self.focus) * FOCUS_SMOOTHING
        focus = self.focus
        restart = self.restart if self.mode == DEAD else None

        self.drift += -self.drift * DRIFT_DECAY + self.rng.normal(0, DRIFT_NOISE, (22, 2))

        for team in (0, 1):
            idx = self._idx[team]
            ball = self.to_att(team, focus)
            ts = self.tac[team]
            in_poss = self.poss_team == team
            xs = self.anchor[idx, 0].copy()
            ys = self.anchor[idx, 1].copy()

            if restart is not None and restart.kind == "kick_off":
                shape = np.array([self._kickoff_shape(k) for k in idx])
                xs, ys = shape[:, 0], shape[:, 1]
            elif in_poss:
                xs += 0.6 * (ball[0] - 52.5) + 4 + 8 * (ts.press_height - 0.5)
                ys = 34 + (ys - 34) * (0.85 + 0.4 * ts.width) + (ball[1] - 34) * 0.2
            else:
                xs += 0.55 * (ball[0] - 52.5) - 6 + 14 * (ts.press_height - 0.5)
                cx = xs[1:].mean()
                xs[1:] = cx + (xs[1:] - cx) * 0.7
                ys = 34 + (ys - 34) * 0.7 + (ball[1] - 34) * 0.45
                # Hold the lines: don't collapse onto the goal line, stay in front of the ball.
                bx = float(ball[0])
                xs = np.where(self._is_def[idx], np.maximum(xs, min(bx - 4, 11.0)), xs)
                xs = np.where(self._is_mid[idx], np.maximum(xs, min(bx - 1, 18.0)), xs)
                xs = np.where(self._is_att[idx], np.maximum(xs, min(bx + 5, 30.0)), xs)

            xs[0] = min(max(3 + 0.1 * float(ball[0]), 2.0), 18.0)
            ys[0] = 34 + (ball[1] - 34) * 0.15
            xs[1:] += self.drift[idx[1:], 0]
            ys[1:] += self.drift[idx[1:], 1]
            if in_poss and restart is None:
                line = max(self._offside_line(team), ball[0])
                xs[1:] = np.minimum(xs[1:], line - 0.8)
            for k, (run_tgt, until) in list(self.runs.items()):
                if self.team_of(k) == team and in_poss and self.t < until:
                    xs[k - team * 11], ys[k - team * 11] = run_tgt
                    factor[k] = 1.0
                elif self.t >= until or not in_poss:
                    self.runs.pop(k, None)
            pts = np.empty((11, 2))
            np.minimum(np.maximum(xs, 1.0), L - 1, out=pts[:, 0])
            np.minimum(np.maximum(ys, 1.0), W - 1, out=pts[:, 1])
            tgt[idx] = self.to_abs(team, pts)

        if self.mode != DEAD:
            self._mark(tgt, factor)

        if self.mode == DEAD:
            factor[:] = 0.6
            if restart is not None:
                taker = self._restart_taker()
                tgt[taker] = restart.at
                factor[taker] = 0.9
        elif self.mode == HELD and self.holder is not None:
            c = self.holder
            tgt[c] = self.carry_target
            factor[c] = 0.78 if self.driving else SETTLE_SPEED
            gain[c] = ROLE_GAIN
            own_goal = self.to_abs(1 - self.team_of(c), np.array([0.0, 34.0]))
            to_goal = own_goal - self.pos[c]
            goal_side = self.pos[c] + to_goal / max(float(np.linalg.norm(to_goal)), 1e-6) * 1.0
            for j in self._pressers(c):
                tgt[j] = goal_side
                factor[j] = 1.0
        elif self.mode == FLIGHT and self.flight is not None:
            end = self.flight.end
            if self.flight.home_to is not None:
                tgt[self.flight.home_to] = end
                factor[self.flight.home_to] = 1.0
            for team in (0, 1):
                j = self._nearest(team, end, outfield=True)
                if j is not None:
                    tgt[j] = end
                    factor[j] = 1.0
        elif self.mode == LOOSE:
            for team in (0, 1):
                for j in self._nearest_n(team, self.ball[:2], 2):
                    tgt[j] = self.ball[:2] + self.loose_vel * 0.6
                    factor[j] = 1.0

        gain[factor >= 0.9] = ROLE_GAIN
        stunned = self.stun_until > self.t
        tgt[stunned] = self.pos[stunned]
        return tgt, factor, gain

    def _move(self, tgt: np.ndarray, factor: np.ndarray, gain: np.ndarray) -> None:
        d = tgt - self.pos
        dist = np.linalg.norm(d, axis=1)
        vmax = self.vmax * (0.82 + 0.18 * self.stamina) * factor
        speed = np.minimum(vmax, dist * gain)
        with np.errstate(invalid="ignore", divide="ignore"):
            unit = np.where(dist[:, None] > 1e-6, d / dist[:, None], 0.0)
        dv = unit * speed[:, None] - self.vel
        dvn = np.linalg.norm(dv, axis=1)
        scale = np.minimum(1.0, (ACCEL * DT) / np.maximum(dvn, 1e-9))
        self.vel += dv * scale[:, None]
        self.vel[~self.active] = 0
        if self.mode == HELD and self.holder is not None:
            self._block_carrier(self.holder)
        self.pos += self.vel * DT
        self.pos[~self.active] = (-5.0, -5.0)
        v = np.linalg.norm(self.vel, axis=1)
        self.stamina -= DT * (1.2e-5 + 4.5e-6 * v**2) * (1.4 - self.r["stamina"] / 100)
        np.clip(self.stamina, 0.3, 1.0, out=self.stamina)

    def _mark(self, tgt: np.ndarray, factor: np.ndarray) -> None:
        """Near their own box, defenders pick up attackers and stand goal-side of them."""
        dteam = 1 - self.poss_team
        if self.to_att(dteam, self.focus)[0] > MARKING_ZONE + 15:
            return
        attackers = [int(k) for k in self.team_slots(self.poss_team) if not self.is_gk[k]]
        if not attackers:
            return
        apos = self.to_att(dteam, self.pos[attackers])
        threats = sorted(
            (
                a
                for a, p in zip(attackers, apos, strict=True)
                if p[0] < MARKING_ZONE and abs(p[1] - 34) < 26
            ),
            key=lambda a: float(self.to_att(dteam, self.pos[a])[0]),
        )
        free = [int(k) for k in self.team_slots(dteam) if not self.is_gk[k]]
        own_goal = self.to_abs(dteam, np.array([0.0, 34.0]))
        for a in threats:
            if not free:
                break
            d = np.linalg.norm(self.pos[free] - self.pos[a], axis=1)
            i = int(np.argmin(d))
            if d[i] > 15:
                continue
            k = free.pop(i)
            to_goal = own_goal - self.pos[a]
            tgt[k] = self.pos[a] + to_goal / max(float(np.linalg.norm(to_goal)), 1e-6) * 1.5
            factor[k] = 0.95

    def _block_carrier(self, c: int) -> None:
        """A carrier can't run through a defender: remove velocity towards close opponents."""
        opp = self.team_slots(1 - self.team_of(c))
        rel = self.pos[opp] - self.pos[c]
        dist = np.linalg.norm(rel, axis=1)
        for k in np.flatnonzero((dist < 2.0) & (self.stun_until[opp] <= self.t)):
            u = rel[k] / max(dist[k], 1e-6)
            toward = float(self.vel[c] @ u)
            if toward > 0:
                self.vel[c] -= u * toward * (1.0 - dist[k] / 2.0) ** 0.5

    def _nearest(
        self, team: int, xy: np.ndarray, outfield: bool = False, exclude: int | None = None
    ) -> int | None:
        idx = self.team_slots(team)
        if outfield:
            idx = idx[~self.is_gk[idx]]
        if exclude is not None:
            idx = idx[idx != exclude]
        if len(idx) == 0:
            return None
        return int(idx[np.argmin(np.linalg.norm(self.pos[idx] - xy, axis=1))])

    def _nearest_n(self, team: int, xy: np.ndarray, n: int) -> list[int]:
        idx = self.team_slots(team)
        idx = idx[~self.is_gk[idx]]
        order = np.argsort(np.linalg.norm(self.pos[idx] - xy, axis=1))
        return [int(i) for i in idx[order[:n]]]

    def _pressers(self, c: int) -> list[int]:
        team = 1 - self.team_of(c)
        ball_att = self.to_att(team, self.pos[c])
        n = 2 if self.tac[team].press_height > 0.6 and ball_att[0] > 45 else 1
        return self._nearest_n(team, self.pos[c], n)

    # ------------------------------------------------------------------ ball: held

    def _give_ball(self, slot: int, cause: str = "recovery", timer: float | None = None) -> None:
        team = self.team_of(slot)
        self.last_touch = team
        if team != self.poss_team:
            self.poss_id += 1
            self.poss_team = team
            self._event("possession_change", team, slot, start=self.pos[slot], outcome=cause)
        self.mode = HELD
        self.holder = slot
        self.flight = None
        self.pending_miss = None
        self.ball[:2] = self.pos[slot]
        self.ball[2] = 0.0
        tempo = self.tac[team].tempo
        self.timer = (
            timer
            if timer is not None
            else float(self.rng.uniform(0.5, 2.4) * (1.35 - 0.7 * tempo) * TIMER_SCALE)
        )
        self.carry_start = self.pos[slot].copy()
        self.carry_t0 = self.t
        self.pressed_by = set()
        self._new_carry_target(slot)
        if self.rng.random() < 0.5:
            self._maybe_start_run(team)

    def _new_carry_target(self, c: int, drive: bool = False) -> None:
        """Drive forward into space (a chosen carry), or settle the ball while deciding."""
        team = self.team_of(c)
        att = self.to_att(team, self.pos[c])
        self.driving = drive
        if drive:
            tx = att[0] + self.rng.uniform(6, 14)
            ty = att[1] + (34 - att[1]) * 0.2 + self.rng.normal(0, 4)
        else:
            tx = att[0] + self.rng.uniform(-1, 4)
            ty = att[1] + self.rng.normal(0, 2)
        self.carry_target = self.to_abs(
            team, np.array([np.clip(tx, 1, L - 1), np.clip(ty, 1, W - 1)])
        )

    def _maybe_start_run(self, team: int) -> None:
        cand = [
            k
            for k in self.team_slots(team)
            if self.slot_label[k] in ("ST", "LW", "RW", "AM", "CM")
            and k != self.holder
            and k not in self.runs
        ]
        if not cand:
            return
        ball_att = self.to_att(team, self.ball[:2])
        if ball_att[0] < 40:
            return
        k = int(self.rng.choice(cand))
        line = self._offside_line(team)
        cur = self.to_att(team, self.pos[k])
        tgt = np.array(
            [
                min(line + self.rng.uniform(6, 14), 100.0),
                np.clip(cur[1] + self.rng.normal(0, 6), 8, 60),
            ]
        )
        self.runs[k] = (tgt, self.t + self.rng.uniform(2.5, 4.5))

    def _step_held(self) -> None:
        c = self.holder
        assert c is not None
        team = self.team_of(c)
        heading = self.vel[c]
        hn = np.linalg.norm(heading)
        self.ball[:2] = self.pos[c] + (heading / hn * 0.4 if hn > 0.1 else 0)
        self.ball[2] = 0.0
        if np.linalg.norm(self.carry_target - self.pos[c]) < 1.5:
            self._new_carry_target(c, drive=self.driving)

        opp = self.team_slots(1 - team)
        d = np.linalg.norm(self.pos[opp] - self.pos[c], axis=1)
        jn = int(np.argmin(d))
        j, dmin = int(opp[jn]), float(d[jn])
        if dmin < 2.6 and j not in self.pressed_by:
            self.pressed_by.add(j)
            self._event("pressure", 1 - team, j, start=self.pos[j], on=self.slot_pid[c])
        self.timer -= DT * (2.2 if dmin < 2.6 else 1.0)
        if self.rng.random() < RUN_RATE:
            self._maybe_start_run(team)

        if dmin < 1.3 and self.t >= self.stun_until[j] and not self.is_gk[j]:
            rate = TACKLE_RATE * self.r["defending"][j] / 70
            if self._in_box(team, self.pos[c]):  # last-ditch defending in the box
                rate *= BOX_TACKLE_BOOST
            if self.force_red[1 - team]:
                rate = 1.0
            if self.rng.random() < rate:
                self._tackle(j, c)
                return
        if self.timer <= 0:
            self._decide(c)

    def _close_carry(self, c: int) -> None:
        moved = float(np.linalg.norm(self.pos[c] - self.carry_start))
        if moved > 3.0:
            self._event(
                "carry",
                self.team_of(c),
                c,
                start=self.carry_start,
                end=self.pos[c],
                duration=self.t - self.carry_t0,
            )
        self.carry_start = self.pos[c].copy()
        self.carry_t0 = self.t

    def _tackle(self, j: int, c: int) -> None:
        team_j = self.team_of(j)
        self._close_carry(c)
        foul_p = FOUL_SHARE * (1.15 - self.r["decisions"][j] / 200)
        if self.slot_pid[j] in self.yellow:
            foul_p *= 0.25  # booked players are careful
        if self._in_box(self.team_of(c), self.pos[c]):
            foul_p *= BOX_FOUL_FACTOR  # defenders are careful inside their own box
        if self.force_red[team_j] or self.rng.random() < foul_p:
            self._foul(j, c)
            return
        p_win = float(
            np.clip(0.6 + (self.r["defending"][j] - self.r["dribbling"][c]) / 120, 0.2, 0.8)
        )
        if self.rng.random() < p_win:
            self._event("tackle", team_j, j, start=self.pos[j], outcome="won", on=self.slot_pid[c])
            if self.rng.random() < 0.6:
                self._give_ball(j, cause="tackle")
            else:
                self._loose(self.pos[c], self.rng.normal(0, 4, 2), touch=team_j)
        else:
            self._event("tackle", team_j, j, start=self.pos[j], outcome="lost", on=self.slot_pid[c])
            self._event(
                "dribble",
                self.team_of(c),
                c,
                start=self.pos[c],
                outcome="complete",
                past=self.slot_pid[j],
            )
            self.stun_until[j] = self.t + BEATEN_STUN

    def _foul(self, j: int, c: int) -> None:
        team_j, team_c = self.team_of(j), self.team_of(c)
        at = self.pos[c].copy()
        self._event("foul", team_j, j, start=at, on=self.slot_pid[c])
        pid = self.slot_pid[j]
        if self.force_red[team_j]:
            self.force_red[team_j] -= 1
            self._card(j, "red")
        else:
            r = self.rng.random()
            if r < STRAIGHT_RED_RATE:
                self._card(j, "red")
            elif r < STRAIGHT_RED_RATE + YELLOW_RATE:
                if pid in self.yellow:
                    self._card(j, "second_yellow")
                else:
                    self.yellow.add(pid)
                    self._card(j, "yellow")
        if self._in_box(team_c, at):
            spot = self.to_abs(team_c, np.array([94.0, 34.0]))
            self._set_dead("penalty", team_c, spot, delay=self._dead_time("penalty"))
        else:
            self._set_dead("free_kick", team_c, at, delay=self._dead_time("free_kick"))
        if self.restart is not None and self.rng.random() < INJURY_RATE:
            self.restart.until += float(self.rng.uniform(*INJURY_DELAY))

    def _card(self, slot: int, kind: str) -> None:
        self._event("card", self.team_of(slot), slot, start=self.pos[slot], outcome=kind)
        if kind in ("red", "second_yellow"):
            self.active[slot] = False
            self.runs.pop(slot, None)

    # ------------------------------------------------------------------ decision layer

    def _pass_options(self, c: int, min_d: float = 4.0, max_d: float = 65.0) -> list[tuple]:
        team = self.team_of(c)
        ts = self.tac[team]
        here = self.pos[c]
        here_att = self.to_att(team, here)
        opp = self.team_slots(1 - team)
        opp_pos = self.pos[opp]
        line = self._offside_line(team)
        opts = []
        for r in self.team_slots(team):
            if r == c:
                continue
            d0 = float(np.linalg.norm(self.pos[r] - here))
            lead_t = min(d0 / 15.0, 1.5)
            target = np.clip(self.pos[r] + self.vel[r] * lead_t, [0.5, 0.5], [L - 0.5, W - 0.5])
            d = float(np.linalg.norm(target - here))
            if d < min_d or d > max_d:
                continue
            lane_d, lane_t = m.segment_distances(here, target, opp_pos)
            mask = (lane_t > 0.05) & (lane_t < 0.98)
            lane = float(lane_d[mask].min()) if mask.any() else 15.0
            space = float(np.linalg.norm(opp_pos - target, axis=1).min())
            lofted = d > 32 or (lane < 1.5 and d > 18)
            tatt = self.to_att(team, target)
            # Defensive density the spatial model doesn't capture: the final third and box
            # are crowded, and crosses into the box are rarely completed.
            zone = 0.0
            if tatt[0] > 70 and here_att[0] <= 70:
                zone += FINAL_THIRD_PENALTY
            if m.in_penalty_area(tatt[0], tatt[1]):
                zone += BOX_PENALTY
                if abs(here_att[1] - 34) > 18 and here_att[0] > 70:
                    zone += CROSS_PENALTY
            p = m.pass_success(d, lane, space, int(self.r["passing"][c]), lofted, zone)
            gain = m.xt(tatt[0], tatt[1])
            loss = m.xt(L - tatt[0], W - tatt[1]) + 0.005
            u = p * gain - (1 - p) * loss * LOSS_WEIGHT
            u += ts.directness * 0.004 * max(0.0, tatt[0] - here_att[0]) / 10
            if self.is_gk[r]:
                u -= 0.006
            offside = (
                tatt[0] > line + 0.3
                and tatt[0] > here_att[0]
                and self.to_att(team, self.pos[r])[0] > line + 0.3
            )
            opts.append(
                (
                    u,
                    "pass",
                    dict(r=r, target=target, p=p, d=d, lofted=lofted, lane=lane, offside=offside),
                )
            )
        return opts

    def _softmax_pick(self, opts: list[tuple], c: int):
        us = np.array([o[0] for o in opts])
        tau = TEMPERATURE * (1.5 - self.r["decisions"][c] / 100)
        w = np.exp((us - us.max()) / tau)
        return opts[int(self.rng.choice(len(opts), p=w / w.sum()))]

    def _decide(self, c: int) -> None:
        team = self.team_of(c)
        att = self.to_att(team, self.pos[c])
        opp = self.team_slots(1 - team)
        dists = np.linalg.norm(self.pos[opp] - self.pos[c], axis=1)
        jn = int(np.argmin(dists))
        dmin, j = float(dists[jn]), int(opp[jn])
        pressure = math.exp(-dmin / 2.0)

        # Players don't knowingly pass to an offside teammate; offsides still happen through
        # mistimed runs (OFFSIDE_RISK in _launch_pass).
        opts = [o for o in self._pass_options(c) if not o[2]["offside"]]
        dist_goal = math.hypot(L - att[0], 34 - att[1])
        max_range = 36.0 if self.force_goal[team] else 30.0
        opp_att = self.to_att(team, self.pos[opp])
        ahead_mask = (
            (opp_att[:, 0] > att[0])
            & (opp_att[:, 0] < att[0] + 10)
            & (np.abs(opp_att[:, 1] - att[1]) < 7)
        )
        blockers = int(ahead_mask.sum())
        # Not every touch is a shooting chance (body shape, balance, angle).
        opening = self.force_goal[team] or self.rng.random() < SHOT_OPENING * (1 - 0.5 * pressure)
        if opening and dist_goal < max_range and att[0] > 70 and not self.is_gk[c]:
            outfield = opp[~self.is_gk[opp]]
            n_block = m.cone_blockers(att[0], att[1], self.to_att(team, self.pos[outfield]))
            x = m.xg(att[0], att[1], pressure) * BLOCKER_XG_FACTOR**n_block
            if x >= MIN_SHOT_XG or self.force_goal[team]:
                bias = SHOT_BIAS + (0.15 if self.force_goal[team] else 0.0)
                opts.append((x + bias, "shot", dict(xg=x, pressure=pressure, blockers=n_block)))
        if dmin < 2.5 and att[0] > 35 and not self.is_gk[c]:
            p = float(
                np.clip(0.35 + (self.r["dribbling"][c] - self.r["defending"][j]) / 120, 0.15, 0.7)
            )
            ahead = m.xt(min(att[0] + 7, L), att[1])
            lose = m.xt(L - att[0], W - att[1])
            opts.append(
                (p * ahead - (1 - p) * lose * LOSS_WEIGHT + DRIBBLE_BIAS, "dribble", dict(j=j, p=p))
            )
        if dmin > 5 and att[0] < 92:
            ahead = m.xt(min(att[0] + 8, L), att[1] + (34 - att[1]) * 0.1)
            opts.append((ahead * (0.9 - 0.2 * blockers) + CARRY_BIAS, "carry", {}))
        if att[0] < 30 and dmin < 3:
            opts.append((-0.004, "clearance", {}))
        if not opts:
            opts.append((0.0, "clearance", {}))

        _, kind, data = self._softmax_pick(opts, c)
        if kind == "pass":
            self._launch_pass(c, **data)
        elif kind == "shot":
            self._shoot(c, data["xg"], data["pressure"], blockers=data["blockers"])
        elif kind == "dribble":
            self._dribble(c, data["j"], data["p"])
        elif kind == "carry":
            self.timer = float(self.rng.uniform(0.6, 1.6))
            self._new_carry_target(c, drive=True)
        else:
            self._clear(c)

    # ------------------------------------------------------------------ actions

    def _pass_kind(
        self, team: int, start: np.ndarray, target: np.ndarray, r: int, d: float, lofted: bool
    ) -> str:
        s, e = self.to_att(team, start), self.to_att(team, target)
        if e[0] > 88 and abs(s[1] - 34) > 18 and abs(e[1] - 34) < 14:
            return "cross"
        if abs(e[1] - s[1]) > 35:
            return "switch"
        if r in self.runs:
            return "through_ball"
        return "long" if d > 32 else "short"

    def _launch_pass(
        self,
        c: int,
        r: int,
        target: np.ndarray,
        p: float,
        d: float,
        lofted: bool,
        lane: float,
        offside: bool,
        play_pattern: str = "open_play",
    ) -> None:
        team = self.team_of(c)
        self._close_carry(c)
        start = self.pos[c].copy()
        self.ball[:2] = start
        kind = self._pass_kind(team, start, target, r, d, lofted)
        if lofted:
            speed = float(np.clip(12 + 0.25 * d, 14, 28))
        else:
            speed = float(np.clip(7 + 0.38 * d, 8, 26))
        if play_pattern == "throw_in":
            speed = min(speed, 14.0)
        speed *= float(self.rng.uniform(0.92, 1.08))
        duration = d / speed
        under_pressure = len(self.pressed_by) > 0
        self.holder = None
        self.last_touch = team

        ev = self._event(
            "pass",
            team,
            c,
            start=start,
            end=target,
            outcome=None,
            duration=duration,
            recipient=self.slot_pid[r],
            length=round(d, 1),
            height="high" if lofted else "ground",
            kind=kind,
            speed=round(speed, 1),
            play_pattern=play_pattern,
            under_pressure=under_pressure,
        )

        if not offside and kind != "cross":
            line = self._offside_line(team)
            r_att = self.to_att(team, self.pos[r])
            t_att = self.to_att(team, target)
            if (
                r_att[0] > line - 1.5
                and t_att[0] > line - 1.5
                and t_att[0] > self.to_att(team, start)[0] + 5
            ):
                offside = bool(self.rng.random() < OFFSIDE_RISK)
        if offside:
            ev["outcome"] = "offside"
            self._event("offside", team, r, start=self.pos[r])
            self._set_dead(
                "free_kick", 1 - team, self.pos[r].copy(), delay=self._dead_time("offside")
            )
            return

        height = min(0.12 * d, 14.0) if lofted else 0.0
        if self.rng.random() < p:

            def arrive() -> None:
                ev["outcome"] = "complete"
                ev["end"] = self._pt(self.ball[:2])
                self.last_complete = (self.slot_pid[c], r, self.t, self.poss_id)
                self._give_ball(r, cause="pass")

            self.flight = Flight(start, target.copy(), self.t, duration, height, arrive, home_to=r)
        else:
            opp = self.team_slots(1 - team)
            opp = opp[~self.is_gk[opp]] if len(opp) > 1 else opp
            lane_d, lane_t = m.segment_distances(start, target, self.pos[opp])
            mask = (lane_t > 0.1) & (lane_t < 0.95)
            if mask.any() and float(lane_d[mask].min()) < INTERCEPT_LANE and not lofted:
                k = int(np.argmin(np.where(mask, lane_d, np.inf)))
                j = int(opp[k])
                point = start + (target - start) * lane_t[k]

                def intercepted() -> None:
                    ev["outcome"] = "incomplete"
                    ev["end"] = self._pt(self.ball[:2])
                    self._event("interception", 1 - team, j, start=self.ball[:2])
                    if (
                        self._in_box(team, self.ball[:2])
                        and self.rng.random() < CORNER_FROM_BOX_DEFENDING
                    ):
                        self._corner(team, self.ball[:2])
                    else:
                        self._give_ball(j, cause="interception")

                self.flight = Flight(
                    start, point, self.t, duration * float(lane_t[k]), 0.0, intercepted, home_to=j
                )
            else:
                err = 2.5 + 0.08 * d
                miss = target + self.rng.normal(0, err, 2)
                direction = (miss - start) / max(float(np.linalg.norm(miss - start)), 1e-6)

                def missed() -> None:
                    ev["outcome"] = "incomplete"
                    ev["end"] = self._pt(self.ball[:2])
                    self.pending_miss = ev
                    self._loose(self.ball[:2], direction * speed * 0.35, touch=team, keep_miss=True)

                self.flight = Flight(start, miss, self.t, duration, height, missed)
        self.mode = FLIGHT

    def _shoot(
        self,
        c: int,
        x: float,
        pressure: float,
        play_pattern: str = "open_play",
        header: bool = False,
        blockers: int = 0,
    ) -> None:
        team = self.team_of(c)
        self._close_carry(c)
        start = self.pos[c].copy() if self.holder == c else self.ball[:2].copy()
        start_att = self.to_att(team, start)
        dist = math.hypot(L - start_att[0], 34 - start_att[1])
        if header:
            speed = float(self.rng.uniform(9, 16))
        else:
            speed = float(np.clip(self.rng.normal(25, 4) - 0.1 * max(0.0, 20 - dist), 14, 35))
        fin = 0.8 + 0.4 * (self.r["finishing"][c] - 50) / 50
        gk = 11 * (1 - team)
        gk_mod = 1.15 - 0.3 * (self.r["goalkeeping"][gk] - 50) / 50 if self.active[gk] else 1.3
        p_goal = float(np.clip(x * fin * gk_mod, 0.0, 0.97))
        forced = self.force_goal[team] > 0
        r = self.rng.random()
        if forced or (r < p_goal and not self.scenario.scripted_goals_only):
            outcome = "goal"
        elif r < p_goal or r < p_goal + 0.24:
            outcome = "saved"
        elif r < p_goal + 0.24 + min(0.08 + 0.12 * blockers, 0.5):
            outcome = "blocked"
        else:
            outcome = "off_target"

        y_goal = 34 + self.rng.normal(0, 2.0)
        if outcome == "goal":
            end_att = np.array([L, np.clip(y_goal, 30.6, 37.4)])
            z = float(self.rng.uniform(0.1, 2.3))
        elif outcome == "saved":
            end_att = np.array([L - 0.8, np.clip(y_goal, 31, 37)])
            z = float(self.rng.uniform(0.2, 2.0))
        elif outcome == "blocked":
            frac = float(self.rng.uniform(0.15, 0.4))
            end_att = start_att + (np.array([L, y_goal]) - start_att) * frac
            z = 0.5
        else:
            side = 1 if self.rng.random() < 0.5 else -1
            end_att = np.array([L + 1.0, 34 + side * self.rng.uniform(3.9, 9.0)])
            z = float(self.rng.uniform(0.3, 4.0))
        end = self.to_abs(team, end_att)
        duration = float(np.linalg.norm(end - start)) / speed
        self.holder = None
        self.last_touch = team

        ev = self._event(
            "shot",
            team,
            c,
            start=start,
            end=end,
            outcome=outcome,
            duration=duration,
            xg=round(x, 3),
            speed=round(speed, 1),
            body="head" if header else "foot",
            distance=round(dist, 1),
            play_pattern=play_pattern,
            under_pressure=pressure > 0.3,
            end_z=round(z, 2),
        )

        def arrive() -> None:
            if outcome == "goal":
                if forced:
                    self.force_goal[team] -= 1
                self._goal(c, team, ev)
            elif outcome == "saved":
                self._event("save", 1 - team, gk, start=self.ball[:2], on=self.slot_pid[c])
                rr = self.rng.random()
                if rr < 0.55:
                    self._give_ball(gk, cause="save", timer=float(self.rng.uniform(4, 8)))
                elif rr < 0.85:
                    self._corner(team, end)
                else:
                    rebound = self._dir_vec(team, -1) * 6 + self.rng.normal(0, 3, 2)
                    self._loose(self.ball[:2], rebound, touch=1 - team)
            elif outcome == "blocked":
                blocker = self._nearest(1 - team, self.ball[:2], outfield=True)
                if blocker is not None:
                    self._event("block", 1 - team, blocker, start=self.ball[:2])
                if self.rng.random() < 0.35:
                    self._corner(team, end)
                else:
                    v = self.rng.normal(0, 5, 2)
                    self._loose(self.ball[:2], v, touch=1 - team)
            else:
                self._goal_kick(1 - team)

        self.flight = Flight(
            start, end, self.t, duration, 4 * z / 3 if z > 0.5 else 0.0, arrive, out_ok=True
        )
        self.mode = FLIGHT

    def _dir_vec(self, team: int, sign: int) -> np.ndarray:
        """Unit vector along the pitch, towards the opponent goal (sign=1) or own goal (-1)."""
        return np.array([self.dir[team] * sign, 0.0])

    def _dribble(self, c: int, j: int, p: float) -> None:
        team = self.team_of(c)
        if self.rng.random() < p:
            self._event(
                "dribble", team, c, start=self.pos[c], outcome="complete", past=self.slot_pid[j]
            )
            self.stun_until[j] = self.t + BEATEN_STUN
            self.timer = float(self.rng.uniform(0.6, 1.4))
            self._new_carry_target(c, drive=True)
        else:
            self._close_carry(c)
            self._event(
                "dribble", team, c, start=self.pos[c], outcome="incomplete", past=self.slot_pid[j]
            )
            self._event(
                "tackle", 1 - team, j, start=self.pos[j], outcome="won", on=self.slot_pid[c]
            )
            self._give_ball(j, cause="tackle")

    def _clear(self, c: int) -> None:
        team = self.team_of(c)
        self._close_carry(c)
        start = self.pos[c].copy()
        att = self.to_att(team, start)
        end_att = np.array([att[0] + self.rng.uniform(30, 55), self.rng.uniform(-8, 76)])
        end = self.to_abs(team, end_att)
        d = float(np.linalg.norm(end - start))
        self._event("clearance", team, c, start=start, end=end)
        self.holder = None
        self.last_touch = team
        direction = (end - start) / max(d, 1e-6)
        speed = 22.0
        self.flight = Flight(
            start,
            end,
            self.t,
            d / speed,
            min(0.15 * d, 16.0),
            lambda: self._loose(self.ball[:2], direction * 5, touch=team),
        )
        self.mode = FLIGHT

    def _goal(self, c: int, team: int, shot_ev: dict) -> None:
        self.score[team] += 1
        assist = None
        if (
            self.last_complete
            and self.last_complete[1] == c
            and self.last_complete[3] == self.poss_id
            and self.t - self.last_complete[2] < 20
        ):
            assist = self.last_complete[0]
        self._event(
            "goal",
            team,
            c,
            start=self.ball[:2],
            assist=assist,
            shot=shot_ev["id"],
            score={"home": self.score[0], "away": self.score[1]},
        )
        self._set_dead(
            "kick_off", 1 - team, np.array([L / 2, W / 2]), delay=self._dead_time("goal")
        )

    # ------------------------------------------------------------------ ball: flight & loose

    def _step_flight(self) -> None:
        f = self.flight
        assert f is not None
        s = (self.t - f.t0) / max(f.duration, DT)
        if f.home_to is not None and s > 0.3:
            f.end = f.end + (self.pos[f.home_to] - f.end) * min(1.0, s)
        s1 = min(s, 1.0)
        self.ball[:2] = f.start + (f.end - f.start) * s1
        self.ball[2] = 4 * f.height * s1 * (1 - s1)
        if not f.out_ok and self._out_of_bounds():
            self._out_of_play()
            return
        if s >= 1.0:
            self.ball[2] = 0.0
            f.on_arrive()

    def _loose(self, at: np.ndarray, vel: np.ndarray, touch: int, keep_miss: bool = False) -> None:
        self.mode = LOOSE
        self.holder = None
        self.flight = None
        self.ball[:2] = at
        self.ball[2] = 0.0
        self.loose_vel = np.asarray(vel, dtype=float)
        self.last_touch = touch
        if not keep_miss:
            self.pending_miss = None

    def _step_loose(self) -> None:
        self.ball[:2] += self.loose_vel * DT
        self.loose_vel *= 0.94
        if self._out_of_bounds():
            self._out_of_play()
            return
        idx = np.flatnonzero(self.active & (self.stun_until <= self.t))
        d = np.linalg.norm(self.pos[idx] - self.ball[:2], axis=1)
        k = int(np.argmin(d))
        if d[k] < 1.0:
            slot = int(idx[k])
            team = self.team_of(slot)
            if team != self.poss_team:
                self._event("ball_recovery", team, slot, start=self.ball[:2])
            self._give_ball(slot, cause="loose_ball")

    def _in_box(self, team: int, xy: np.ndarray) -> bool:
        """Is xy inside the penalty area `team` is attacking?"""
        a = self.to_att(team, xy)
        return bool(m.in_penalty_area(a[0], a[1]))

    def _out_of_bounds(self) -> bool:
        x, y = self.ball[:2]
        return x < 0 or x > L or y < 0 or y > W

    def _out_of_play(self) -> None:
        x, y = float(self.ball[0]), float(self.ball[1])
        if self.pending_miss is not None:
            self.pending_miss["outcome"] = "out"
            self.pending_miss = None
        last = self.last_touch
        if y < 0 or y > W:
            at = np.array([np.clip(x, 0.5, L - 0.5), 0.0 if y < 0 else W])
            self._set_dead("throw_in", 1 - last, at, delay=self._dead_time("throw_in"))
            return
        # Over a goal line: whose goal is it?
        end_x = 0.0 if x < 0 else L
        defender = 0 if self.to_att(0, np.array([end_x, 34.0]))[0] < L / 2 else 1
        if last == defender or self.rng.random() < DEFLECTION_CORNER:
            self._corner(1 - defender, np.array([end_x, y]))
        else:
            self._goal_kick(defender)

    def _corner(self, team: int, near: np.ndarray) -> None:
        end_x = L if self.dir[team] == 1 else 0.0
        y = 0.0 if near[1] < W / 2 else W
        self._set_dead("corner", team, np.array([end_x, y]), delay=self._dead_time("corner"))

    def _goal_kick(self, team: int) -> None:
        at = self.to_abs(team, np.array([5.5, 34.0 + self.rng.choice([-9.0, 9.0])]))
        self._set_dead("goal_kick", team, at, delay=self._dead_time("goal_kick"))

    # ------------------------------------------------------------------ dead ball & restarts

    def _dead_time(self, kind: str) -> float:
        lo, hi = DEAD_TIME[kind]
        return float(self.rng.uniform(lo, hi))

    def _set_dead(self, kind: str, team: int, at: np.ndarray, delay: float) -> None:
        self.mode = DEAD
        self.holder = None
        self.flight = None
        self.runs.clear()
        self.ball[:2] = at
        self.ball[2] = 0.0
        if team != self.poss_team:
            self.poss_id += 1
            self.poss_team = team
            self._event("possession_change", team, None, start=at, outcome=kind)
        self.restart = Restart(kind, team, np.asarray(at, dtype=float), self.t + delay)
        if kind not in ("penalty",) and self.period == 2:
            self._maybe_subs()

    def _restart_taker(self) -> int:
        rs = self.restart
        assert rs is not None
        if rs.taker is not None and self.active[rs.taker]:
            return rs.taker
        if rs.kind == "goal_kick":
            rs.taker = rs.team * 11
        elif rs.kind == "penalty":
            idx = self.team_slots(rs.team)
            idx = idx[~self.is_gk[idx]]
            rs.taker = int(idx[np.argmax(self.r["finishing"][idx])])
        else:
            rs.taker = self._nearest(rs.team, rs.at, outfield=True)
        assert rs.taker is not None
        return rs.taker

    def _step_dead(self) -> None:
        rs = self.restart
        assert rs is not None
        taker = self._restart_taker()
        if self.t < rs.until:
            return
        if np.linalg.norm(self.pos[taker] - rs.at) > 1.5:
            if self.t < rs.until + 8:
                return
            self.pos[taker] = rs.at
        self.pos[taker] = rs.at
        self.vel[taker] = 0
        self.restart = None
        self._take_restart(rs, taker)

    def _take_restart(self, rs: Restart, c: int) -> None:
        team = rs.team
        self.mode = HELD
        self.holder = c
        self.last_touch = team
        self.carry_start = self.pos[c].copy()
        self.carry_t0 = self.t
        self.pressed_by = set()
        att = self.to_att(team, rs.at)

        if rs.kind == "penalty":
            self._shoot(c, 0.78, 0.0, play_pattern="penalty")
            return
        if rs.kind == "corner":
            self._take_corner(c, team)
            return
        if rs.kind == "free_kick":
            dist = math.hypot(L - att[0], 34 - att[1])
            if dist < 30 and abs(att[1] - 34) < 20 and self.rng.random() < 0.35:
                self._shoot(c, m.xg(att[0], att[1]) * 0.6, 0.0, play_pattern="free_kick")
                return

        if rs.kind == "kick_off":
            opts = self._pass_options(c, min_d=3.0, max_d=20.0)
        elif rs.kind == "throw_in":
            opts = self._pass_options(c, min_d=4.0, max_d=25.0)
        elif rs.kind == "goal_kick":
            long_ball = self.rng.random() < self.tac[team].directness
            opts = self._pass_options(
                c, min_d=35.0 if long_ball else 6.0, max_d=70.0 if long_ball else 30.0
            )
        else:
            opts = self._pass_options(c)
        opts = [o for o in opts if not o[2]["offside"]] or opts
        if not opts:
            self._give_ball(c, cause=rs.kind)
            return
        _, _, data = self._softmax_pick(opts, c)
        self._launch_pass(c, play_pattern=rs.kind, **data)

    def _take_corner(self, c: int, team: int) -> None:
        start = self.pos[c].copy()
        target_att = np.array([self.rng.uniform(94, 101), self.rng.uniform(28, 40)])
        target = self.to_abs(team, target_att)
        attacker = self._nearest(team, target, outfield=True, exclude=c)
        defender = self._nearest(1 - team, target, outfield=True)
        d = float(np.linalg.norm(target - start))
        ev = self._event(
            "pass",
            team,
            c,
            start=start,
            end=target,
            duration=d / 20,
            recipient=self.slot_pid[attacker] if attacker is not None else None,
            length=round(d, 1),
            height="high",
            kind="cross",
            speed=20.0,
            play_pattern="corner",
            under_pressure=False,
        )
        self.holder = None
        r = self.rng.random()

        def arrive() -> None:
            if attacker is not None and r < 0.28:
                ev["outcome"] = "complete"
                self.holder = None
                self.pos[attacker] = self.ball[:2]
                x = m.xg(target_att[0], target_att[1], pressure=0.4, header=True)
                self.carry_start = self.pos[attacker].copy()
                self.carry_t0 = self.t
                self._shoot(attacker, x, 0.4, play_pattern="corner", header=True)
            elif defender is not None and r < 0.8:
                ev["outcome"] = "incomplete"
                self._event("clearance", 1 - team, defender, start=self.ball[:2])
                if self.rng.random() < CORNER_FROM_BOX_DEFENDING:
                    self._corner(team, self.ball[:2])
                    return
                v = self._dir_vec(1 - team, 1) * self.rng.uniform(10, 18) + self.rng.normal(0, 4, 2)
                self._loose(self.ball[:2], v, touch=1 - team)
            else:
                ev["outcome"] = "incomplete"
                self._give_ball(11 * (1 - team), cause="claim", timer=float(self.rng.uniform(4, 8)))

        self.flight = Flight(start, target, self.t, d / 20, 6.0, arrive)
        self.mode = FLIGHT

    def _maybe_subs(self) -> None:
        minute = self.minute()
        for team in (0, 1):
            ts = self.tac[team]
            made_now = 0
            while (
                ts.subs_made < len(ts.subs_plan)
                and minute >= ts.subs_plan[ts.subs_made]
                and made_now < 2
                and self.bench[team]
            ):
                idx = [k for k in self.team_slots(team) if not self.is_gk[k]]
                if not idx:
                    break
                tired = np.array(
                    [
                        self.stamina[k]
                        - (0.05 if self.slot_label[k] in ("ST", "LW", "RW", "AM") else 0)
                        for k in idx
                    ]
                )
                off = idx[int(np.argmin(tired))]
                label = self.slot_label[off]
                bench_players = [self.players[p] for p in self.bench[team]]
                same = [p for p in bench_players if p.position == label and p.position != "GK"]
                fallback = [p for p in bench_players if p.position != "GK"]
                if not (same or fallback):
                    break
                on = (same or fallback)[0]
                self.bench[team].remove(on.id)
                off_pid = self.slot_pid[off]
                self.slot_pid[off] = on.id
                self._load_ratings([off])
                self.stamina[off] = 1.0
                self.rosters.append(list(self.slot_pid))
                self._event("sub", team, None, start=self.pos[off], off=off_pid, on=on.id)
                ts.subs_made += 1
                made_now += 1
                if self.restart is not None:
                    self.restart.until += SUB_DELAY
