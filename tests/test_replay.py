import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceNotFoundError

from matchlens.services import replay as replay_mod
from matchlens.services.replay import Refused, decode, handle, package, wait_for_recap
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


# ---------------------------------------------------------------- job robustness (#39, #40, #41, #62)
def _aired(seed=7, period_min=6, **over):
    r = simulate(seed, match_id="m7-x", params={"period_min": period_min})
    final = replay_mod.states_over_time(r)[-1]
    data = {"matchId": "m7-x", "seed": seed, "periodMin": period_min, "paramsVersion": r.metadata.params_version,
            "score": {"h": final["home"]["goals"], "a": final["away"]["goals"]}}
    return {**data, **over}


def test_replay_carries_stats_over_time():
    """Regression: replays showed full-time stats from kick-off; the package now has the state at each 5 s."""
    pkg = package("m7-x", 7, 6, cues=[])
    states = pkg["states"]
    assert len(states) > 10 and "meta" not in states[0] and "window10" not in states[0]
    clocks = [s["clockS"] for s in states]
    assert clocks == sorted(clocks)
    shots = [s["home"]["shots"] + s["away"]["shots"] for s in states]
    assert shots[0] == 0 and shots == sorted(shots) and shots[-1] > 0
    assert states[-1]["ended"]


def test_replay_matching_what_aired_is_packaged():
    pkg = package("m7-x", 7, 6, cues=[], aired=_aired())
    assert pkg["states"]


@pytest.mark.parametrize("over", [{"paramsVersion": "something-else"}, {"score": {"h": 9, "a": 9}}])
def test_replay_that_differs_from_what_aired_is_refused(over):
    with pytest.raises(Refused):
        package("m7-x", 7, 6, cues=[], aired=_aired(**over))


class _Poison:
    queue_name = "replay-requests-poison"

    def __init__(self):
        self.sent = []

    async def send_message(self, body):
        self.sent.append(json.loads(body))


def _msg(content, dequeue_count=1):
    return SimpleNamespace(content=content, dequeue_count=dequeue_count)


@pytest.mark.parametrize("content", ["not json at all", json.dumps({"data": {"seed": 3}}),
                                     json.dumps({"data": {"matchId": "m1", "seed": "x"}})])
def test_unreadable_request_is_poisoned_not_retried(content):
    poison = _Poison()
    assert asyncio.run(handle(_msg(content), db=None, blobs=None, poison=poison)) is True
    assert len(poison.sent) == 1 and poison.sent[0]["content"] == content


def test_request_that_keeps_failing_is_poisoned():
    poison = _Poison()
    good = json.dumps({"data": {"matchId": "m1", "seed": 3}})
    assert asyncio.run(handle(_msg(good, dequeue_count=4), db=None, blobs=None, poison=poison)) is True
    assert "failed 3 times" in poison.sent[0]["reason"]


class _Q:
    def __init__(self, items):
        self.items = items

    async def query_items(self, query, partition_key):
        for i in self.items:
            yield i


class _Runs:
    def __init__(self, appear_after):
        self.reads, self.appear_after = 0, appear_after

    async def read_item(self, item, partition_key):
        self.reads += 1
        if self.reads <= self.appear_after:
            raise ResourceNotFoundError("not yet")
        return {"id": item}


def test_replay_waits_for_the_full_time_recap():
    runs = _Runs(appear_after=2)
    db = SimpleNamespace(get_container_client=lambda n: {"moments": _Q(["e9-full_time"]), "runs": runs}[n])
    assert asyncio.run(wait_for_recap(db, "m1", timeout_s=5, every_s=0)) is True
    assert runs.reads == 3


def test_replay_gives_up_waiting_for_the_recap():
    db = SimpleNamespace(get_container_client=lambda n: {"moments": _Q([]), "runs": _Runs(0)}[n])
    assert asyncio.run(wait_for_recap(db, "m1", timeout_s=0, every_s=0)) is False


def test_replay_frames_carry_the_pending_restart():
    live = simulate(7, match_id="m7-x", params={"period_min": 6})
    pkg = package("m7-x", 7, 6, cues=[])
    by_t = {round(float(t), 1): rs for t, rs in zip(live.tracking_t, live.tracking_restart, strict=True)}
    restarts = [f[5] for f in pkg["frames"]]
    assert restarts == [list(rs) if (rs := by_t[f[0]]) else None for f in pkg["frames"]]
    assert restarts[0][0] == "kickoff" and any(r and r[0] == "throw_in" for r in restarts)
    assert any(r is None for r in restarts)
