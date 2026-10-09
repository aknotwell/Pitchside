import pytest

from pitchside.generator import MatchSim, build_team, generate_match
from pitchside.live import LiveFeed
from pitchside.schemas import LiveMeta, LiveTick, TrackingFrame

SHORT = 4.0  # minutes per half


def new_sim(seed: int = 5) -> MatchSim:
    return MatchSim(build_team("ashenford"), build_team("greywater"), seed, half_length=SHORT)


@pytest.fixture(scope="module")
def played():
    """Play a match through the feed in 1.7 s steps, logging when each event came out."""
    feed = LiveFeed(new_sim())
    released: list[tuple[float, bool, dict]] = []
    frames = []
    t = 0.0
    while not feed.finished:
        for ev in feed.advance_to(t):
            released.append((feed.t, feed.finished, ev))
        frames.append(feed.frame())
        t += 1.7
    return feed, released, frames


def test_steps_matches_run():
    stepped = new_sim()
    for _ in stepped.steps():
        pass
    ran = generate_match("ashenford", "greywater", 5, half_length=SHORT)
    assert stepped.events == ran.events
    assert stepped._rec_t == ran._rec_t


def test_every_event_is_released_once(played):
    feed, released, _ = played
    ids = [ev["id"] for _, _, ev in released]
    assert sorted(ids) == sorted(ev["id"] for ev in feed.sim.events)
    assert len(ids) == len(set(ids))


def test_events_are_not_released_before_they_finish(played):
    _, released, _ = played
    for at, finished, ev in released:
        # Anything still pending when the match ends is flushed at the final whistle.
        assert ev["t"] + ev["duration"] <= at or finished, ev


def test_score_comes_from_released_goals(played):
    feed, _, _ = played
    assert [feed.score["home"], feed.score["away"]] == feed.sim.score


def test_frames_follow_the_tracking_schema(played):
    _, _, frames = played
    times = [f["t"] for f in frames]
    assert times == sorted(times)
    for f in frames[::20]:
        TrackingFrame.model_validate(f)
        assert len(f["players"]) <= 22
        assert ":" in f["clock"]


def test_last_frame_time_and_clock_agree(played):
    feed, _, frames = played
    last = frames[-1]
    assert feed.finished and feed.t == last["t"]
    assert last["clock"] == feed.sim.clock(last["t"])
    assert feed.sim.clock() != last["clock"]  # the sim itself has moved on a tick


def test_frame_is_none_before_kickoff():
    assert LiveFeed(new_sim()).frame() is None


def test_server_streams_meta_then_frames():
    from fastapi.testclient import TestClient

    from pitchside.live.server import app

    with TestClient(app) as client, client.websocket_connect("/ws/match?seed=3&speed=20") as ws:
        first = ws.receive_json()
        assert first["type"] == "meta"
        assert first["meta"]["final_score"] is None
        assert first["meta"]["scenario"] == {"name": "open"}
        assert first["meta"]["home"]["id"] == "ashenford"
        frames = [ws.receive_json() for _ in range(5)]
    LiveMeta.model_validate(first)
    for f in frames:
        LiveTick.model_validate(f)
    assert frames[-1]["frame"]["t"] > frames[0]["frame"]["t"]


@pytest.mark.parametrize(
    "query", ["home=nowhere", "home=greywater", "scenario=../secret.json", "speed=500"]
)
def test_server_rejects_bad_options(query):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from pitchside.live.server import app

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/ws/match?{query}") as ws,
    ):
        ws.receive_json()


def test_server_hides_the_scenario_script():
    from fastapi.testclient import TestClient

    from pitchside.live.server import app

    url = "/ws/match?scenario=late-comeback&speed=20"
    with TestClient(app) as client, client.websocket_connect(url) as ws:
        meta = ws.receive_json()["meta"]
    assert meta["scenario"] == {"name": "late-comeback"}


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://localhost:5173", True),
        ("http://127.0.0.1:8000", True),
        ("http://testserver", True),  # same host as the server
        ("https://evil.example", False),
        ("http://localhost.evil.example", False),
    ],
)
def test_server_checks_origin(origin, allowed):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from pitchside.live.server import app

    with TestClient(app) as client:
        if allowed:
            with client.websocket_connect("/ws/match", headers={"origin": origin}) as ws:
                assert ws.receive_json()["type"] == "meta"
        else:
            with (
                pytest.raises(WebSocketDisconnect),
                client.websocket_connect("/ws/match", headers={"origin": origin}) as ws,
            ):
                ws.receive_json()
