"""Step a match simulation forward at whatever pace the caller chooses.

The simulator writes each event with its outcome already decided at its start time `t`
(a pass's result is rolled when it is played). A live feed must not show the future, so
each event is held back until `t + duration`, when its outcome has actually happened.
"""

from __future__ import annotations

import heapq

from pitchside.generator.sim import MatchSim


class LiveFeed:
    def __init__(self, sim: MatchSim) -> None:
        self.sim = sim
        self._steps = sim.steps()
        self._frame: int | None = None  # index of the latest recorded frame
        self._queued = 0  # sim.events[:_queued] have been queued for release
        self._pending: list[tuple[float, int]] = []  # (release time, event index)
        self.score = {"home": 0, "away": 0}
        self.finished = False

    @property
    def t(self) -> float:
        """Match time of the latest frame (seconds since kick-off)."""
        return self.sim.t

    def advance_to(self, t: float) -> list[dict]:
        """Simulate up to match time `t` and return the events that are now public.

        Stops at the first frame at or after `t`. Events come back in release order, and
        once the match ends every remaining event is released.
        """
        while not self.finished and (self._frame is None or self.sim.t < t):
            try:
                self._frame = next(self._steps)
            except StopIteration:
                self.finished = True
        return self._release()

    def _release(self) -> list[dict]:
        events = self.sim.events
        for i in range(self._queued, len(events)):
            ev = events[i]
            heapq.heappush(self._pending, (ev["t"] + ev["duration"], i))
        self._queued = len(events)

        out = []
        while self._pending and (self.finished or self._pending[0][0] <= self.sim.t):
            ev = events[heapq.heappop(self._pending)[1]]
            if ev["type"] == "goal":
                self.score[ev["team"]] += 1
            out.append(ev)
        return out

    def frame(self) -> dict | None:
        """The latest tracking frame (`TrackingFrame` fields plus the broadcast clock)."""
        i = self._frame
        if i is None:
            return None
        sim = self.sim
        roster = sim.rosters[sim._rec_roster[i]]
        pos, speed, active = sim._rec_pos[i], sim._rec_speed[i], sim._rec_active[i]
        return {
            "t": sim._rec_t[i],
            "period": sim._rec_period[i],
            "clock": sim.clock(),
            "live": bool(sim._rec_live[i]),
            "ball": [round(float(v), 2) for v in sim._rec_ball[i]],
            "players": [
                {
                    "id": roster[k],
                    "x": round(float(pos[k, 0]), 2),
                    "y": round(float(pos[k, 1]), 2),
                    "speed": round(float(speed[k]), 2),
                }
                for k in range(22)
                if active[k]
            ],
        }
