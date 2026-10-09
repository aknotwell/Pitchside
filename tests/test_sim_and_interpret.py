import json
import random

import numpy as np
import pytest

from matchlens.contracts import StreamMessage
from matchlens.interpret.state import MatchInterpreter
from matchlens.sim.engine import MatchEngine, simulate

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
