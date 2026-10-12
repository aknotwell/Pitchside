import json
import random

import numpy as np
import pytest

from matchlens.contracts import StreamMessage
from matchlens.interpret.state import MatchInterpreter
from matchlens.sim.engine import BOX_X, BOX_Y, MatchEngine, outside_box, simulate

SHORT = {"period_min": 8}


@pytest.fixture(scope="module")
def short_match():
    return simulate(11, params=SHORT)


def run_interpreter(messages):
    it = MatchInterpreter("m00011")
    cues, moments = [], []
    for m in messages:
        out = it.handle(m)
        cues += out.cues
        moments += out.moments
    return it, cues, moments


def test_simulator_is_deterministic(short_match):
    again = simulate(11, params=SHORT)
    a = [e.model_dump() for e in short_match.events]
    b = [e.model_dump() for e in again.events]
    assert a == b
    assert np.array_equal(short_match.tracking_players, again.tracking_players, equal_nan=True)


def test_stream_is_ordered_and_complete(short_match):
    msgs = list(short_match.stream())
    assert msgs[0].kind == "metadata" and msgs[-1].kind == "end"
    events = [m for m in msgs if m.kind == "event"]
    assert len(events) == len(short_match.events)
    seqs = [m.payload["seq"] for m in events]
    assert seqs == sorted(seqs)
    # every message must survive a JSON round trip (that's what goes on the wire)
    for m in msgs[:50]:
        StreamMessage.model_validate_json(m.model_dump_json())


def test_event_sanity(short_match):
    passes = [e for e in short_match.events if e.type == "pass"]
    acc = sum(e.outcome == "complete" for e in passes) / len(passes)
    assert 0.65 < acc < 0.92
    for e in short_match.events:
        if e.x is not None:
            assert -53 <= e.x <= 53 and -35 <= e.y <= 35


def test_players_have_roles_traits_and_teams_have_styles(short_match):
    meta = short_match.metadata
    for team in (meta.home, meta.away):
        assert team.style_name and set(team.style) == {"press", "line", "width", "tempo", "directness"}
        assert all(p.role and len(p.traits) >= 15 for p in team.players)
    # individuality: no two outfield starters share the same trait profile
    profiles = [tuple(sorted(p.traits.items())) for p in meta.home.players[1:11]]
    assert len(set(profiles)) == len(profiles)


class _StateLog(MatchEngine):
    def _step(self, t):
        super()._step(t)
        self.log.append((self.state, self.owner))


@pytest.fixture(scope="module")
def logged():
    eng = _StateLog(11, params=SHORT)
    eng.log = []
    return eng, eng.run()


def test_ball_keeps_moving_while_a_player_has_it(logged):
    """No more frozen ball while someone is on it: they take a touch, dribble or shield."""
    eng, r = logged
    v = np.r_[0.0, np.linalg.norm(np.diff(r.tracking_ball[:, :2], axis=0), axis=1) * r.metadata.fps]
    owned = np.array([s == "owned" for s, _ in eng.log])
    longest, run = 0, 0
    for still in (v < 0.2) & owned:
        run = run + 1 if still else 0
        longest = max(longest, run)
    assert longest / r.metadata.fps < 3.0  # (goalkeepers holding a catch is the longest legitimate pause)
    assert ((v < 0.2) & owned).sum() / owned.sum() < 0.12


def test_ball_carrier_is_engaged_in_open_play(logged):
    """Somebody closes the ball down: the carrier's nearest opponent is usually within a few metres."""
    eng, r = logged
    near = []
    for k in range(0, len(eng.log), 5):
        state, owner = eng.log[k]
        if state != "owned" or owner < 0 or eng.is_gk[owner]:
            continue
        team = r.player_ids[owner][0]
        opp = [j for j, pid in enumerate(r.player_ids) if pid[0] != team]
        d = np.linalg.norm(r.tracking_players[k, opp] - r.tracking_ball[k, :2], axis=1)
        near.append(np.nanmin(d))
    near = np.array(near)
    assert np.median(near) < 6.0
    assert (near > 12).mean() < 0.1


def test_outside_box_moves_points_to_the_nearest_edge():
    pts = np.array([[50.0, 0.0], [37.0, 5.0], [45.0, 19.0], [30.0, 0.0], [-50.0, 0.0]])
    out = outside_box(pts, 1.0)
    assert out[0].tolist() == [BOX_X, 0.0]  # deep in the box: out through the front edge
    assert out[1].tolist() == [BOX_X, 5.0]
    assert out[2].tolist() == [45.0, BOX_Y]  # near the side edge: out sideways
    assert out[3].tolist() == [30.0, 0.0] and out[4].tolist() == [-50.0, 0.0]  # already outside / other end
    assert outside_box(pts, -1.0)[4].tolist() == [-BOX_X, 0.0]


