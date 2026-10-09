"""Scripted storylines for demo matches.

A scripted event is *armed* at its minute and happens at the next chance: a forced goal
is the team's next shot, a forced red card is the team's next tackle. Everything else
in the match stays simulated.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ScriptedEvent:
    minute: float
    type: Literal["goal", "red_card"]
    team: Literal["home", "away"]


@dataclass
class Scenario:
    name: str
    description: str = ""
    events: list[ScriptedEvent] = field(default_factory=list)
    # When True, only scripted goals are scored so the storyline's scoreline is exact.
    scripted_goals_only: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        events = [ScriptedEvent(**e) for e in data.get("events", [])]
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


def load_scenario(name_or_path: str) -> Scenario:
    if name_or_path in SCENARIOS:
        return SCENARIOS[name_or_path]
    path = Path(name_or_path)
    if path.exists():
        return Scenario.from_dict(json.loads(path.read_text()))
    raise KeyError(f"Unknown scenario '{name_or_path}'. Built-in: {', '.join(SCENARIOS)}")
