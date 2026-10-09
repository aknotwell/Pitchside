"""Synthetic match engine: an agent-based simulation in which events emerge from movement.

Every tick (10 Hz) the engine moves 22 players and the ball together:

* Players follow a team shape that reacts to the ball, the team's tactical style
  (press, line height, width, tempo, directness) and their own role (overlapping
  full-back, false nine, ball-winner...). Off the ball, attackers find space and
  time runs in behind; defenders close down the carrier, cover passing lanes and
  mark the attackers near them.
* The ball carrier keeps the ball moving (first touch, dribble, shield) and decides
  by weighing passes, carries and shots against what is actually around them:
  passing lanes, the nearest opponents, the angle to goal. A softmax over those
  utilities, with a temperature set by vision and flair, gives every player their
  own decision-making.
* Pressure, tackles, fouls, interceptions, recoveries, saves and goals are resolved
  from positions: a defender has to be there to press, tackle or cut out a pass.

The engine is fully deterministic for a given (seed, params).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

from matchlens.contracts import (
    HALF_LENGTH,
    HALF_WIDTH,
    Frame,
    MatchEvent,
    MatchMetadata,
    PlayerPos,
    StreamMessage,
    TrackingBatch,
)
from matchlens.sim.params import load_params, params_version
from matchlens.world import FORMATIONS, TRAITS, make_fixture

TEAMS = ("h", "a")
SIGN = {"h": 1.0, "a": -1.0}
GOAL_HALF = 3.66
BOX_X = HALF_LENGTH - 16.5
BOX_Y = 20.16

OWNED, FLIGHT, LOOSE, DEAD = "owned", "flight", "loose", "dead"

# How often a role makes a run in behind (multiplies run_prob_per_s), and how it shifts the shape.
RUN_RATE = {
    "poacher": 2.2, "pressing forward": 1.4, "target forward": 0.5, "false nine": 0.7,
    "inverted winger": 1.6, "touchline winger": 1.4, "trickster": 1.0,
    "overlapping full-back": 1.0, "box-to-box": 1.2, "playmaker": 0.4, "inverted full-back": 0.2,
}
POSS_DX = {
    "false nine": -9.0, "target forward": 2.0, "poacher": 3.0, "overlapping full-back": 11.0,
    "inverted full-back": 5.0, "defensive full-back": -3.0, "box-to-box": 5.0, "deep-lying playmaker": -3.0,
    "anchor": -4.0, "ball-playing defender": 1.0, "inverted winger": 2.0,
}
POSS_WY = {"inverted winger": 0.72, "touchline winger": 1.1, "inverted full-back": 0.45, "false nine": 0.6}
DEF_DX = {"pressing forward": 4.0, "ball-winner": 2.0, "poacher": 6.0, "touchline winger": 2.0,
          "covering defender": -2.0, "trickster": 3.0}


def _dist(pts: np.ndarray, p: np.ndarray) -> np.ndarray:
    return np.hypot(pts[:, 0] - p[0], pts[:, 1] - p[1])


def other(team: str) -> str:
    return "a" if team == "h" else "h"


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def shot_geometry(xa: float, ya: float) -> tuple[float, float]:
    """Distance to goal centre and the angle subtended by the goal mouth (radians)."""
    dx = max(0.3, HALF_LENGTH - xa)
    dist = math.hypot(dx, ya)
    a1 = math.atan2(ya + GOAL_HALF, dx)
    a2 = math.atan2(ya - GOAL_HALF, dx)
    return dist, abs(a1 - a2)


def threat(xa: float, ya: float) -> float:
    """Value of having the ball at a point (team-attacking frame), a smooth expected-threat surface."""
    d = math.hypot(HALF_LENGTH - xa, ya)
    prog = min(1.0, max(0.0, (xa + HALF_LENGTH) / (2 * HALF_LENGTH)))
    return 0.004 + 0.30 * math.exp(-d / 11.0) + 0.05 * prog * prog


def turnover_cost(xa: float) -> float:
    """What losing the ball here costs: much more in front of your own goal."""
    return 0.02 + 0.10 * math.exp(-(xa + HALF_LENGTH) / 25.0)


def display_clock(clock_s: float, period: int, period_start_s: float, period_min: float = 45) -> str:
    regulation = period_min * 60
    base = 0 if period == 1 else regulation
    elapsed = clock_s - period_start_s
    if elapsed <= regulation:
        total = base + elapsed
        return f"{int(total // 60):02d}:{int(total % 60):02d}"
    extra = elapsed - regulation
    return f"{int((base + regulation) // 60)}+{int(extra // 60) + 1}:{int(extra % 60):02d}"


@dataclass
class SimResult:
    metadata: MatchMetadata
    events: list[MatchEvent]
    tracking_t: np.ndarray  # (T,)
    tracking_ball: np.ndarray  # (T, 3)
    tracking_players: np.ndarray  # (T, P, 2), NaN when off the pitch
    player_ids: list[str]
    period_bounds: list[tuple[float, float]]

    def stream(self, batch_s: float = 1.0) -> Iterator[StreamMessage]:
        """Merge events and 1 s tracking batches in clock order."""
        mid = self.metadata.match_id
        yield StreamMessage(kind="metadata", match_id=mid, clock_s=0.0, payload=self.metadata.model_dump(mode="json"))
        ev_i = 0
        n_ev = len(self.events)
        fps = self.metadata.fps
        frames_per_batch = max(1, int(round(batch_s * fps)))
        T = len(self.tracking_t)
        for start in range(0, T, frames_per_batch):
            end = min(T, start + frames_per_batch)
            t_end = float(self.tracking_t[end - 1])
            while ev_i < n_ev and self.events[ev_i].clock_s <= t_end:
                e = self.events[ev_i]
                yield StreamMessage(kind="event", match_id=mid, clock_s=e.clock_s, payload=e.model_dump(mode="json"))
                ev_i += 1
            frames = []
            for k in range(start, end):
                players = [
                    PlayerPos(id=pid, x=round(float(p[0]), 2), y=round(float(p[1]), 2))
                    for pid, p in zip(self.player_ids, self.tracking_players[k], strict=True)
                    if not math.isnan(p[0])
                ]
                b = self.tracking_ball[k]
                frames.append(
                    Frame(
                        t_s=round(float(self.tracking_t[k]), 2),
                        ball=(round(float(b[0]), 2), round(float(b[1]), 2), round(float(b[2]), 2)),
                        players=players,
                    )
                )
            period = 1 if float(self.tracking_t[start]) < self.period_bounds[0][1] + 1e-6 else 2
            batch = TrackingBatch(match_id=mid, period=period, t0_s=float(self.tracking_t[start]), fps=fps, frames=frames)
            yield StreamMessage(kind="tracking", match_id=mid, clock_s=batch.t0_s, payload=batch.model_dump(mode="json"))
        while ev_i < n_ev:
            e = self.events[ev_i]
            yield StreamMessage(kind="event", match_id=mid, clock_s=e.clock_s, payload=e.model_dump(mode="json"))
            ev_i += 1
        last = self.events[-1].clock_s if self.events else 0.0
        yield StreamMessage(kind="end", match_id=mid, clock_s=last, payload={"match_id": mid})


@dataclass
class _TeamState:
    tid: str
    on: list[int]  # slot -> player index
    slot_pos: list[str]
    anchors: np.ndarray  # (11, 2) team-attacking frame
    style: dict[str, float]
    strength: float
    bench: list[int] = field(default_factory=list)
    subs_at: list[float] = field(default_factory=list)
    # per-slot role effects, refreshed when the lineup changes
    poss_dx: np.ndarray = field(default_factory=lambda: np.zeros(11))
    poss_wy: np.ndarray = field(default_factory=lambda: np.ones(11))
    def_dx: np.ndarray = field(default_factory=lambda: np.zeros(11))
    run_rate: np.ndarray = field(default_factory=lambda: np.zeros(11))
    A: np.ndarray = field(default_factory=lambda: np.zeros(11, dtype=int))
    back_line: np.ndarray = field(default_factory=lambda: np.zeros(11, dtype=bool))


@dataclass
class _Flight:
    kind: str  # pass | shot
    t0: float
    t1: float
    start: np.ndarray
    end: np.ndarray
    z: float
    team: str
    kicker: int
    receiver: int = -1
    speed: float = 15.0
    lofted: bool = False
    result: str = ""  # shots: goal | catch | parry | parry_corner | block | block_corner | wide


@dataclass
class _Restart:
    kind: str
    team: str
    spot: np.ndarray
    ready_t: float
    taker: int


class MatchEngine:
    def __init__(self, seed: int, match_id: str | None = None, params: dict | None = None, fps: int = 10):
        self.seed = seed
        self.p = load_params(params)
        self.fps = fps
        self.dt = 1.0 / fps
        self.match_id = match_id or f"m{seed:05d}"
        self.rng = np.random.default_rng(seed)
        home, away = make_fixture(seed)
        self.teams = {"h": home, "a": away}
        players = [pl for tid in TEAMS for pl in self.teams[tid].players]
        self.players = players
        self.ids = [pl.id for pl in players]
        self.idx = {pid: i for i, pid in enumerate(self.ids)}
        self.P = len(players)
        self.tid_of = [pl.team_id for pl in players]
        self.role = [pl.role for pl in players]
        self.is_gk = np.array([pl.position == "GK" for pl in players])
        self.tr = {n: np.array([pl.traits.get(n, 50) / 100.0 for pl in players]) for n in TRAITS}
        lo, hi = self.p["max_speed_mps"]
        pace = np.clip((self.tr["pace"] - 0.35) / 0.55, 0.0, 1.0)
        self.vmax = (lo + (hi - lo) * pace + self.rng.normal(0, 0.15, self.P)) * np.where(self.is_gk, 0.85, 1.0)
        self.amax = self.p["max_accel_mps2"] * (0.8 + 0.4 * self.tr["acceleration"])
        self.ts: dict[str, _TeamState] = {}
        for tid, team in self.teams.items():
            form = FORMATIONS[team.formation]
            st = _TeamState(
                tid=tid,
                on=[self.idx[pl.id] for pl in team.players[:11]],
                slot_pos=[pos for pos, _, _ in form],
                anchors=np.array([(ax, ay) for _, ax, ay in form]),
                style=dict(team.style) or {"press": 0.5, "line": 0.5, "width": 0.55, "tempo": 1.0, "directness": 0.45},
                strength=1.0 + float(self.rng.normal(0, self.p["strength_sd"])),
                bench=[self.idx[pl.id] for pl in team.players[11:]],
            )
            self.ts[tid] = st
        for st in self.ts.values():
            self._refresh_roles(st)
        self.events: list[MatchEvent] = []
        self.seq = 0
        self.period = 1
        self.period_start = 0.0
        self.period_bounds: list[tuple[float, float]] = []
        self.score = {"h": 0, "a": 0}
        self._result: tuple[np.ndarray, np.ndarray, np.ndarray, list[str]] | None = None
        self.decision_log: list[dict] | None = None  # set to [] to record every on-ball decision

    # ------------------------------------------------------------------ helpers
    def _refresh_roles(self, st: _TeamState) -> None:
        st.A = np.array(st.on)
        st.back_line = np.array([pos in ("CB", "RB", "LB") or (pos == "DM" and k == 6) for k, pos in enumerate(st.slot_pos)])
        if len(self.ts) == 2:
            self._act = np.concatenate([self.ts["h"].A, self.ts["a"].A])
            self._slot_index = {int(i): k for k, i in enumerate(self._act)}
        roles = [self.role[i] for i in st.on]
        st.poss_dx = np.array([POSS_DX.get(r, 0.0) for r in roles])
        st.poss_wy = np.array([POSS_WY.get(r, 1.0) for r in roles])
        st.def_dx = np.array([DEF_DX.get(r, 0.0) for r in roles])
        st.run_rate = np.array([RUN_RATE.get(r, 0.0) if st.slot_pos[s] not in ("GK", "CB") else 0.0
                                for s, r in enumerate(roles)])
        for s, r in enumerate(roles):
            if st.run_rate[s] == 0.0 and st.slot_pos[s] in ("CM", "RW", "LW", "RM", "LM", "ST"):
                st.run_rate[s] = 0.6

    def _emit(self, t: float, etype: str, team: str | None = None, player: int | None = None, **kw) -> MatchEvent:
        self.seq += 1
        for k in ("x", "y", "end_x", "end_y"):
            if kw.get(k) is not None:
                lim = HALF_LENGTH if k.endswith("x") else HALF_WIDTH
                kw[k] = round(max(-lim, min(lim, float(kw[k]))), 2)
        ev = MatchEvent(
            event_id=f"{self.match_id}-e{self.seq:05d}",
            match_id=self.match_id,
            seq=self.seq,
            period=self.period,
            clock_s=round(t, 2),
            display_clock=display_clock(t, self.period, self.period_start, self.p["period_min"]),
            type=etype,  # type: ignore[arg-type]
            team_id=team,
            player_id=None if player is None else self.ids[player],
            **kw,
        )
        self.events.append(ev)
        return ev

    def _minute(self, t: float) -> float:
        """Minute on a 90-minute scale, so scripted stories fire at the same phase in short matches."""
        frac = (t - self.period_start) / (self.p["period_min"] * 60)
        return 45 * frac + (0 if self.period == 1 else 45)

    def _style(self, tid: str, t: float) -> dict[str, float]:
        if t != self._sty_t:
            self._sty_t = t
            self._sty = {k: self._compute_style(k, t) for k in TEAMS}
        return self._sty[tid]

    def _compute_style(self, tid: str, t: float) -> dict[str, float]:
        sty = dict(self.ts[tid].style)
        minute = self._minute(t)
        for s in self.p["script"]:
            if s["team"] == tid and s["from_min"] <= minute < s["to_min"]:
                sty["press"] = sty["press"] + s.get("press", 0.0)
                sty["tempo"] = sty["tempo"] + s.get("tempo", 0.0)
                sty["line"] = sty["line"] + 0.5 * s.get("press", 0.0)
        diff = self.score[tid] - self.score[other(tid)]
        if diff < 0 and minute > 60:  # chasing the game
            urgency = min(1.0, (minute - 60) / 25) * (0.7 if diff == -1 else 1.0)
            sty["press"] += 0.2 * urgency
            sty["line"] += 0.2 * urgency
            sty["directness"] += 0.2 * urgency
            sty["tempo"] += 0.12 * urgency
        elif diff > 0 and minute > 70:  # seeing it out
            sty["line"] -= 0.15
            sty["press"] -= 0.1
            sty["tempo"] -= 0.06
        return sty

    def _att(self, tid: str, xy: np.ndarray) -> np.ndarray:
        return xy * SIGN[tid]

    def _gk(self, tid: str) -> int:
        return self.ts[tid].on[0]

    def _active(self) -> np.ndarray:
        return self._act

    # ------------------------------------------------------------------ main loop
    def _simulate(self) -> None:
        if self._result is not None:
            return
        p, rng = self.p, self.rng
        dt = self.dt
        period_s = p["period_min"] * 60
        max_T = int((2 * period_s + 2 * p["stoppage_s"][1] * p["period_min"] / 45 + 600) * self.fps)
        frames_ball = np.zeros((max_T, 3))
        frames_pl = np.full((max_T, self.P, 2), np.nan, dtype=np.float32)
        P = self.P
        self.pos = np.full((P, 2), np.nan)
        self.vel = np.zeros((P, 2))
        self.ball = np.zeros(3)
        self.ball_vel = np.zeros(2)
        self.drift = np.zeros((P, 2))
        self.run_until = np.zeros(P)
        self.run_tgt = np.zeros((P, 2))
        self.beaten_until = np.zeros(P)
        self.yellow = np.zeros(P, dtype=bool)
        self.entered = np.zeros(P)
        self.flight: _Flight | None = None
        self.restart: _Restart | None = None
        self.pending: dict | None = None
        self.owner = -1
        self.poss = "h"
        self.last_touch = "h"
        self.state = DEAD
        self.loose_lock = 0.0
        self.in_hands = False
        self.setpiece: str | None = None
        self.force_shot = False
        self.header_chance = False
        self.shielding = False
        self.sp_kind: str | None = None
        self.sp_until = 0.0
        self.gained_t = 0.0
        self._sty_t = -1.0
        self._tick = 0
        tm = np.array([0] * 11 + [1] * 11)
        self._same = tm[:, None] == tm[None, :]
        self._eye1e3 = np.eye(22) * 1e3
        self._tgt: np.ndarray | None = None
        self._urg = np.zeros(P)
        self.support: dict[int, np.ndarray] = {}
        self.support_t = -1e9
        self.support_owner = -1
        self._sty: dict[str, dict[str, float]] = {}
        tau = max(0.5, float(p["drift_tau_s"]))
        self._drift_decay = dt / tau
        self._drift_sd = float(p["drift_m"]) * math.sqrt(2 * dt / tau) * (0.6 + 0.8 * self.tr["flair"])

        k = 0
        first = "h" if rng.random() < 0.5 else "a"
        for period in (1, 2):
            self.period = period
            t = k * dt
            self.period_start = t
            stoppage = rng.uniform(*p["stoppage_s"]) * (0.6 if period == 1 else 1.0) * p["period_min"] / 45
            end_t = t + period_s + stoppage
            self._emit(t, "period_start", qualifiers={"period": period})
            if period == 2:
                self._schedule_subs(t)
            self._kickoff(t, first if period == 1 else other(first), delay=1.0, teleport=True)
            while True:
                t = k * dt
                self._step(t)
                frames_ball[k] = self.ball
                frames_pl[k] = self.pos
                k += 1
                over = t >= end_t and self.state in (OWNED, DEAD) and self.pending is None
                if over or t >= end_t + 30 or k >= max_T:
                    break
            if self.state == OWNED:
                self._end_carry(t)
            if self.pending is not None:
                self._resolve_pass("out", t)
            self._emit(t, "period_end", qualifiers={"period": period})
            self.period_bounds.append((self.period_start, t))

        # Pass and carry events are emitted when they resolve, stamped with their start time.
        self.events.sort(key=lambda e: (e.clock_s, e.seq))
        for n, e in enumerate(self.events, start=1):
            e.seq = n
            e.event_id = f"{self.match_id}-e{n:05d}"
        T = k
        ts = np.arange(T) * dt
        self._result = (ts, frames_ball[:T], frames_pl[:T], list(self.ids))

    def _step(self, t: float) -> None:
        if self.state == DEAD:
            self._dead_step(t)
        elif self.state == OWNED:
            self._owned_step(t)
        elif self.state == FLIGHT:
            self._flight_step(t)
        else:
            self._loose_step(t)
        self._move(t)
        self._ball_step(t)

    # ------------------------------------------------------------------ restarts
    def _kickoff(self, t: float, team: str, delay: float | None = None, teleport: bool = False) -> None:
        if teleport:
            for tid in TEAMS:
                st = self.ts[tid]
                for s, i in enumerate(st.on):
                    ax, ay = st.anchors[s]
                    x = min(ax * 0.9, -1.0)
                    if tid == team and st.slot_pos[s] == "ST" and abs(ay) < 8:
                        x = -0.5
                    self.pos[i] = np.array([x, ay * 0.9]) * SIGN[tid]
                    self.vel[i] = 0.0
            self.ball[:] = 0.0
            self.ball_vel[:] = 0.0
        self._set_dead(t, "kickoff", team, np.zeros(2), delay=delay)

    def _taker(self, kind: str, team: str, spot: np.ndarray) -> int:
        st = self.ts[team]
        outfield = st.on[1:]
        if kind in ("goal_kick",):
            return st.on[0]
        if kind == "kickoff":
            fwd = [i for s, i in enumerate(st.on) if st.slot_pos[s] in ("ST", "CM")]
            return fwd[-1]
        if kind == "corner":
            return max(outfield, key=lambda i: self.tr["crossing"][i] + 0.2 * self.tr["passing"][i])
        if kind == "penalty":
            return max(outfield, key=lambda i: self.tr["finishing"][i] + 0.5 * self.tr["composure"][i])
        if kind == "free_kick":
            xa = self._att(team, spot)[0]
            if xa > 22 and abs(spot[1]) < 22:
                return max(outfield, key=lambda i: self.tr["long_shots"][i] + 0.5 * self.tr["passing"][i])
        d = np.linalg.norm(self.pos[outfield] - spot, axis=1)
        return outfield[int(np.argmin(d))]

    def _set_dead(self, t: float, kind: str, team: str, spot: np.ndarray, delay: float | None = None) -> None:
        p, rng = self.p, self.rng
        if self.state == OWNED:
            self._end_carry(t)
        self.state = DEAD
        self.owner = -1
        self.flight = None
        self.poss = team
        self.run_until[:] = 0.0
        if delay is None:
            delay = rng.uniform(*p["restart_s"][kind])
        delay += self._apply_subs(t)
        spot = np.array(spot, dtype=float)
        self.restart = _Restart(kind, team, spot, t + delay, self._taker(kind, team, spot))

    def _dead_step(self, t: float) -> None:
        r = self.restart
        assert r is not None
        if t < r.ready_t:
            return
        d = float(np.linalg.norm(self.pos[r.taker] - r.spot))
        if d > 1.5 and t < r.ready_t + 4.0:
            return
        if d > 1.5:  # out of patience: the taker is placed on the ball
            self.pos[r.taker] = r.spot - np.array([SIGN[r.team] * 0.6, 0.0])
        self.ball[:2] = r.spot
        self.ball[2] = 0.0
        self.restart = None
        self.sp_kind, self.sp_until = r.kind, (t + 3.0 if r.kind in ("corner", "penalty") else t)
        self._gain(r.taker, t, setpiece=r.kind)

    def _schedule_subs(self, t0: float) -> None:
        for tid in TEAMS:
            st = self.ts[tid]
            st.subs_at = sorted(t0 + self.rng.uniform(12, 40) * 60 * self.p["period_min"] / 45 for _ in range(3))

    def _apply_subs(self, t: float) -> float:
        """Substitutions happen at stoppages: the most tired outfield player comes off."""
        extra = 0.0
        for tid in TEAMS:
            st = self.ts[tid]
            while st.subs_at and st.subs_at[0] <= t and st.bench:
                st.subs_at.pop(0)
                tired = [(t - self.entered[i]) * (1.25 - self.tr["stamina"][i]) + 300 * self.yellow[i]
                         + self.rng.uniform(0, 400) for i in st.on[1:]]
                slot = 1 + int(np.argmax(tired))
                out_i = st.on[slot]
                group = st.slot_pos[slot]
                fits = [b for b in st.bench if self.players[b].position == group and self.players[b].position != "GK"]
                cands = fits or [b for b in st.bench if self.players[b].position != "GK"]
                if not cands:
                    continue
                in_i = cands[0]
                st.bench.remove(in_i)
                self._emit(t, "substitution", tid, out_i, related_player_id=self.ids[in_i])
                st.on[slot] = in_i
                self.pos[in_i] = np.array([self.rng.uniform(-3, 3), -HALF_WIDTH - 1.0])
                self.vel[in_i] = 0.0
                self.entered[in_i] = t
                self.pos[out_i] = np.nan
                self.vel[out_i] = 0.0
                self._refresh_roles(st)
                self._tgt = None
                extra = max(extra, self.rng.uniform(*self.p["restart_s"]["sub"]))
        return extra

    # ------------------------------------------------------------------ possession
    def _gain(self, i: int, t: float, setpiece: str | None = None, how: str | None = None) -> None:
        p, rng = self.p, self.rng
        tid = self.tid_of[i]
        if self.pending is not None:
            self._resolve_pass("complete" if self.pending["team"] == tid else "intercepted", t, receiver=i)
        if self.poss != tid:
            self.run_until[:] = 0.0
        self.state = OWNED
        self.owner = i
        self.poss = tid
        self.last_touch = tid
        self.flight = None
        self.setpiece = setpiece
        self.in_hands = how == "catch"
        self.force_shot = how == "header_chance" or setpiece == "penalty"
        self.header_chance = how == "header_chance"
        self.pressed: set[int] = set()
        self.carry_start = (t, self.ball[:2].copy())
        self.gained_t = t
        self.dribbles = 0
        tempo = self._style(tid, t)["tempo"]
        comp = self.tr["composure"][i]
        if setpiece == "kickoff":
            delay = rng.uniform(0.2, 0.6)
        elif setpiece:
            delay = rng.uniform(0.4, 1.4)
        elif how == "catch":
            delay = rng.uniform(2.5, 6.0)
        elif self.force_shot:
            delay = rng.uniform(0.0, 0.15)
        else:
            delay = rng.uniform(*p["decision_s"]) * (1.25 - 0.5 * comp) / max(0.6, tempo)
        self.decide_t = t + delay
        self.shielding = False
        # First touch: take the ball into space (or protect it if someone is tight).
        b = self.ball[:2]
        opps = self.ts[other(tid)].on
        d = _dist(self.pos[opps], b)
        fwd = np.array([SIGN[tid], 0.0])
        if setpiece:
            self.carry_tgt = b.copy()
        elif d.min() < 2.5:
            away = b - self.pos[opps[int(np.argmin(d))]]
            self.carry_tgt = b + away / (np.linalg.norm(away) + 1e-6) * 2.0
            self.shielding = True
        else:
            ang = rng.normal(0, 0.5)
            dirv = np.array([fwd[0] * math.cos(ang), math.sin(ang)])
            self.carry_tgt = b + dirv * rng.uniform(1.5, 4.0)
        self.carry_tgt = self._inside(self.carry_tgt)

    def _inside(self, xy: np.ndarray, margin: float = 1.0) -> np.ndarray:
        return np.array([max(-HALF_LENGTH + margin, min(HALF_LENGTH - margin, xy[0])),
                         max(-HALF_WIDTH + margin, min(HALF_WIDTH - margin, xy[1]))])

    def _end_carry(self, t: float) -> None:
        if self.state != OWNED or self.owner < 0:
            return
        t0, start = self.carry_start
        end = self.ball[:2]
        length = float(np.linalg.norm(end - start))
        if length >= 3.0 and not self.setpiece:
            tid = self.tid_of[self.owner]
            prog = float(max(0.0, SIGN[tid] * (end[0] - start[0])))
            self._emit(t0, "carry", tid, self.owner, x=start[0], y=start[1], end_x=end[0], end_y=end[1],
                       qualifiers={"length_m": round(length, 1), "progressive_m": round(prog, 1),
                                   "dribbles": self.dribbles, "duration_s": round(t - t0, 1)})
        self.carry_start = (t, end.copy())

    def _resolve_pass(self, outcome: str, t: float, receiver: int | None = None) -> None:
        pd = self.pending
        if pd is None:
            return
        self.pending = None
        rel = pd["receiver"] if receiver is None or outcome != "complete" else receiver
        self._emit(pd["t"], "pass", pd["team"], pd["passer"], x=pd["x"], y=pd["y"], end_x=pd["end_x"], end_y=pd["end_y"],
                   outcome=outcome, related_player_id=self.ids[rel], qualifiers=pd["q"])

    # ------------------------------------------------------------------ on the ball
    def _owned_step(self, t: float) -> None:
        p, rng = self.p, self.rng
        i = self.owner
        tid = self.tid_of[i]
        opp = other(tid)
        b = self.ball[:2]
        if not self.in_hands and not self.setpiece:
            opps = self.ts[opp].on
            d = _dist(self.pos[opps], b)
            for j, dj in zip(opps, d, strict=True):
                if dj < 3.0 and j not in self.pressed and not self.is_gk[j]:
                    self.pressed.add(j)
                    self._emit(t, "pressure", opp, j, x=b[0], y=b[1])
            press = self._style(opp, t)["press"]
            order = np.argsort(d)
            in_box = SIGN[tid] * b[0] > BOX_X and abs(b[1]) < BOX_Y
            danger = min(1.0, threat(SIGN[tid] * b[0], SIGN[tid] * b[1]) / 0.12)
            v = self.vel[i]
            sp_i = math.hypot(v[0], v[1])
            for o in order[:2]:
                j = opps[o]
                if d[o] > 1.7 or self.beaten_until[j] > t or self.is_gk[j]:
                    continue
                # Running at a defender who is in the way is a take-on: it is settled now.
                rel = self.pos[j] - self.pos[i]
                if d[o] < 1.3 and sp_i > 1.5 and (rel @ v) / (sp_i * (np.linalg.norm(rel) + 1e-6)) > 0.4:
                    self._tackle(t, i, j, take_on=True)
                    return
                rate = p["tackle_rate_per_s"] * (0.45 + self.tr["aggression"][j]) * (0.8 + 0.5 * press)
                # Defenders step in when the carrier gets near goal; they stay on their feet in the box.
                rate *= (1.0 + 2.0 * danger) * (0.6 if in_box else 1.0)
                if rng.random() < rate * self.dt:
                    self._tackle(t, i, j)
                    return
            # Tight marking and a nervous carrier: get rid of it sooner.
            if d.min() < 1.4 and self.decide_t > t + 0.25 and rng.random() < (1.25 - self.tr["composure"][i]) * 3 * self.dt:
                self.decide_t = t
        reached = float(np.linalg.norm(self.carry_tgt - b)) < 0.8
        if t >= self.decide_t or (reached and not self.setpiece and not self.in_hands):
            self._decide(t)

    def _tackle(self, t: float, i: int, j: int, take_on: bool = False) -> None:
        p, rng = self.p, self.rng
        tid = self.tid_of[i]
        opp = other(tid)
        b = self.ball[:2].copy()
        agg, tack = self.tr["aggression"][j], self.tr["tackling"][j]
        p_foul = p["foul_share_of_tackles"] * (0.5 + agg) * (1.5 - tack) * (0.5 if self.yellow[j] else 1.0)
        if SIGN[tid] * b[0] > BOX_X and abs(b[1]) < BOX_Y:
            p_foul *= 0.3
        if rng.random() < p_foul:
            self._end_carry(t)
            self._emit(t, "foul", opp, j, x=b[0], y=b[1], related_player_id=self.ids[i])
            if not self.yellow[j] and rng.random() < p["card_share_of_fouls"] * (0.6 + agg):
                self.yellow[j] = True
                self._emit(t + 1.0, "card", opp, j, outcome="yellow")
            xa = SIGN[tid] * b[0]
            if xa > BOX_X and abs(b[1]) < BOX_Y:
                self._set_dead(t, "penalty", tid, np.array([SIGN[tid] * (HALF_LENGTH - 11.0), 0.0]))
            else:
                self._set_dead(t, "free_kick", tid, self._inside(b, 0.5))
            return
        speed_i = float(np.linalg.norm(self.vel[i]))
        p_win = sigmoid(2.4 * (tack - self.tr["dribbling"][i]) + p["tackle_win_logit"] + (p["take_on_logit"] if take_on else 0.0)
                        + (0.4 if self.shielding is False and speed_i < 1.5 else 0.0)
                        - 0.5 * (self.tr["composure"][i] - 0.5))
        if rng.random() < p_win:
            self._end_carry(t)
            self._emit(t, "tackle", opp, j, x=b[0], y=b[1], outcome="won", related_player_id=self.ids[i])
            if rng.random() < 0.55 + 0.3 * self.tr["first_touch"][j]:
                self._gain(j, t)
            else:  # poked away: a loose ball either side can win
                ang = rng.uniform(0, 2 * math.pi)
                self._loose(t, np.array([math.cos(ang), math.sin(ang)]) * rng.uniform(3, 7), team=opp)
                self.poss = opp
        else:  # beaten: the defender is out of the play for a moment
            self.beaten_until[j] = t + 1.0 + 1.2 * self.tr["flair"][i]
            self.dribbles += 1
            fwd = np.array([SIGN[tid], 0.0])
            self.carry_tgt = self._inside(self.ball[:2] + fwd * 6 + np.array([0.0, rng.normal(0, 3)]))
            self.shielding = False
            self.decide_t = max(self.decide_t, t + 0.6)

    def _decide(self, t: float) -> None:
        p, rng = self.p, self.rng
        i = self.owner
        tid = self.tid_of[i]
        opp = other(tid)
        s = SIGN[tid]
        st, ost = self.ts[tid], self.ts[opp]
        sty = self._style(tid, t)
        b_abs = self.ball[:2].copy()
        b = b_abs * s
        sp = self.setpiece
        tr = self.tr
        vision, flair, passing = tr["vision"][i], tr["flair"][i], tr["passing"][i]

        opps = np.array(ost.on)
        o_att = self.pos[opps] * s
        o_v = self.vmax[opps]
        d_opp = np.linalg.norm(o_att - b, axis=1)
        dmin = float(d_opp.min())
        pr = min(1.0, max(0.0, (5.0 - dmin) / 4.0)) if not self.in_hands else 0.0

        utils: list[float] = []
        acts: list[tuple] = []

        # --- shot
        dist, ang = shot_geometry(b[0], b[1])
        max_d = p["shot_max_dist_m"] + 8 * (tr["long_shots"][i] - 0.5)
        can_shoot = sp in (None, "free_kick", "penalty") and not self.in_hands
        if self.force_shot or (can_shoot and b[0] > 10 and dist < max_d):
            if sp == "penalty":
                xg, blockers, header = 0.76, 0, False
            else:
                xg = sigmoid(p["xg_a"] + p["xg_b"] * dist + p["xg_c"] * ang)
                gx = np.array([HALF_LENGTH, 0.0])
                v = gx - b
                L = float(np.linalg.norm(v))
                u = v / L
                rel = o_att - b
                along = rel @ u
                perp = np.abs(rel[:, 0] * u[1] - rel[:, 1] * u[0])
                outfield = ~self.is_gk[opps]
                blockers = int(np.sum(outfield & (along > 0.5) & (along < L - 1.0) & (perp < 1.0 + 0.09 * along)))
                header = self.header_chance
                xg *= (0.8 if dmin < 2.5 else 1.0) * (0.55 if header else 1.0) * (0.8 ** blockers)
                if sp == "free_kick":
                    xg *= 0.6
            skill = tr["finishing"][i] if dist < 18 else 0.5 * (tr["finishing"][i] + tr["long_shots"][i])
            u_shot = xg * p["shot_bias"] * (0.65 + 0.7 * skill) + 0.004 * flair
            if dist > 16 and sp != "penalty":  # some players just love to shoot from range
                u_shot += p["long_shot_pref"] * tr["long_shots"][i] ** 2 * (1 if blockers == 0 else 0.4)
            if self.force_shot:
                u_shot += 1.0
            utils.append(u_shot)
            acts.append(("shot", xg, dist, ang, blockers, header, dmin < 2.5))

        # --- passes (one candidate per teammate, plus a through ball for anyone running)
        lo_len, hi_len = p["pass_max_len_m"]
        max_len = lo_len + (hi_len - lo_len) * vision
        if self.is_gk[i]:
            max_len = 70.0
        if sp == "throw_in":
            max_len = 22.0
        cands = []
        for slot, j in enumerate(st.on):
            if j == i:
                continue
            rpos = self.pos[j] * s
            rvel = self.vel[j] * s
            if self.is_gk[j] and (b[0] > -20 or sp == "goal_kick"):
                continue
            tgt = rpos + rvel * 0.5
            cands.append((j, tgt, "short"))
            if self.run_until[j] > t and rvel[0] > 2.0:
                lead = rpos + rvel * 1.4 + np.array([3.0, 0.0])
                cands.append((j, lead, "through"))
        if cands:
            T_ = np.array([c[1] for c in cands])
            T_[:, 0] = np.clip(T_[:, 0], -HALF_LENGTH + 1, HALF_LENGTH - 1)
            T_[:, 1] = np.clip(T_[:, 1], -HALF_WIDTH + 1, HALF_WIDTH - 1)
            vec = T_ - b
            L = np.linalg.norm(vec, axis=1)
            in_box = (T_[:, 0] > BOX_X) & (np.abs(T_[:, 1]) < BOX_Y)
            cross = (b[0] > 25) & (np.abs(b[1]) > 14) & in_box
            lofted = (L > 32) | cross | ((sp == "goal_kick") & (L > 25)) | ((sp == "corner") & (L > 12))
            speed = np.where(lofted, np.clip(12 + 0.25 * L, 14, 24), np.clip(8 + 0.5 * L, 10, 24))
            dur = L / speed
            # Lane risk: can an opponent get to the ball's path before it does?
            fr = np.array([0.15, 0.35, 0.55, 0.75, 1.0])
            pts = b + fr[None, :, None] * vec[:, None, :]  # (n, 5, 2)
            ball_t = fr[None, :] * dur[:, None]  # (n, 5)
            dd = np.linalg.norm(pts[:, :, None, :] - o_att[None, None, :, :], axis=3)  # (n, 5, 11)
            reach = np.maximum(0.0, dd - p["intercept_reach_m"]) / o_v[None, None, :] + 0.3
            margin = ball_t[:, :, None] - reach
            pint = 1.0 / (1.0 + np.exp(-np.clip(3.0 * margin, -30, 30)))
            pint[lofted, :-1, :] = 0.0  # in the air until it lands
            lane = 1.0 - np.prod(1.0 - 0.55 * pint.max(axis=1), axis=1)
            space = np.linalg.norm(T_[:, None, :] - o_att[None, :, :], axis=2).min(axis=1)
            ctrl = 0.35 / (1.0 + np.exp((space - 2.0))) + 0.15 * lofted
            p_exec = (p["pass_base"] - p["pass_len_penalty_per_m"] * L - p["pass_pressure_penalty"] * pr
                      - 0.06 * lofted) * (0.82 + 0.3 * passing) * st.strength
            p_exec = np.clip(p_exec, 0.3, 0.99)
            p_succ = p_exec * (1 - lane) * (1 - ctrl)
            for n, (j, tgt, kind) in enumerate(cands):
                if L[n] < (3.0 if sp else 5.0) or L[n] > max_len:
                    continue
                if sp == "kickoff" and T_[n, 0] > 1.0:
                    continue
                gain = T_[n, 0] - b[0]
                val = p["pass_value_discount"] * threat(T_[n, 0], T_[n, 1])
                u_ = (p_succ[n] * val - (1 - p_succ[n]) * turnover_cost(b[0])
                      + 0.0012 * sty["directness"] * max(0.0, gain) * (1 if L[n] > 25 else 0.5)
                      + 0.012 * (1 - sty["directness"]) * p_succ[n]
                      + (0.006 * flair if kind == "through" else 0.0))
                u_ += rng.normal(0, 0.006 * (1.4 - vision))  # imperfect reading of the picture
                utils.append(float(u_))
                k_ = "cross" if cross[n] else ("through" if kind == "through" else
                                               ("switch" if abs(T_[n, 1] - b[1]) > 30 else ("long" if L[n] > 32 else "short")))
                acts.append(("pass", j, T_[n].copy(), bool(lofted[n]), float(speed[n]), k_, float(p_succ[n]), pr))

        # --- clearance: deep in your own third, sometimes you just get it away
        if sp is None and not self.in_hands and not self.is_gk[i] and b[0] < -HALF_LENGTH + 30:
            side = 1.0 if b[1] >= 0 else -1.0
            tgt = np.array([b[0] + rng.uniform(30, 50), side * rng.uniform(12, 33)])
            mates = [j for j in st.on if j != i]
            j = mates[int(np.argmin(_dist(self.pos[mates] * s, tgt)))]
            utils.append(p["clear_value"] * (0.4 + pr) * (1.3 - 0.6 * tr["composure"][i]))
            acts.append(("pass", j, tgt, True, 22.0, "clearance", 0.5, pr))

        # --- carries: forward, diagonals, or shield
        if sp is None and not self.in_hands and not self.force_shot and not self.is_gk[i]:
            drib = tr["dribbling"][i]
            for a in (0.0, 0.6, -0.6, 1.3, -1.3, math.pi):
                dist_c = 2.5 if a == math.pi else 7.0
                tgt = b + np.array([math.cos(a), math.sin(a)]) * dist_c
                tgt = np.clip(tgt, [-HALF_LENGTH + 2, -HALF_WIDTH + 2], [HALF_LENGTH - 2, HALF_WIDTH - 2])
                t_c = dist_c / 5.0
                # How close can each opponent get to the ball along the carry (now, a third, two thirds, the end)?
                path = b + np.array([0.0, 0.33, 0.66, 1.0])[:, None] * (tgt - b)[None, :]
                reachable = (np.hypot(path[:, None, 0] - o_att[None, :, 0], path[:, None, 1] - o_att[None, :, 1])
                             - o_v[None, :] * (np.array([0.0, 0.33, 0.66, 1.0]) * t_c * 0.6)[:, None])
                space = np.sort(reachable.min(axis=0))[:3]
                z = 1.2 * space + 2.5 * (drib - 0.5) + p["carry_keep_logit"] + (1.0 if a == math.pi else 0.0)
                risk = 1.0 / (1.0 + np.exp(np.clip(z, -30, 30)))
                p_keep = float(np.prod(1.0 - risk))
                u_ = (p_keep * p["carry_value_discount"] * threat(tgt[0], tgt[1]) - (1 - p_keep) * turnover_cost(b[0])
                      + p["carry_bias"] * 0.01 + 0.004 * flair * drib - (0.006 if a == math.pi else 0.0))
                u_ += rng.normal(0, 0.006 * (1.4 - vision))
                utils.append(float(u_))
                acts.append(("carry", tgt, a == math.pi))

        if not acts:  # nothing sensible: hold it a moment
            self.decide_t = t + 0.5
            return
        temp = p["decision_temp"] * (1.5 - vision) * (0.7 + 0.6 * flair)
        u = np.array(utils)
        w = np.exp((u - u.max()) / max(1e-4, temp))
        pick = int(rng.choice(len(acts), p=w / w.sum()))
        choice = acts[pick]
        if self.decision_log is not None:
            self.decision_log.append({"t": t, "player": self.ids[i], "ball": (float(b[0]), float(b[1])), "pressure": pr,
                                      "chosen": pick, "options": [(a[0] if a[0] != "pass" else f"pass/{a[5]}", float(x))
                                                                  for a, x in zip(acts, utils, strict=True)]})

        if choice[0] == "shot":
            self._end_carry(t)
            _, xg, dist, ang, blockers, header, pressured = choice
            self._shoot(t, i, xg, dist, ang, blockers, header, pressured)
        elif choice[0] == "pass":
            self._end_carry(t)
            _, j, tgt, lofted, speed, kind, p_succ, pr_ = choice
            self._pass(t, i, j, tgt, lofted, speed, kind, p_succ, pr_)
        else:
            _, tgt, shield = choice
            self.carry_tgt = self._inside(tgt * s, 1.5)
            self.shielding = bool(shield)
            self.decide_t = t + rng.uniform(*p["carry_s"]) * (0.6 if shield else 1.0)

    def _pass(self, t, i, j, tgt, lofted, speed, kind, p_succ, pr) -> None:
        p, rng = self.p, self.rng
        tid = self.tid_of[i]
        s = SIGN[tid]
        b = self.ball[:2].copy()
        L = float(np.linalg.norm(tgt - b * s))
        err = p["pass_error_m"] * (0.4 + 0.05 * L) * (1.3 - self.tr["passing"][i]) * (1 + 0.8 * pr)
        if self.setpiece == "throw_in":
            err *= 0.5
        elif kind == "clearance":
            err *= 2.0
        end_att = tgt + rng.normal(0, err, 2)
        end = end_att * s
        L_act = float(np.linalg.norm(end - b))
        dur = max(0.25, L_act / speed)
        z = min(14.0, 0.18 * L_act) if lofted else 0.0
        if self.setpiece == "throw_in":
            z = 1.5
        self.flight = _Flight("pass", t, t + dur, b, end, z, tid, i, receiver=j, speed=speed, lofted=lofted)
        self.state = FLIGHT
        self.last_touch = tid
        prog = float(max(0.0, end_att[0] - b[0] * s))
        q = {"length_m": round(L_act, 1), "under_pressure": pr > 0.3, "difficulty": round(1 - p_succ, 3),
             "ball_speed_mps": round(speed, 1), "progressive_m": round(prog, 1), "kind": kind,
             "height": "high" if lofted else "ground"}
        if self.setpiece:
            q["set_piece"] = self.setpiece
        self.pending = {"t": t, "team": tid, "passer": i, "receiver": j, "x": b[0], "y": b[1],
                        "end_x": end[0], "end_y": end[1], "q": q}
        self.setpiece = None
        self.owner = -1
        # Give-and-go: the passer sometimes sprints on for the return.
        if not self.is_gk[i] and b[0] * s > -15 and rng.random() < 0.3 * self.tr["work_rate"][i]:
            self.run_until[i] = t + rng.uniform(1.5, 3.0)
            self.run_tgt[i] = self._inside((b * s + np.array([12.0, rng.normal(0, 4)])) * s)

    def _shoot(self, t, i, xg, dist, ang, blockers, header, pressured) -> None:
        p, rng = self.p, self.rng
        tid = self.tid_of[i]
        opp = other(tid)
        s = SIGN[tid]
        b = self.ball[:2].copy()
        xg = round(min(0.95, float(xg)), 3)
        skill = self.tr["finishing"][i] if dist < 18 else 0.5 * (self.tr["finishing"][i] + self.tr["long_shots"][i])
        gk = self._gk(opp)
        p_goal = min(0.97, xg * (0.75 + 0.5 * skill) * (1.2 - 0.4 * self.tr["reflexes"][gk]))
        if header:
            speed_kmh = rng.uniform(35, 65)
        else:
            speed_kmh = min(125.0, rng.uniform(55, 80) + dist * rng.uniform(0.8, 1.6))
        if rng.random() < p_goal:
            outcome = "goal"
        else:
            split = dict(self.p["shot_outcome_split"])
            if blockers == 0:
                split["blocked"] *= 0.25
            else:
                split["blocked"] *= 1.0 + 0.6 * blockers
            outcome = str(rng.choice(list(split), p=np.array(list(split.values())) / sum(split.values())))
        gy = rng.uniform(-3.3, 3.3)
        if outcome == "off_target":
            gy = rng.choice([-1, 1]) * rng.uniform(3.9, 8.0) if rng.random() < 0.6 else rng.uniform(-3.5, 3.5)
        end_att = np.array([HALF_LENGTH + (0.8 if outcome == "goal" else 0.0), gy])
        result = {"goal": "goal", "off_target": "wide"}.get(outcome, "")
        if outcome == "saved":
            end_att = np.array([HALF_LENGTH - 0.8, gy * 0.8])
            r = rng.random()
            catch = 0.35 + 0.3 * self.tr["reflexes"][gk] - (0.15 if speed_kmh > 95 else 0.0)
            result = "catch" if r < catch else ("parry_corner" if rng.random() < p["corner_after_save"] else "parry")
        elif outcome == "blocked":
            frac = rng.uniform(0.15, 0.45)
            end_att = b * s + (np.array([HALF_LENGTH, gy]) - b * s) * frac
            result = "block_corner" if rng.random() < p["corner_after_block"] else "block"
        end = end_att * s
        L = float(np.linalg.norm(end - b))
        dur = max(0.2, L / (speed_kmh / 3.6))
        z = rng.uniform(0.2, 2.2) if outcome != "off_target" else rng.uniform(0.5, 4.0)
        body = "head" if header else ("left_foot" if self.players[i].foot == "L" else "right_foot")
        if not header and rng.random() < 0.2:
            body = "right_foot" if body == "left_foot" else "left_foot"
        situation = {"free_kick": "free_kick", "penalty": "penalty"}.get(self.setpiece or "", "open_play")
        self._emit(t, "shot", tid, i, x=b[0], y=b[1], end_x=end[0], end_y=end[1], outcome=outcome,
                   qualifiers={"xg": xg, "distance_m": round(dist, 1), "angle_rad": round(ang, 3),
                               "shot_speed_kmh": round(speed_kmh, 1), "under_pressure": bool(pressured),
                               "body_part": body, "situation": situation, "blockers": int(blockers)})
        self.flight = _Flight("shot", t, t + dur, b, end, z, tid, i, speed=speed_kmh / 3.6, result=result)
        self.state = FLIGHT
        self.setpiece = None
        self.owner = -1
        self.last_touch = tid

    # ------------------------------------------------------------------ ball in the air / loose
    def _flight_step(self, t: float) -> None:
        f = self.flight
        assert f is not None
        p, rng = self.p, self.rng
        if f.kind == "shot":
            if t >= f.t1:
                self._shot_lands(t, f)
            return
        bx, by, bz = self.ball
        if abs(bx) > HALF_LENGTH + 0.3 or abs(by) > HALF_WIDTH + 0.3:
            self._ball_out(t)
            return
        opp = other(f.team)
        opps = self.ts[opp].on
        if bz < 2.3 and t > f.t0 + 0.15:
            d = np.linalg.norm(self.pos[opps] - self.ball[:2], axis=1)
            for o in np.argsort(d)[:2]:
                j = opps[o]
                reach = p["intercept_reach_m"] * (0.8 + 0.5 * self.tr["positioning"][j])
                if self.is_gk[j]:
                    reach *= 1.6
                if d[o] > reach:
                    continue
                q = p["intercept_prob"] * (0.6 + 0.7 * self.tr["positioning"][j]) * max(0.3, 1.3 - f.speed / 22)
                if rng.random() < q:
                    b = self.ball[:2].copy()
                    self._resolve_pass("intercepted", t)
                    self._emit(t, "interception", opp, j, x=b[0], y=b[1])
                    if rng.random() < 0.55 + 0.35 * self.tr["first_touch"][j]:
                        self._gain(j, t)
                    else:
                        v = self.ball_vel_from_flight(f) * rng.uniform(0.2, 0.5)
                        self._loose(t, v + rng.normal(0, 2, 2), team=opp)
                        self.poss = opp
                    return
        if t < f.t1:
            return
        # Arrival: whoever is closest gets there first; a lofted ball is an aerial duel.
        act = self._active()
        d = _dist(self.pos[act], f.end)
        order = np.argsort(d)
        mates = [act[o] for o in order[:6] if self.tid_of[act[o]] == f.team and d[o] < 2.6]
        foes = [act[o] for o in order[:6] if self.tid_of[act[o]] != f.team and d[o] < 2.6]
        if not mates and not foes:
            self._loose(t, self.ball_vel_from_flight(f) * 0.45, team=None)
            return
        a = mates[0] if mates else None
        dfd = foes[0] if foes else None
        if a is not None and dfd is not None:
            da, dd_ = float(np.linalg.norm(self.pos[a] - f.end)), float(np.linalg.norm(self.pos[dfd] - f.end))
            if f.lofted:
                pa = sigmoid(3.0 * (self.tr["heading"][a] - self.tr["heading"][dfd]) + 0.8 * (dd_ - da) - 0.3)
            else:
                pa = sigmoid(1.5 * (dd_ - da) + 1.0)
            winner = a if rng.random() < pa else dfd
        else:
            winner = a if a is not None else dfd
        assert winner is not None
        if self.tid_of[winner] != f.team:
            b = self.ball[:2].copy()
            self._resolve_pass("intercepted", t)
            self._emit(t, "interception", other(f.team), winner, x=b[0], y=b[1])
            if f.lofted and SIGN[self.tid_of[winner]] * b[0] < -25:  # headed clear
                away = np.array([SIGN[self.tid_of[winner]] * rng.uniform(10, 18), rng.normal(0, 6)])
                self._loose(t, away, team=self.tid_of[winner])
                self.poss = self.tid_of[winner]
            else:
                self._gain(winner, t)
            return
        # A teammate gets there: control, or a heavy touch.
        near_opp = float(np.linalg.norm(self.pos[opps] - f.end, axis=1).min())
        pc = (p["control_base"] + 0.08 * (self.tr["first_touch"][winner] - 0.5) - 0.12 * (near_opp < 2.0)
              - 0.08 * f.lofted - 0.04 * (f.speed > 20))
        xa = SIGN[f.team] * f.end[0]
        box = xa > BOX_X and abs(f.end[1]) < BOX_Y
        if f.lofted and box and rng.random() < 0.55:
            self._gain(winner, t, how="header_chance")
            return
        if rng.random() < pc:
            self._gain(winner, t)
        else:
            self._loose(t, self.ball_vel_from_flight(f) * 0.25 + rng.normal(0, 1.5, 2), team=f.team)

    def ball_vel_from_flight(self, f: _Flight) -> np.ndarray:
        v = f.end - f.start
        n = float(np.linalg.norm(v)) + 1e-6
        return v / n * f.speed

    def _loose(self, t: float, vel: np.ndarray, team: str | None) -> None:
        self.state = LOOSE
        self.owner = -1
        self.flight = None
        self.ball_vel = np.array(vel, dtype=float)
        self.ball[2] = 0.0
        self.loose_lock = t + 0.3
        if team is not None:
            self.last_touch = team

    def _loose_step(self, t: float) -> None:
        bx, by = self.ball[:2]
        if abs(bx) > HALF_LENGTH or abs(by) > HALF_WIDTH:
            self._ball_out(t)
            return
        if t < self.loose_lock:
            return
        act = self._active()
        d = _dist(self.pos[act], self.ball[:2])
        o = int(np.argmin(d))
        if d[o] > 1.1:
            return
        w = act[o]
        close = [act[k] for k in np.argsort(d)[:3] if d[k] < 1.1 and self.tid_of[act[k]] != self.tid_of[w]]
        if close and self.rng.random() < 0.45:
            w = close[0]
        tid = self.tid_of[w]
        if self.pending is None and tid != self.poss:
            b = self.ball[:2]
            self._emit(t, "recovery", tid, w, x=b[0], y=b[1])
        elif self.pending is not None and tid != self.pending["team"]:
            b = self.ball[:2].copy()
            self._resolve_pass("intercepted", t)
            self._emit(t, "recovery", tid, w, x=b[0], y=b[1])
        self._gain(w, t)

    def _ball_out(self, t: float) -> None:
        x, y = float(self.ball[0]), float(self.ball[1])
        last = self.last_touch
        if self.pending is not None:
            self._resolve_pass("out", t)
        if abs(y) > HALF_WIDTH and abs(x) <= HALF_LENGTH:
            kind, team = "throw_in", other(last)
            spot = np.array([max(-HALF_LENGTH + 1, min(HALF_LENGTH - 1, x)), math.copysign(HALF_WIDTH, y)])
        else:
            end_sign = 1.0 if x > 0 else -1.0
            attackers = "h" if end_sign > 0 else "a"
            if last == attackers:
                kind, team = "goal_kick", other(attackers)
                spot = np.array([end_sign * (HALF_LENGTH - 5.5), math.copysign(4.0, y) if abs(y) > 6 else 0.0])
            else:
                kind, team = "corner", attackers
                spot = np.array([end_sign * (HALF_LENGTH - 0.3), math.copysign(HALF_WIDTH - 0.3, y)])
        self._emit(t, "ball_out", last, None, x=x, y=y, qualifiers={"restart": kind})
        self._set_dead(t, kind, team, spot)

    def _shot_lands(self, t: float, f: _Flight) -> None:
        rng = self.rng
        tid = f.team
        opp = other(tid)
        gk = self._gk(opp)
        s = SIGN[tid]
        r = f.result
        if r == "goal":
            self.score[tid] += 1
            self._kickoff(t, opp)
            return
        if r in ("catch", "parry", "parry_corner"):
            self._emit(t, "save", opp, gk, x=f.end[0], y=f.end[1])
        if r == "catch":
            self.pos[gk] = f.end - np.array([s * 0.4, 0.0])
            self._gain(gk, t, how="catch")
        elif r in ("parry_corner", "block_corner"):
            self.last_touch = opp
            spot = np.array([s * (HALF_LENGTH - 0.3), math.copysign(HALF_WIDTH - 0.3, f.end[1] if f.end[1] else 1.0)])
            self._emit(t + 0.5, "ball_out", opp, None, x=spot[0], y=spot[1], qualifiers={"restart": "corner"})
            self._set_dead(t, "corner", tid, spot)
        elif r in ("parry", "block"):
            v = np.array([-s * rng.uniform(4, 11), rng.normal(0, 6)])
            self._loose(t, v, team=opp)
        else:  # wide or over
            self._set_dead(t, "goal_kick", opp, np.array([s * (HALF_LENGTH - 5.5), 0.0]))

    # ------------------------------------------------------------------ movement
    def _targets(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        p, rng = self.p, self.rng
        tgt = self.pos.copy()
        urgent = np.zeros(self.P)  # 0 = normal movement, otherwise the share of top speed to run at
        b = self.ball[:2]
        if self.state == DEAD and self.restart:
            rk: str | None = self.restart.kind
        else:  # set-piece shapes hold for a moment after the ball is played
            rk = self.sp_kind if t < self.sp_until else None
        for tid in TEAMS:
            st = self.ts[tid]
            s = SIGN[tid]
            A = st.A
            opp = other(tid)
            O = self.ts[opp].A
            bx, by = b * s
            poss = self.poss == tid
            sty = self._style(tid, t)
            ax, ay = st.anchors[:, 0], st.anchors[:, 1]
            if rk == "kickoff":
                X = np.minimum(ax * 0.9, -1.0)
                Y = ay * 0.9
            elif poss:
                X = 0.85 * ax + 0.55 * bx + 4 + 6 * sty["line"] + st.poss_dx
                wy = np.where((st.poss_wy < 1.0) & (bx < 5), 1.0, st.poss_wy)
                Y = ay * (0.95 + 0.35 * sty["width"]) * wy + 0.22 * by
                o_x = np.sort(self.pos[O, 0] * s)[::-1]
                last_line = max(float(o_x[1]), bx)
                X = np.minimum(np.clip(X, -47, 46), last_line - 0.5)
            else:
                X = 0.6 * ax + 0.62 * bx + (-10 + 18 * sty["line"]) + 6 + st.def_dx
                Y = ay * (0.6 + 0.15 * sty["width"]) + 0.42 * by
                X = np.clip(X, -47, 36)
                # The back line stays goal-side of the ball, and the box fills up when the ball gets close.
                X = np.where(st.back_line, np.minimum(X, max(-48.5, bx - 3.0)), X)
                if bx < -HALF_LENGTH + 30:
                    Y = np.where(st.back_line, Y * 0.7, Y)
            gk_x = -49 + min(6.0 if self.role[A[0]] == "sweeper keeper" else 3.5, max(0.0, (bx + 15) * 0.08))
            X[0], Y[0] = gk_x, max(-3.0, min(3.0, by * 0.15))
            Y = np.clip(Y, -HALF_WIDTH + 1.5, HALF_WIDTH - 1.5)
            T = np.stack([X, Y], axis=1)
            self.drift[A] += -self.drift[A] * self._drift_decay + rng.normal(0, 1, (11, 2)) * self._drift_sd[A, None]
            T[1:] += self.drift[A[1:]]
            if rk == "corner":
                T = self._corner_shape(tid, T, poss)
            elif rk == "penalty":  # everyone outside the box; the keeper on the line
                if poss:
                    T[1:, 0] = np.minimum(T[1:, 0], BOX_X - 1.0)
                else:
                    T[1:, 0] = np.maximum(T[1:, 0], -BOX_X + 1.0)
                    T[0] = (-HALF_LENGTH + 0.3, 0.0)
            tgt[A] = T * s

            if poss and self.state in (OWNED, FLIGHT):
                # Runs in behind and finding space
                if self.state == OWNED and bx > -25 and self.owner >= 0:
                    rate = p["run_prob_per_s"] * st.run_rate * (0.5 + self.tr["work_rate"][A]) * self.dt
                    starts = (rng.random(11) < rate) & (self.run_until[A] <= t)
                    o_x = np.sort(self.pos[O, 0] * s)[::-1]
                    line = float(o_x[1])
                    for slot in np.nonzero(starts)[0]:
                        j = A[slot]
                        if j == self.owner:
                            continue
                        px, py = self.pos[j] * s
                        role = self.role[j]
                        ty = py * (0.6 if role in ("inverted winger", "poacher") else 0.95) + rng.normal(0, 3)
                        self.run_tgt[j] = self._inside(np.array([min(line + rng.uniform(5, 13), 47.0), ty]) * s, 2.0)
                        self.run_until[j] = t + rng.uniform(1.8, 3.5)
                running = self.run_until[A] > t
                tgt[A[running]] = self.run_tgt[A[running]]
                urgent[A[running]] = 1.0  # runs in behind are flat out
                if self.state == OWNED and self.owner >= 0 and not self.setpiece:
                    for j, spot in self._support(t, tid, A, O, running).items():
                        tgt[j] = spot
                        urgent[j] = 0.7 if float(np.linalg.norm(self.pos[j] - spot)) > 6.0 else 0.0
                        running[np.nonzero(A == j)[0]] = True  # don't push supporters off their spot
                # Find space: drift away from the nearest marker
                Pm = self.pos[A[1:]]
                dd = np.linalg.norm(Pm[:, None, :] - self.pos[O][None, :, :], axis=2)
                no = np.argmin(dd, axis=1)
                dmin = dd[np.arange(10), no]
                away = Pm - self.pos[O][no]
                push = (np.maximum(0.0, 6.0 - dmin) * 0.8 / (dmin + 1e-6))[:, None] * away
                near_ball = np.linalg.norm(Pm - b, axis=1) < 35
                tgt[A[1:]] += np.where((near_ball & ~running[1:])[:, None], push, 0.0)
            elif not poss and self.state in (OWNED, FLIGHT, LOOSE) and rk is None:
                self._defend(t, tid, A, O, tgt, urgent, sty)

        if self.state == OWNED and self.owner >= 0:
            tgt[self.owner] = self.carry_tgt
        elif self.state == FLIGHT and self.flight is not None and self.flight.kind == "pass":
            f = self.flight
            if f.receiver >= 0:
                tgt[f.receiver] = f.end
                urgent[f.receiver] = 0.9
        elif self.state == LOOSE:
            act = self._active()
            d = _dist(self.pos[act], b)
            lead = b + self.ball_vel * 0.5
            for tid in TEAMS:
                mask = np.array([self.tid_of[i] == tid and not self.is_gk[i] for i in act])
                idx = act[mask][np.argsort(d[mask])[:2]]
                tgt[idx] = lead
                urgent[idx] = 0.9
        elif self.state == DEAD and self.restart is not None:
            r = self.restart
            tgt[r.taker] = r.spot - np.array([SIGN[r.team] * 0.5, 0.0])
            urgent[r.taker] = 0.6 if float(np.linalg.norm(self.pos[r.taker] - r.spot)) > 8 else 0.0
            if r.kind in ("free_kick", "corner", "kickoff"):  # opponents give 9.15 m
                O = self.ts[other(r.team)].A
                v = tgt[O] - r.spot
                dist = np.linalg.norm(v, axis=1) + 1e-6
                tgt[O] = np.where((dist < 9.5)[:, None], r.spot + v / dist[:, None] * 9.5, tgt[O])
        return tgt, urgent

    def _support(self, t, tid, A, O, running) -> dict[int, np.ndarray]:
        """Two teammates show for the ball: spots 9-14 m away with the most space, refreshed every second."""
        if self.owner == self.support_owner and t < self.support_t + 1.0:
            return self.support
        self.support_owner, self.support_t = self.owner, t
        s = SIGN[tid]
        b = self.ball[:2] * s
        o_att = self.pos[O] * s
        cand = []
        for ang in (0.6, -0.6, 1.4, -1.4, 2.4, -2.4, 0.0):
            r = self.rng.uniform(9, 14)
            pt = b + np.array([math.cos(ang), math.sin(ang)]) * r
            if abs(pt[0]) > HALF_LENGTH - 2 or abs(pt[1]) > HALF_WIDTH - 2 or pt[0] < -46:
                continue
            space = float(_dist(o_att, pt).min())
            cand.append((space + (1.5 if ang in (0.6, -0.6, 0.0) else 0.0), pt))
        cand.sort(key=lambda c: -c[0])
        free = [j for k, j in enumerate(A[1:]) if j != self.owner and not running[k + 1]]
        out: dict[int, np.ndarray] = {}
        for _, pt in cand[:2]:
            if not free:
                break
            d = _dist(self.pos[free] * s, pt)
            k = int(np.argmin(d))
            if d[k] > 25:
                continue
            out[free.pop(k)] = pt * s
        self.support = out
        return out

    def _defend(self, t, tid, A, O, tgt, urgent, sty) -> None:
        """Out of possession: close down the ball, cover the nearest option, mark the rest."""
        p = self.p
        s = SIGN[tid]
        b = self.ball[:2]
        own_goal = np.array([-s * HALF_LENGTH, 0.0])
        out = A[1:]
        d_ball = _dist(self.pos[out], b)
        beaten = self.beaten_until[out] > t
        d_sel = np.where(beaten, 1e9, d_ball)
        order = np.argsort(d_sel)
        press = max(0.0, min(1.3, sty["press"]))
        bx_own = float(b[0] * s)  # ball in this team's attacking frame
        engaged: set[int] = set()
        if self.state == OWNED and not self.in_hands:
            first = out[order[0]]
            radius = p["engage_m"] * (0.8 + 0.9 * press) * (1.3 if self.role[first] == "pressing forward" else 1.0)
            if d_sel[order[0]] < radius or bx_own < 0:
                lo, hi = p["jockey_m"]
                jockey = hi - (hi - lo) * min(1.0, 0.5 * press + 0.5 * self.tr["aggression"][first])
                near_goal = bx_own < -HALF_LENGTH + 30
                if near_goal:  # tight to the ball near your own goal
                    jockey = lo
                # The longer they have it, the tighter you get.
                jockey = lo + (jockey - lo) * math.exp(-(t - self.gained_t) / 2.5)
                g = own_goal - b
                tgt[first] = b + g / (np.linalg.norm(g) + 1e-6) * jockey
                urgent[first] = min(1.0, 0.75 + 0.2 * press)
                engaged.add(first)
                second = out[order[1]]
                if (press > 0.65 and bx_own > -10 or near_goal) and d_sel[order[1]] < radius:
                    tgt[second] = b + g / (np.linalg.norm(g) + 1e-6) * (jockey + 1.5) + np.array([0.0, 2.5])
                    urgent[second] = 0.75
                    engaged.add(second)
                else:  # cover the most dangerous short option
                    att = np.array([i for i in self.ts[other(tid)].on if i != self.owner])
                    da = np.linalg.norm(self.pos[att] - b, axis=1)
                    q = self.pos[att[int(np.argmin(da))]]
                    tgt[second] = b + 0.55 * (q - b)
                    engaged.add(second)
        elif self.state == FLIGHT and self.flight is not None and self.flight.kind == "pass":
            land = self.flight.end
            dl = np.linalg.norm(self.pos[out] - land, axis=1)
            j = out[int(np.argmin(dl))]
            tgt[j] = land + (own_goal - land) / (np.linalg.norm(own_goal - land) + 1e-6) * 1.0
            urgent[j] = 0.85
            engaged.add(j)
        # Marking: pull towards the goal side of the nearest attacker
        rest = [i for i in out if i not in engaged]
        if not rest:
            return
        R = np.array(rest)
        att = self.ts[other(tid)].A[1:]
        S = tgt[R]
        dd = np.linalg.norm(S[:, None, :] - self.pos[att][None, :, :], axis=2)
        nn = np.argmin(dd, axis=1)
        q = self.pos[att][nn]
        g = own_goal - q
        gs = q + g / (np.linalg.norm(g, axis=1, keepdims=True) + 1e-6) * 1.5
        w = (0.3 + 0.45 * self.tr["marking"][R]) * (dd[np.arange(len(R)), nn] < 12)
        tgt[R] = S + w[:, None] * (gs - S)

    def _corner_shape(self, tid: str, T: np.ndarray, poss: bool) -> np.ndarray:
        st = self.ts[tid]
        A = st.on
        if poss:
            spots = [(HALF_LENGTH - 6, -2.5), (HALF_LENGTH - 7, 3.0), (HALF_LENGTH - 10, 0.0), (HALF_LENGTH - 4, 4.5),
                     (HALF_LENGTH - 12, -6.0), (HALF_LENGTH - 19, 6.0), (HALF_LENGTH - 20, -8.0)]
            order = sorted(range(1, 11), key=lambda s: -self.tr["heading"][A[s]])
            for k, slot in enumerate(order[: len(spots)]):
                T[slot] = spots[k]
            for slot in order[len(spots):]:
                T[slot] = (-5.0 if self.players[A[slot]].position in ("CB", "RB", "LB") else 15.0, T[slot][1] * 0.5)
        else:
            spots = [(-HALF_LENGTH + 1.0, -3.0), (-HALF_LENGTH + 1.0, 3.0), (-HALF_LENGTH + 5.0, -2.0),
                     (-HALF_LENGTH + 5.5, 2.5), (-HALF_LENGTH + 7.0, 0.0), (-HALF_LENGTH + 9.0, -5.0),
                     (-HALF_LENGTH + 9.0, 5.0), (-HALF_LENGTH + 13.0, 0.0), (-HALF_LENGTH + 17.0, 7.0)]
            order = sorted(range(1, 11), key=lambda s: -self.tr["marking"][A[s]] - self.tr["heading"][A[s]])
            for k, slot in enumerate(order[: len(spots)]):
                T[slot] = spots[k]
            for slot in order[len(spots):]:
                T[slot] = (-8.0, 0.0)
            T[0] = (-HALF_LENGTH + 0.8, 0.0)
        return T

    def _move(self, t: float) -> None:
        p = self.p
        dt = self.dt
        self._tick += 1
        A = self._act
        if self._tick % 2 == 1 or self._tgt is None:  # targets refresh at 5 Hz; motion runs at 10 Hz
            self._tgt, self._urg = self._targets(t)
            minutes = np.minimum((t - self.entered[A]) / 60 * 45 / p["period_min"], 100.0)
            self._vm = self.vmax[A] * (1 - 0.09 * (1 - self.tr["stamina"][A]) * minutes / 90)
            self._jog = np.minimum(p["jog_max_mps"] * (0.85 + 0.3 * self.tr["work_rate"][A]), self._vm)
            self._amax_dt = self.amax[A] * dt
            pos = self.pos[A]
            dx = pos[:, 0][:, None] - pos[:, 0][None, :]
            dy = pos[:, 1][:, None] - pos[:, 1][None, :]
            dist = np.maximum(np.hypot(dx, dy), 0.05) + self._eye1e3
            w = np.where((dist < 2.0) & self._same, (2.0 - dist) / dist, 0.0) * 1.5
            self._rep = np.stack([(w * dx).sum(axis=1), (w * dy).sum(axis=1)], axis=1)  # teammates keep apart
        tgt, urgent = self._tgt, self._urg
        if self.state == OWNED and self.owner >= 0:
            tgt[self.owner] = self.carry_tgt
        pos, vel = self.pos[A], self.vel[A]
        gap = tgt[A] - pos
        d = np.hypot(gap[:, 0], gap[:, 1]) + 1e-9
        urg = urgent[A]
        vm = self._vm
        hurry = urg > 0
        cap = np.where(hurry, np.maximum(vm * urg, self._jog), self._jog)
        steer = p["steer_gain"]
        scale = np.minimum(steer, cap / d)
        scale = np.where(hurry & (d > p["urgent_min_gap_m"]), cap / d, scale)
        desired = gap * scale[:, None]
        if self.state == OWNED and self.owner >= 0:
            k = self._slot_index[self.owner]
            drib = 1.5 if self.shielding else self.vmax[self.owner] * (0.5 + 0.3 * self.tr["dribbling"][self.owner])
            n = math.hypot(desired[k, 0], desired[k, 1])
            if n > drib:
                desired[k] *= drib / n
        dv = desired + self._rep - vel
        a = np.hypot(dv[:, 0], dv[:, 1]) + 1e-9
        dv *= np.minimum(1.0, self._amax_dt / a)[:, None]
        vel = vel + dv + self.rng.normal(0, 0.012, vel.shape)
        pos = pos + vel * dt
        np.clip(pos[:, 0], -HALF_LENGTH - 2, HALF_LENGTH + 2, out=pos[:, 0])
        np.clip(pos[:, 1], -HALF_WIDTH - 2, HALF_WIDTH + 2, out=pos[:, 1])
        self.pos[A] = pos
        self.vel[A] = vel

    def _ball_step(self, t: float) -> None:
        dt = self.dt
        if self.state == OWNED and self.owner >= 0:
            i = self.owner
            v = self.vel[i]
            n = float(np.linalg.norm(v))
            if n > 0.4:
                self._heading = v / n
            elif not hasattr(self, "_heading"):
                self._heading = np.array([SIGN[self.tid_of[i]], 0.0])
            off = 0.35 + min(0.6, 0.08 * n)
            self.ball[:2] = self.pos[i] + self._heading * off
            self.ball[2] = 0.0
            if self.in_hands:
                self.ball[:2] = self.pos[i]
                self.ball[2] = 1.0
        elif self.state == FLIGHT and self.flight is not None:
            f = self.flight
            fr = min(1.0, max(0.0, (t - f.t0) / max(1e-6, f.t1 - f.t0)))
            self.ball[:2] = f.start + (f.end - f.start) * fr
            self.ball[2] = 4 * f.z * fr * (1 - fr) if f.kind == "pass" else f.z * fr
        elif self.state == LOOSE:
            self.ball[:2] += self.ball_vel * dt
            self.ball_vel *= max(0.0, 1 - 0.9 * dt)
            self.ball[2] = max(0.0, self.ball[2] - 2 * dt)
        elif self.state == DEAD and self.restart is not None:
            g = self.restart.spot - self.ball[:2]
            n = float(np.linalg.norm(g))
            if n > 0.05:
                self.ball[:2] += g / n * min(n, 10.0 * dt)
            self.ball[2] = 0.0

    # ------------------------------------------------------------------ public
    def simulate_events(self) -> None:
        self._simulate()

    def simulate_tracking(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
        self._simulate()
        assert self._result is not None
        return self._result

    def run(self) -> SimResult:
        ts, ball, players, ids = self.simulate_tracking()
        meta = MatchMetadata(
            match_id=self.match_id,
            home=self.teams["h"],
            away=self.teams["a"],
            seed=self.seed,
            params_version=params_version(self.p),
            fps=self.fps,
            period_min=float(self.p["period_min"]),
        )
        return SimResult(meta, self.events, ts, ball, players, ids, self.period_bounds)


def simulate(seed: int, match_id: str | None = None, params: dict | None = None, fps: int = 10) -> SimResult:
    return MatchEngine(seed, match_id=match_id, params=params, fps=fps).run()