class _GoalKickLog(MatchEngine):
    """Records, for every tick a goal kick is not yet kicked, whether an opponent is in that penalty area."""

    def _step(self, t):
        super()._step(t)
        side = self._goal_kick_side()
        if side is None or self.state != "owned":
            return
        opp = self.ts["a" if self.poss == "h" else "h"].A
        self.violations += int((outside_box(self.pos[opp], side) != self.pos[opp]).any())

    def _gain(self, i, t, setpiece=None, how=None):
        if setpiece == "goal_kick":  # the restart is taken now
            opp = self.ts["a" if self.tid_of[i] == "h" else "h"].A
            mates = [j for j in self.ts[self.tid_of[i]].A if j != i]
            side = 1.0 if self.ball[0] > 0 else -1.0
            self.kicks += 1
            self.violations += int((outside_box(self.pos[opp], side) != self.pos[opp]).any())
            self.mates_in_box += int((outside_box(self.pos[mates], side) != self.pos[mates]).any(axis=1).sum())
        super()._gain(i, t, setpiece=setpiece, how=how)


def test_no_opponent_in_the_box_at_a_goal_kick():
    """Hard rule: from the restart until the keeper kicks it, every opponent is outside the penalty area.
    The keeper's own team may stay in it (and usually does, to take a short goal kick)."""
    kicks = violations = mates_in_box = 0
    for seed in (11, 12, 13):
        eng = _GoalKickLog(seed, params={"period_min": 15})
        eng.kicks = eng.violations = eng.mates_in_box = 0
        eng.run()
        kicks += eng.kicks
        violations += eng.violations
        mates_in_box += eng.mates_in_box
    assert kicks >= 5
    assert violations == 0
    assert mates_in_box >= kicks  # on average at least one teammate in the box per goal kick


def test_top_speeds_are_human(short_match):
    """Half-time resets and substitutions move players instantly; that must not count as running."""
    it, _, _ = run_interpreter(short_match.stream())
    assert it.state_doc()["topSpeeds"]
    assert all(s["kmh"] < 40 for s in it.state_doc()["topSpeeds"])


def test_interpreter_replay_is_identical(short_match):
    msgs = list(short_match.stream())
    it1, cues1, moments1 = run_interpreter(msgs)
    it2, cues2, moments2 = run_interpreter(msgs)
    s1, s2 = it1.state_doc(), it2.state_doc()
    assert json.dumps(s1, sort_keys=True) == json.dumps(s2, sort_keys=True)
    assert [c.id for c in cues1] == [c.id for c in cues2]
    assert [m.moment_id for m in moments1] == [m.moment_id for m in moments2]


def test_interpreter_ignores_duplicate_events(short_match):
    msgs = list(short_match.stream())
    rng = random.Random(3)
    dupes = []
    for m in msgs:
        dupes.append(m)
        if m.kind == "event" and rng.random() < 0.2:
            dupes.append(m)  # at-least-once delivery
    it1, cues1, _ = run_interpreter(msgs)
    it2, cues2, _ = run_interpreter(dupes)
    assert it1.state_doc()["home"] == it2.state_doc()["home"]
    assert it1.state_doc()["away"] == it2.state_doc()["away"]
    assert [c.id for c in cues1] == [c.id for c in cues2]


def test_goals_match_events_and_moments_have_evidence(short_match):
    it, cues, moments = run_interpreter(short_match.stream())
    goals = sum(1 for e in short_match.events if e.type == "shot" and e.outcome == "goal")
    s = it.state_doc()
    assert s["home"]["goals"] + s["away"]["goals"] == goals
    assert sum(1 for c in cues if c.template == "goal") == goals
    kinds = {m.kind for m in moments}
    assert {"half_time", "full_time"} <= kinds
    for m in moments:
        assert m.evidence, m.kind
        assert all(isinstance(e.value, (int, float, str)) for e in m.evidence)


def test_offside_is_called_and_restarts_with_the_defending_side():
    """Law 11: a pass to an offside player ends 'offside', and the other side takes an indirect free kick."""
    called = 0
    for seed in (1, 2, 4):
        ev = simulate(seed, params=SHORT).events
        for n, e in enumerate(ev):
            if e.type != "offside":
                continue
            called += 1
            passes = [x for x in ev[:n] if x.type == "pass"]
            assert passes[-1].outcome == "offside" and passes[-1].team_id == e.team_id
            assert passes[-1].qualifiers.get("set_piece") not in ("throw_in", "goal_kick", "corner")
            restart = next(x for x in ev[n + 1:] if x.type in ("pass", "period_end"))
            if restart.type == "pass":  # (unless the whistle went for the end of the half first)
                assert restart.team_id != e.team_id and restart.qualifiers.get("set_piece") == "free_kick"
    assert called > 0
