import json

import numpy as np
import pytest

from pitchside.generator import generate_match
from pitchside.generator import models as m
from pitchside.generator.output import iter_tracking_lines, match_meta
from pitchside.generator.scenarios import (
    SCENARIOS,
    Scenario,
    ScenarioError,
    ScriptedEvent,
    load_scenario,
)
from pitchside.generator.teams import CLUBS, build_team
from pitchside.generator.validate import match_stats, single_match_report
from pitchside.schemas import Event, MatchMeta, TrackingFrame

SHORT = 8.0  # minutes per half, keeps tests fast
RUN_OFF = 8.0  # metres players may overrun the lines


@pytest.fixture(scope="module")
def match():
    return generate_match("ashenford", "greywater", seed=11, half_length=SHORT)


def test_squads_are_stable_and_complete():
    for club in CLUBS:
        a, b = build_team(club), build_team(club)
        assert a == b
        assert len(a.players) == 18
        assert len(a.starting_xi) == 11 and len(a.bench) == 7
        assert len({p.id for p in a.players}) == 18
        assert sum(p.position == "GK" for p in a.players) == 2


def test_events_match_schema_and_are_chronological(match):
    events = [Event.model_validate(e) for e in match.events]
    assert events[0].type == "period" and events[-1].type == "period"
    times = [e.t for e in events]
    assert times == sorted(times)


def test_raw_positions_stay_near_the_pitch(match):
    # Check the simulator's own state, not the written output (which is clamped and would
    # hide a player running off to x=500). Players may overrun into the run-off area when
    # braking from a sprint; the ball is stopped about a metre past the line at most.
    pos, active = np.stack(match._rec_pos), np.stack(match._rec_active)
    players = pos[active]
    assert players[:, 0].min() > -RUN_OFF and players[:, 0].max() < 105 + RUN_OFF
    assert players[:, 1].min() > -RUN_OFF and players[:, 1].max() < 68 + RUN_OFF
    ball = np.stack(match._rec_ball)
    assert ball[:, 0].min() > -1.5 and ball[:, 0].max() < 106.5
    assert ball[:, 1].min() > -1.5 and ball[:, 1].max() < 69.5


def test_tracking_and_meta_match_schema(match):
    lines = list(iter_tracking_lines(match))
    assert len(lines) == len(match._rec_t)
    for line in lines[:: len(lines) // 50]:
        frame = TrackingFrame.model_validate(json.loads(line))
        assert len(frame.players) <= 22
    meta = MatchMeta.model_validate_json(match_meta(match).model_dump_json())
    assert meta.final_score == {"home": match.score[0], "away": match.score[1]}


def test_passes_and_shots_are_consistent(match):
    passes = [e for e in match.events if e["type"] == "pass"]
    assert passes
    assert all(e["outcome"] in ("complete", "incomplete", "out", "offside") for e in passes)
    goals = [e for e in match.events if e["type"] == "goal"]
    assert len(goals) == sum(match.score)


def test_same_seed_same_match():
    a = generate_match("kingsmere", "lowmarsh", seed=5, half_length=4)
    b = generate_match("kingsmere", "lowmarsh", seed=5, half_length=4)
    assert a.events == b.events
    assert np.array_equal(np.stack(a._rec_pos), np.stack(b._rec_pos))


def test_scripted_scenario_controls_scoreline():
    scenario = Scenario(
        name="test",
        events=[ScriptedEvent(3, "goal", "home"), ScriptedEvent(11, "goal", "away")],
        scripted_goals_only=True,
    )
    sim = generate_match("ashenford", "greywater", seed=2, scenario=scenario, half_length=SHORT)
    assert sim.score == [1, 1]
    goals = [e for e in sim.events if e["type"] == "goal"]
    assert goals[0]["team"] == "home" and goals[0]["t"] >= 3 * 60
    assert goals[1]["team"] == "away" and goals[1]["period"] == 2  # minute 11 of 8-minute halves


def test_scripted_minute_after_half_time_is_second_half():
    # Regression: the first-half clock runs past half_length during stoppage time, which used
    # to trigger "minute 46" events at 45+1.
    scenario = Scenario(
        name="t", events=[ScriptedEvent(SHORT + 0.1, "goal", "home")], scripted_goals_only=True
    )
    for seed in range(1, 6):
        sim = generate_match("ashenford", "greywater", seed, scenario=scenario, half_length=SHORT)
        goals = [e for e in sim.events if e["type"] == "goal" and e["team"] == "home"]
        assert goals and all(g["period"] == 2 for g in goals)


def test_scripted_red_card_removes_player():
    scenario = Scenario(name="red", events=[ScriptedEvent(2, "red_card", "home")])
    sim = generate_match("ashenford", "greywater", seed=4, scenario=scenario, half_length=SHORT)
    reds = [e for e in sim.events if e["type"] == "card" and e["outcome"] == "red"]
    assert reds and reds[0]["team"] == "home"
    assert sim.active[:11].sum() == 10


def test_builtin_scenarios_load():
    for name, scenario in SCENARIOS.items():
        assert Scenario.from_dict(scenario.to_dict()) == scenario, name


def test_validation_report_covers_all_metrics(match):
    report = single_match_report(match)
    assert report["summary"]["total"] == len(report["metrics"]) == len(match_stats(match)["values"])


def test_probability_models_behave():
    assert m.xg(99, 34) > m.xg(88, 34) > m.xg(80, 34)
    assert m.xg(94, 34) > m.xg(94, 34, pressure=1.0)
    assert m.xg(94, 34) > m.xg(94, 34, header=True)
    assert m.pass_success(10, 10, 10, 75, False) > m.pass_success(40, 10, 10, 75, True)
    assert m.pass_success(15, 10, 10, 75, False) > m.pass_success(15, 0.5, 0.5, 75, False)
    assert m.xt(100, 34) > m.xt(70, 34) > m.xt(30, 34)
    blockers = np.array([[100.0, 34.0], [100.0, 5.0]])
    assert m.cone_blockers(90, 34, blockers) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"name": "x", "events": [{"minute": 10, "type": "own_goal", "team": "home"}]},
        {"name": "x", "events": [{"minute": 10, "type": "goal", "team": "neutral"}]},
        {"name": "x", "events": [{"minute": -5, "type": "goal", "team": "home"}]},
        {"name": "x", "events": [{"minute": "10", "type": "goal", "team": "home"}]},
        {"name": "x", "events": [{"minute": 10, "type": "goal"}]},
        {"name": "x", "events": [{"minute": 10, "type": "goal", "team": "home", "x": 1}]},
        {"name": "x", "events": [{"minute": 10, "type": "goal", "team": "home"}] * 31},
        {"name": "x", "unexpected": True},
        {"events": []},
        ["not", "an", "object"],
    ],
)
def test_invalid_scenarios_are_rejected(data):
    with pytest.raises(ScenarioError):
        Scenario.from_dict(data)


def test_scenario_file_loading(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"name": "derby", "events": [{"minute": 89, "type": "goal", "team": "away"}]})
    )
    assert load_scenario(str(good)).scripted_goals_only
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ScenarioError):
        load_scenario(str(bad))
    with pytest.raises(ScenarioError):
        load_scenario(str(tmp_path / "missing.json"))
