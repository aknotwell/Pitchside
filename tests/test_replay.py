import base64
import json

from matchlens.services.replay import decode, package
from matchlens.sim.engine import simulate


def test_decode_accepts_base64_and_plain_cloudevents():
    ev = {"type": "MatchLens.Match.FullTime", "data": {"matchId": "m1", "seed": 7}}
    raw = json.dumps(ev)
    assert decode(raw) == ev
    assert decode(base64.b64encode(raw.encode()).decode()) == ev


def test_replay_is_the_same_match():
    """The replay job re-simulates from the seed; it must reproduce the live match exactly."""
    live = simulate(7, match_id="m7-x", params={"period_min": 6})
    pkg = package("m7-x", 7, 6, cues=[])
    assert [e["event_id"] for e in pkg["events"]] == [e.event_id for e in live.events]
    assert pkg["meta"]["seed"] == 7 and pkg["frames"]


def test_replay_frames_carry_the_pending_restart():
    live = simulate(7, match_id="m7-x", params={"period_min": 6})
    pkg = package("m7-x", 7, 6, cues=[])
    by_t = {round(float(t), 1): rs for t, rs in zip(live.tracking_t, live.tracking_restart, strict=True)}
    restarts = [f[5] for f in pkg["frames"]]
    assert restarts == [list(rs) if (rs := by_t[f[0]]) else None for f in pkg["frames"]]
    assert restarts[0][0] == "kickoff" and any(r and r[0] == "throw_in" for r in restarts)
    assert any(r is None for r in restarts)
