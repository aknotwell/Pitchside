import pytest

from pitchside.generator import MatchSim, build_team, generate_match
from pitchside.live import LiveFeed
from pitchside.schemas import TrackingFrame

SHORT = 4.0  # minutes per half


def new_sim(seed: int = 5) -> MatchSim:
    return MatchSim(build_team("ashenford"), build_team("greywater"), seed, half_length=SHORT)


@pytest.fixture(scope="module")
def played():
    """Play a match through the feed in 1.7 s steps, logging when each event came out."""
    feed = LiveFeed(new_sim())
    released: list[tuple[float, dict]] = []
    frames = []
    t = 0.0
    while not feed.finished:
        for ev in feed.advance_to(t):
            released.append((feed.t, ev))
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
    ids = [ev["id"] for _, ev in released]
    assert sorted(ids) == sorted(ev["id"] for ev in feed.sim.events)
    assert len(ids) == len(set(ids))


def test_events_are_not_released_before_they_finish(played):
    feed, released, _ = played
    end = feed.sim.t
    for at, ev in released:
        # Anything still pending when the match ends is flushed at the final whistle.
        assert ev["t"] + ev["duration"] <= at or at == end, ev


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


def test_frame_is_none_before_kickoff():
    assert LiveFeed(new_sim()).frame() is None
