"""Incremental, deterministic match metrics.

``MatchInterpreter.handle`` is a pure function of the stream so far: replaying
the same messages gives the same state, cues and moments. All I/O lives in the
service wrapper (``matchlens.services.interpreter``).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from matchlens.contracts import (
    EvidenceItem,
    MatchEvent,
    MatchMetadata,
    Moment,
    OverlayCue,
    StreamMessage,
    TrackingBatch,
)

FINAL_THIRD_X = 17.5  # team-attacking frame
BOX_X, BOX_Y = 36.0, 20.16
PPDA_ZONE_X = -10.5  # defender-frame x above which defensive actions count (opponent's own 60%)
SPRINT_MPS = 7.0
TOP_SPEED_TAG_MPS = 8.3  # 30 km/h, the usual broadcast 'top speed' tag
MOMENTUM_HALF_LIFE_S = 180.0
WINDOW_S = 600.0


def att_x(team: str, x: float) -> float:
    return x if team == "h" else -x


def att_y(team: str, y: float) -> float:
    return y if team == "h" else -y


def r2(v: float) -> float:
    return round(float(v), 2)


@dataclass
class TeamStats:
    goals: int = 0
    shots: int = 0
    shots_on_target: int = 0
    xg: float = 0.0
    passes: int = 0
    passes_complete: int = 0
    final_third_entries: int = 0
    box_entries: int = 0
    possession_s: float = 0.0
    def_actions_high: int = 0
    opp_passes_own_60: int = 0  # opponent passes in *their* defensive 60%, used for our PPDA
    turnovers: int = 0
    pressures: int = 0
    threat: float = 0.0  # EWMA, see momentum
    cards: int = 0

    def summary(self) -> dict[str, Any]:
        acc = self.passes_complete / self.passes if self.passes else 0.0
        ppda = self.opp_passes_own_60 / self.def_actions_high if self.def_actions_high else None
        return {
            "goals": self.goals,
            "shots": self.shots,
            "shotsOnTarget": self.shots_on_target,
            "xg": r2(self.xg),
            "passes": self.passes,
            "passAccuracy": r2(acc),
            "finalThirdEntries": self.final_third_entries,
            "boxEntries": self.box_entries,
            "possessionS": r2(self.possession_s),
            "ppda": r2(ppda) if ppda is not None else None,
            "pressures": self.pressures,
            "turnovers": self.turnovers,
            "cards": self.cards,
        }


@dataclass
class PlayerPhys:
    distance_m: float = 0.0
    top_speed: float = 0.0
    sprints: int = 0
    speed: float = 0.0
    sprinting: bool = False
    last: tuple[float, float, float] | None = None  # t, x, y
    tagged_top: float = 0.0


@dataclass
class WindowEvent:
    t: float
    team: str
    kind: str  # pass_own60, def_high, xg, turnover, threat
    value: float = 1.0


@dataclass
class Output:
    cues: list[OverlayCue] = field(default_factory=list)
    moments: list[Moment] = field(default_factory=list)
    state: dict[str, Any] | None = None
    frames: dict[str, Any] | None = None


class MatchInterpreter:
    """Holds the live state of one match."""

    def __init__(self, match_id: str, state_every_s: float = 5.0, momentum_check_s: float = 60.0):
        self.match_id = match_id
        self.meta: MatchMetadata | None = None
        self.names: dict[str, str] = {}
        self.team_names: dict[str, str] = {}
        self.teams = {"h": TeamStats(), "a": TeamStats()}
        self.players: dict[str, PlayerPhys] = {}
        self.window: deque[WindowEvent] = deque()
        self.seen: set[str] = set()
        self.clock = 0.0
        self.display_clock = "00:00"
        self.period = 1
        self.possession: str | None = None
        self.poss_since = 0.0
        self.chain: list[MatchEvent] = []  # current possession chain
        self.momentum_hist: list[tuple[float, float]] = []
        self.last_state_t = -1e9
        self.last_momentum_check = 0.0
        self.last_moment_t: dict[str, float] = {}
        self.ended = False
        self.last_tracking_t0 = -1.0
        self.state_every_s = state_every_s
        self.momentum_check_s = momentum_check_s

    # ------------------------------------------------------------- public API
    def handle(self, msg: StreamMessage) -> Output:
        out = Output()
        if msg.kind == "metadata":
            self._on_metadata(MatchMetadata.model_validate(msg.payload))
            out.state = self.state_doc()
        elif msg.kind == "event":
            ev = MatchEvent.model_validate(msg.payload)
            if ev.event_id in self.seen:
                return out  # idempotent: duplicates are ignored
            self.seen.add(ev.event_id)
            self._on_event(ev, out, msg.trace_id)
        elif msg.kind == "tracking":
            batch = TrackingBatch.model_validate(msg.payload)
            if batch.t0_s <= self.last_tracking_t0:
                return out  # duplicate or replayed batch
            self.last_tracking_t0 = batch.t0_s
            self._on_tracking(batch, out, msg.trace_id)
        elif msg.kind == "end":
            self.ended = True
            out.state = self.state_doc()
        if out.state is None and self.clock - self.last_state_t >= self.state_every_s:
            out.state = self.state_doc()
        if out.state is not None:
            self.last_state_t = self.clock
        return out

    def momentum(self) -> float:
        h, a = self.teams["h"].threat, self.teams["a"].threat
        return r2((h - a) / (h + a + 0.15))

    def window_stats(self, team: str, span_s: float = WINDOW_S) -> dict[str, float]:
        t0 = self.clock - span_s
        opp = "a" if team == "h" else "h"
        own = [w for w in self.window if w.t >= t0]
        def_high = sum(1 for w in own if w.team == team and w.kind == "def_high")
        opp_passes = sum(1 for w in own if w.team == opp and w.kind == "pass_own60")
        xg = sum(w.value for w in own if w.team == team and w.kind == "xg")
        turnovers = sum(1 for w in own if w.kind == "turnover")
        threat = sum(w.value for w in own if w.team == team and w.kind == "threat")
        return {
            "ppda": r2(opp_passes / def_high) if def_high else 99.0,
            "def_actions_high": def_high,
            "xg": r2(xg),
            "turnovers_per_min": r2(turnovers / (span_s / 60)),
            "threat": r2(threat),
        }

    def control_chaos(self) -> float:
        """0 = controlled, 1 = chaotic, from the turnover rate over the last 5 minutes."""
        tpm = self.window_stats("h", 300)["turnovers_per_min"]
        return r2(max(0.0, min(1.0, (tpm - 1.0) / 2.5)))

    def state_doc(self) -> dict[str, Any]:
        total_poss = self.teams["h"].possession_s + self.teams["a"].possession_s
        poss_h = self.teams["h"].possession_s / total_poss if total_poss else 0.5
        top = sorted(self.players.items(), key=lambda kv: kv[1].top_speed, reverse=True)[:5]
        return {
            "id": self.match_id,
            "matchId": self.match_id,
            "type": "matchState",
            "clockS": r2(self.clock),
            "displayClock": self.display_clock,
            "period": self.period,
            "ended": self.ended,
            "home": {"id": "h", "name": self.team_names.get("h", "Home"), **self.teams["h"].summary()},
            "away": {"id": "a", "name": self.team_names.get("a", "Away"), **self.teams["a"].summary()},
            "possessionHome": r2(poss_h),
            "momentum": self.momentum(),
            "controlChaos": self.control_chaos(),
            "window10": {"h": self.window_stats("h"), "a": self.window_stats("a")},
            "topSpeeds": [
                {"playerId": pid, "name": self.names.get(pid, pid), "mps": r2(pp.top_speed), "kmh": r2(pp.top_speed * 3.6)}
                for pid, pp in top
            ],
            "meta": self.meta.model_dump(mode="json") if self.meta else None,
        }

    # ------------------------------------------------------------- internals
    def _on_metadata(self, meta: MatchMetadata) -> None:
        self.meta = meta
        for team in (meta.home, meta.away):
            self.team_names[team.id] = team.name
            for p in team.players:
                self.names[p.id] = p.name
        self.names.update({"h": meta.home.name, "a": meta.away.name})

    def _decay_threat(self, t: float) -> None:
        dt = max(0.0, t - self.clock)
        if dt:
            k = 0.5 ** (dt / MOMENTUM_HALF_LIFE_S)
            for ts in self.teams.values():
                ts.threat *= k

    def _add_threat(self, team: str, t: float, v: float) -> None:
        self.teams[team].threat += v
        self.window.append(WindowEvent(t, team, "threat", v))

    def _set_possession(self, team: str | None, t: float) -> None:
        if self.possession is not None:
            self.teams[self.possession].possession_s += max(0.0, t - self.poss_since)
        if team != self.possession:
            self.chain = []
        self.possession = team
        self.poss_since = t

    def _cue(self, ev: MatchEvent, template: str, title: str, text: str, priority: int, data: dict,
             trace_id: str | None, metrics: list[EvidenceItem] | None = None, ttl: float = 7.0) -> OverlayCue:
        return OverlayCue(
            id=f"{ev.event_id}-{template}",
            matchId=self.match_id,
            display_at_s=ev.clock_s,
            ttl_s=ttl,
            priority=priority,
            template=template,
            persona="all",
            lang="all",
            title=title,
            text=text,
            data=data,
            evidence={"event_ids": [ev.event_id], "metrics": [m.model_dump() for m in (metrics or [])]},
            source="template",
            trace_id=trace_id,
        )

    def _on_event(self, ev: MatchEvent, out: Output, trace_id: str | None) -> None:
        self._decay_threat(ev.clock_s)
        self.clock = max(self.clock, ev.clock_s)
        self.display_clock = ev.display_clock or self.display_clock
        self.period = ev.period
        while self.window and self.window[0].t < self.clock - WINDOW_S:
            self.window.popleft()
        team = ev.team_id
        opp = None if team is None else ("a" if team == "h" else "h")

        if ev.type == "period_start":
            self._set_possession(None, ev.clock_s)
        elif ev.type == "period_end":
            self._set_possession(None, ev.clock_s)
            kind = "half_time" if ev.period == 1 else "full_time"
            out.moments.append(self._moment(kind, ev, trace_id))
            out.state = self.state_doc()
        elif ev.type in ("pass", "carry", "shot") and team:
            if self.possession != team:
                self._set_possession(team, ev.clock_s)
            self.chain.append(ev)

        if ev.type == "pass" and team:
            ts = self.teams[team]
            ts.passes += 1
            if ev.x is not None and att_x(team, ev.x) < 10.5:
                self.window.append(WindowEvent(ev.clock_s, team, "pass_own60"))
                self.teams[opp].opp_passes_own_60 += 1  # type: ignore[index]
            if ev.outcome == "complete":
                ts.passes_complete += 1
                ex = att_x(team, ev.end_x or 0.0)
                sx = att_x(team, ev.x or 0.0)
                if ex > FINAL_THIRD_X >= sx:
                    ts.final_third_entries += 1
                    self._add_threat(team, ev.clock_s, 0.03)
                if ex > BOX_X and abs(ev.end_y or 0.0) < BOX_Y and not (sx > BOX_X):
                    ts.box_entries += 1
                    self._add_threat(team, ev.clock_s, 0.07)
            else:
                ts.turnovers += 1
                self.window.append(WindowEvent(ev.clock_s, team, "turnover"))
        elif ev.type == "carry" and team:
            prog = float(ev.qualifiers.get("progressive_m", 0.0))
            if prog > 5:
                self._add_threat(team, ev.clock_s, 0.004 * prog)
        elif ev.type in ("tackle", "interception", "foul") and team:
            if ev.x is not None and att_x(team, ev.x) > PPDA_ZONE_X:
                self.teams[team].def_actions_high += 1
                self.window.append(WindowEvent(ev.clock_s, team, "def_high"))
            if ev.type == "tackle":
                self.window.append(WindowEvent(ev.clock_s, team, "turnover"))
            if ev.type != "foul":
                self._set_possession(team, ev.clock_s)
                self.chain = [ev]  # the possession starts with the ball-winning action
        elif ev.type == "recovery" and team:
            self._set_possession(team, ev.clock_s)
            self.chain = [ev]
        elif ev.type == "pressure" and team:
            self.teams[team].pressures += 1
        elif ev.type == "shot" and team:
            self._on_shot(ev, out, trace_id)
        elif ev.type == "card" and team:
            self.teams[team].cards += 1
            name = self.names.get(ev.player_id or "", "?")
            out.cues.append(self._cue(ev, "card", f"Yellow card · {self.team_names.get(team, team)}",
                                      f"{name} · {ev.display_clock}", 2, {"player": name, "team": team}, trace_id))
        elif ev.type == "substitution" and team:
            off = self.names.get(ev.player_id or "", "?")
            on = self.names.get(ev.related_player_id or "", "?")
            out.cues.append(self._cue(ev, "substitution", f"Substitution · {self.team_names.get(team, team)}",
                                      f"On: {on}  ·  Off: {off}", 3, {"on": on, "off": off, "team": team}, trace_id))

        if self.clock - self.last_momentum_check >= self.momentum_check_s:
            self.last_momentum_check = self.clock
            self._check_momentum(ev, out, trace_id)
            self._check_pressing(ev, out, trace_id)

    def _on_shot(self, ev: MatchEvent, out: Output, trace_id: str | None) -> None:
        team = ev.team_id or "h"
        ts = self.teams[team]
        xg = float(ev.qualifiers.get("xg", 0.0))
        speed = float(ev.qualifiers.get("shot_speed_kmh", 0.0))
        ts.shots += 1
        ts.xg += xg
        self.window.append(WindowEvent(ev.clock_s, team, "xg", xg))
        self._add_threat(team, ev.clock_s, xg)
        if ev.outcome in ("goal", "saved"):
            ts.shots_on_target += 1
        name = self.names.get(ev.player_id or "", "?")
        metrics = [
            EvidenceItem(key="xg", label="Expected goals", value=r2(xg)),
            EvidenceItem(key="shot_speed_kmh", label="Shot speed", value=r2(speed), unit="km/h"),
            EvidenceItem(key="distance_m", label="Distance", value=r2(ev.qualifiers.get("distance_m", 0.0)), unit="m"),
        ]
        if ev.outcome == "goal":
            ts.goals += 1
            score = f"{self.teams['h'].goals}–{self.teams['a'].goals}"
            out.cues.append(self._cue(
                ev, "goal", f"GOAL · {self.team_names.get(team, team)}",
                f"{name} {ev.display_clock}  ·  {score}  ·  xG {xg:.2f}  ·  {speed:.0f} km/h", 1,
                {"player": name, "team": team, "score": score, "xg": r2(xg), "speedKmh": speed}, trace_id, metrics, ttl=10))
            out.moments.append(self._moment("goal", ev, trace_id))
            out.state = self.state_doc()
        else:
            if xg >= 0.08:
                outcome = {"saved": "saved", "off_target": "off target", "blocked": "blocked"}.get(ev.outcome or "", ev.outcome)
                out.cues.append(self._cue(
                    ev, "shot", f"Shot · {self.team_names.get(team, team)}",
                    f"{name} · {outcome} · xG {xg:.2f} · {speed:.0f} km/h", 3,
                    {"player": name, "team": team, "xg": r2(xg), "speedKmh": speed, "outcome": ev.outcome},
                    trace_id, metrics))
            if xg >= 0.25 and self._cooldown("big_chance", 120):
                out.moments.append(self._moment("big_chance", ev, trace_id))

    def _cooldown(self, key: str, seconds: float) -> bool:
        last = self.last_moment_t.get(key, -1e9)
        if self.clock - last >= seconds:
            self.last_moment_t[key] = self.clock
            return True
        return False

    def _check_momentum(self, ev: MatchEvent, out: Output, trace_id: str | None) -> None:
        m = self.momentum()
        prev = self.momentum_hist[-1][1] if self.momentum_hist else 0.0
        self.momentum_hist.append((self.clock, m))
        if self.clock < 600:
            return
        # A swing: the leader changes and the new leader is clearly on top.
        before = [v for t, v in self.momentum_hist if self.clock - 300 <= t < self.clock - 60]
        ref = sum(before) / len(before) if before else prev
        if abs(m) >= 0.18 and (ref * m < 0) and abs(m - ref) >= 0.25 and self._cooldown("momentum_swing", 420):
            leader = "h" if m > 0 else "a"
            out.moments.append(self._moment("momentum_swing", ev, trace_id, team=leader, ref=ref))

    def _check_pressing(self, ev: MatchEvent, out: Output, trace_id: str | None) -> None:
        if self.clock < 900:
            return
        for team in ("h", "a"):
            w = self.window_stats(team, 480)
            s = self.teams[team]
            match_ppda = s.opp_passes_own_60 / s.def_actions_high if s.def_actions_high else 99.0
            if w["ppda"] <= 6.0 and match_ppda >= 8.0 and w["def_actions_high"] >= 6 and self._cooldown(f"press_{team}", 600):
                out.moments.append(self._moment("pressing_surge", ev, trace_id, team=team, ref=match_ppda))

    def _moment(self, kind: str, ev: MatchEvent, trace_id: str | None, team: str | None = None,
                ref: float | None = None) -> Moment:
        team = team or ev.team_id
        h, a = self.teams["h"], self.teams["a"]
        names = {k: v for k, v in self.team_names.items()}
        ev_ids = [e.event_id for e in self.chain[-8:]] or [ev.event_id]
        evidence: list[EvidenceItem] = []
        players: list[str] = []
        headline = ""
        if kind in ("goal", "big_chance"):
            xg = float(ev.qualifiers.get("xg", 0.0))
            chain = [e for e in self.chain if e.team_id == ev.team_id]
            passes = [e for e in chain if e.type == "pass"]
            players = list(dict.fromkeys(e.player_id for e in chain[-6:] if e.player_id))
            dur = (ev.clock_s - chain[0].clock_s) if chain else 0.0
            prog = sum(float(e.qualifiers.get("progressive_m", 0.0)) for e in chain)
            started = "turnover" if chain and chain[0].type in ("tackle", "interception", "recovery") else "build-up"
            evidence = [
                EvidenceItem(key="xg", label="Shot xG", value=r2(xg)),
                EvidenceItem(key="started_from", label="Move started from", value=started),
                EvidenceItem(key="chain_passes", label="Passes in the move", value=len(passes)),
                EvidenceItem(key="chain_duration_s", label="Move duration", value=round(dur), unit="s"),
                EvidenceItem(key="progressive_m", label="Metres gained towards goal", value=round(prog), unit="m"),
                EvidenceItem(key="shot_speed_kmh", label="Shot speed", value=r2(ev.qualifiers.get("shot_speed_kmh", 0.0)), unit="km/h"),
                EvidenceItem(key="distance_m", label="Shot distance", value=r2(ev.qualifiers.get("distance_m", 0.0)), unit="m"),
                EvidenceItem(key="team_xg", label="Team xG so far", value=r2(self.teams[team or 'h'].xg)),
            ]
            scorer = self.names.get(ev.player_id or "", "?")
            headline = f"{scorer} {'scores' if kind == 'goal' else 'misses a big chance'} for {self.team_names.get(team or '', team)}"
        elif kind == "momentum_swing":
            w_t, w_o = self.window_stats(team or "h"), self.window_stats("a" if team == "h" else "h")
            evidence = [
                EvidenceItem(key="momentum", label="Momentum (−1 away … +1 home)", value=self.momentum(), before=r2(ref or 0.0), after=self.momentum()),
                EvidenceItem(key="threat_10m", label="Threat created, last 10 min", value=w_t["threat"]),
                EvidenceItem(key="opp_threat_10m", label="Opponent threat, last 10 min", value=w_o["threat"]),
                EvidenceItem(key="xg_10m", label="xG, last 10 min", value=w_t["xg"]),
                EvidenceItem(key="opp_xg_10m", label="Opponent xG, last 10 min", value=w_o["xg"]),
                EvidenceItem(key="ppda_10m", label="PPDA, last 10 min (lower = more pressing)", value=w_t["ppda"]),
            ]
            headline = f"Momentum swings to {self.team_names.get(team or '', team)}"
        elif kind == "pressing_surge":
            w = self.window_stats(team or "h", 480)
            opp = "a" if team == "h" else "h"
            evidence = [
                EvidenceItem(key="ppda_8m", label="PPDA, last 8 min", value=w["ppda"], before=r2(ref or 0.0), after=w["ppda"]),
                EvidenceItem(key="ppda_match", label="PPDA, match before this spell", value=r2(ref or 0.0)),
                EvidenceItem(key="def_actions_high_8m", label="High defensive actions, last 8 min", value=w["def_actions_high"]),
                EvidenceItem(key="opp_turnovers", label=f"{self.team_names.get(opp, opp)} turnovers so far", value=self.teams[opp].turnovers),
                EvidenceItem(key="opp_pass_acc", label=f"{self.team_names.get(opp, opp)} pass accuracy", value=self.teams[opp].summary()["passAccuracy"]),
            ]
            headline = f"{self.team_names.get(team or '', team)} turn up the press"
        else:  # half_time / full_time
            sh, sa = h.summary(), a.summary()
            total = h.possession_s + a.possession_s
            evidence = [
                EvidenceItem(key="score", label="Score", value=f"{h.goals}-{a.goals}"),
                EvidenceItem(key="xg_home", label=f"{names.get('h')} xG", value=sh["xg"]),
                EvidenceItem(key="xg_away", label=f"{names.get('a')} xG", value=sa["xg"]),
                EvidenceItem(key="shots_home", label=f"{names.get('h')} shots", value=h.shots),
                EvidenceItem(key="shots_away", label=f"{names.get('a')} shots", value=a.shots),
                EvidenceItem(key="poss_home", label=f"{names.get('h')} possession", value=round(100 * h.possession_s / total) if total else 50, unit="%"),
                EvidenceItem(key="pass_acc_home", label=f"{names.get('h')} pass accuracy", value=round(100 * sh["passAccuracy"]), unit="%"),
                EvidenceItem(key="pass_acc_away", label=f"{names.get('a')} pass accuracy", value=round(100 * sa["passAccuracy"]), unit="%"),
                EvidenceItem(key="ppda_home", label=f"{names.get('h')} PPDA", value=sh["ppda"] if sh["ppda"] is not None else 0),
                EvidenceItem(key="ppda_away", label=f"{names.get('a')} PPDA", value=sa["ppda"] if sa["ppda"] is not None else 0),
            ]
            top = sorted(self.players.items(), key=lambda kv: kv[1].top_speed, reverse=True)[:1]
            if top:
                pid, pp = top[0]
                players = [pid]
                evidence.append(EvidenceItem(key="top_speed_kmh", label=f"Top speed ({self.names.get(pid, pid)})", value=r2(pp.top_speed * 3.6), unit="km/h"))
            headline = f"{'Half time' if kind == 'half_time' else 'Full time'}: {names.get('h')} {h.goals}–{a.goals} {names.get('a')}"
            team = None
        for pid in players:
            names[pid] = self.names.get(pid, pid)
        return Moment(
            moment_id=f"{ev.event_id}-{kind}",
            match_id=self.match_id,
            kind=kind,  # type: ignore[arg-type]
            clock_s=ev.clock_s,
            period=ev.period,
            team_id=team,
            team_name=self.team_names.get(team or "", None) if team else None,
            player_ids=players,
            names=names,
            headline=headline,
            evidence=evidence,
            event_ids=ev_ids,
            score={"h": h.goals, "a": a.goals},
            trace_id=trace_id,
        )

    def _on_tracking(self, batch: TrackingBatch, out: Output, trace_id: str | None) -> None:
        tagged: list[tuple[str, float, float]] = []
        for fr in batch.frames:
            for pp in fr.players:
                ph = self.players.setdefault(pp.id, PlayerPhys())
                if ph.last is not None:
                    t0, x0, y0 = ph.last
                    dt = fr.t_s - t0
                    if dt >= 0.5:  # 0.5 s differences smooth out positional noise
                        d = math.hypot(pp.x - x0, pp.y - y0)
                        if d < 15:  # ignore teleports (substitutions, restarts)
                            ph.distance_m += d
                            ph.speed = 0.4 * ph.speed + 0.6 * (d / dt)
                            ph.top_speed = max(ph.top_speed, ph.speed)
                            if ph.speed >= SPRINT_MPS and not ph.sprinting:
                                ph.sprints += 1
                                ph.sprinting = True
                            elif ph.speed < SPRINT_MPS - 0.5:
                                ph.sprinting = False
                            if ph.top_speed >= TOP_SPEED_TAG_MPS and ph.top_speed > ph.tagged_top + 0.15:
                                ph.tagged_top = ph.top_speed
                                tagged.append((pp.id, ph.top_speed, fr.t_s))
                        ph.last = (fr.t_s, pp.x, pp.y)
                else:
                    ph.last = (fr.t_s, pp.x, pp.y)
        if batch.frames:
            self.clock = max(self.clock, batch.frames[-1].t_s)
        for pid, mps, t in tagged:
            if self.players[pid].distance_m < 300:
                continue  # ignore the first moves of a match (players jogging into position)
            if not self._cooldown(f"speed_{pid}", 600) or not self._cooldown("speed_any", 90):
                continue
            name = self.names.get(pid, pid)
            ph = self.players[pid]
            ev = MatchEvent(event_id=f"{self.match_id}-spd-{pid}-{int(t)}", match_id=self.match_id, seq=0,
                            period=batch.period, clock_s=t, display_clock=self.display_clock, type="carry")
            out.cues.append(self._cue(
                ev, "player_tag.speed", f"{name}", f"Top speed {mps * 3.6:.1f} km/h  ·  {ph.distance_m / 1000:.1f} km covered",
                4, {"playerId": pid, "player": name, "kmh": r2(mps * 3.6), "distanceKm": r2(ph.distance_m / 1000)},
                trace_id, [EvidenceItem(key="top_speed_kmh", label="Top speed", value=r2(mps * 3.6), unit="km/h")], ttl=5))
        # Downsample to 2 Hz for the browser's live pitch
        step = max(1, batch.fps // 2)
        out.frames = {
            "matchId": self.match_id,
            "t0": batch.t0_s,
            "frames": [fr.model_dump(mode="json") for fr in batch.frames[::step]],
        }
