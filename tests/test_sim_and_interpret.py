import json
import random

import numpy as np
import pytest

from matchlens.contracts import StreamMessage
from matchlens.interpret.state import MatchInterpreter
from matchlens.sim.engine import simulate

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
