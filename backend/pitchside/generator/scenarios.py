"""Scripted storylines for demo matches.

A scripted event is *armed* at its minute and happens at the next chance: a forced goal
is the team's next shot, a forced red card is the team's next tackle. Everything else
in the match stays simulated.

Scenarios may come from files or, later, from an AI agent, so they are validated as
untrusted input: unknown fields, types, teams and out-of-range minutes are rejected.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

MAX_EVENTS = 30
MAX_MINUTE = 120
MAX_FILE_BYTES = 64 * 1024
EVENT_TYPES = ("goal", "red_card")
TEAMS = ("home", "away")


class ScenarioError(ValueError):
    """A scenario is malformed or out of bounds."""


@dataclass(frozen=True)
class ScriptedEvent:
    minute: float
    type: Literal["goal", "red_card"]
    team: Literal["home", "away"]

    def __post_init__(self) -> None:
        if isinstance(self.minute, bool) or not isinstance(self.minute, int | float):
            raise ScenarioError(f"minute must be a number, got {self.minute!r}")
        if not 0 <= self.minute <= MAX_MINUTE:
            raise ScenarioError(f"minute must be between 0 and {MAX_MINUTE}, got {self.minute}")
        if self.type not in EVENT_TYPES:
            raise ScenarioError(f"type must be one of {EVENT_TYPES}, got {self.type!r}")
        if self.team not in TEAMS:
            raise ScenarioError(f"team must be one of {TEAMS}, got {self.team!r}")


@dataclass
class Scenario:
    name: str
    description: str = ""
    events: list[ScriptedEvent] = field(default_factory=list)
    # When True, only scripted goals are scored so the storyline's scoreline is exact.
    scripted_goals_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not 0 < len(self.name) <= 64:
            raise ScenarioError("name must be a string of 1-64 characters")
        if not isinstance(self.description, str) or len(self.description) > 500:
            raise ScenarioError("description must be a string of at most 500 characters")
        if len(self.events) > MAX_EVENTS:
            raise ScenarioError(f"at most {MAX_EVENTS} scripted events are allowed")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        if not isinstance(data, dict):
            raise ScenarioError("scenario must be a JSON object")
        _reject_unknown(data, {"name", "description", "events", "scripted_goals_only"}, "scenario")
        raw_events = data.get("events", [])
        if not isinstance(raw_events, list) or len(raw_events) > MAX_EVENTS:
            raise ScenarioError(f"events must be a list of at most {MAX_EVENTS} items")
        events = []
        for e in raw_events:
            if not isinstance(e, dict):
                raise ScenarioError("each event must be a JSON object")
            _reject_unknown(e, {"minute", "type", "team"}, "event")
            try:
                events.append(ScriptedEvent(**e))
            except TypeError as exc:
                raise ScenarioError(f"event is missing a field: {exc}") from None
        if "name" not in data:
            raise ScenarioError("scenario needs a name")
        if not isinstance(data.get("scripted_goals_only", False), bool):
            raise ScenarioError("scripted_goals_only must be true or false")
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            events=events,
            scripted_goals_only=data.get(
                "scripted_goals_only", any(e.type == "goal" for e in events)
            ),
        )


def _s(name: str, description: str, *events: tuple[float, str, str]) -> Scenario:
    scripted = [ScriptedEvent(m, t, side) for m, t, side in events]  # type: ignore[arg-type]
    has_goals = any(e.type == "goal" for e in scripted)
    return Scenario(name, description, scripted, scripted_goals_only=has_goals)


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        _s("open", "No script: a fully simulated match."),
        _s(
            "late-comeback",
            "Away team goes 2-0 up; home team scores three after the 75th minute.",
            (18, "goal", "away"),
            (52, "goal", "away"),
            (76, "goal", "home"),
            (84, "goal", "home"),
            (90, "goal", "home"),
        ),
        _s(
            "red-card-chaos",
            "Home team down to ten men before half-time; the match opens up.",
            (34, "red_card", "home"),
            (41, "goal", "away"),
            (63, "goal", "home"),
            (71, "goal", "away"),
            (88, "goal", "home"),
        ),
        _s(
            "early-goal",
            "Home team scores inside five minutes, then has to hold on.",
            (2, "goal", "home"),
        ),
    ]
}


def _reject_unknown(data: dict, allowed: set[str], what: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ScenarioError(f"unknown {what} field(s): {', '.join(sorted(map(str, unknown)))}")


def load_scenario(name_or_path: str) -> Scenario:
    if name_or_path in SCENARIOS:
        return SCENARIOS[name_or_path]
    path = Path(name_or_path)
    if not path.is_file():
        raise ScenarioError(f"Unknown scenario '{name_or_path}'. Built-in: {', '.join(SCENARIOS)}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ScenarioError(f"scenario file is larger than {MAX_FILE_BYTES // 1024} KB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ScenarioError(f"scenario file is not valid JSON: {exc}") from None
    return Scenario.from_dict(data)
