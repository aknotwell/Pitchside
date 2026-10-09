"""Shared data contracts for every Pitchside component.

Coordinates are metres on a 105 x 68 pitch, origin at the bottom-left corner of the
home team's first-half defending end. `t` is seconds since kick-off, monotonic across
both periods (the second period continues from where the first stopped).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0

Position = Literal["GK", "LB", "CB", "RB", "DM", "CM", "AM", "LW", "RW", "ST"]
Side = Literal["home", "away"]


class Point(BaseModel):
    x: float
    y: float


class Ratings(BaseModel):
    """0-100 attribute ratings used by the simulator."""

    pace: int
    passing: int
    dribbling: int
    finishing: int
    defending: int
    decisions: int
    stamina: int
    goalkeeping: int


class Player(BaseModel):
    id: str
    name: str
    number: int
    position: Position
    ratings: Ratings


class Tactics(BaseModel):
    """0-1 sliders that shape how a team plays."""

    press_height: float = 0.5
    tempo: float = 0.5
    width: float = 0.5
    directness: float = 0.5


class Team(BaseModel):
    id: str
    name: str
    short_name: str
    colors: tuple[str, str]
    formation: str
    tactics: Tactics
    starting_xi: list[str]
    bench: list[str]
    players: list[Player]


class MatchMeta(BaseModel):
    match_id: str
    seed: int
    competition: str
    venue: str
    home: Team
    away: Team
    scenario: dict[str, Any] | None = None
    final_score: dict[Side, int] | None = None
    generator_version: str


EventType = Literal[
    "period",
    "pass",
    "carry",
    "dribble",
    "shot",
    "goal",
    "save",
    "pressure",
    "tackle",
    "interception",
    "clearance",
    "block",
    "ball_recovery",
    "possession_change",
    "foul",
    "card",
    "offside",
    "sub",
]


class Event(BaseModel):
    id: str
    t: float
    period: int
    clock: str = Field(description="Broadcast clock, e.g. '45+2:13'")
    type: EventType
    team: Side | None = None
    player: str | None = None
    possession: int = Field(description="Possession sequence number")
    start: Point | None = None
    end: Point | None = None
    duration: float = 0.0
    outcome: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PlayerFrame(BaseModel):
    id: str
    x: float
    y: float
    speed: float = Field(description="m/s")


class TrackingFrame(BaseModel):
    t: float
    period: int
    live: bool = Field(description="False while the ball is dead (restarts, goals, subs)")
    ball: tuple[float, float, float]
    players: list[PlayerFrame]


class Moment(BaseModel):
    id: str
    t: float
    type: str
    importance: float = Field(ge=0, le=1)
    reasons: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)
    players: list[str] = Field(default_factory=list)


class OverlayCue(BaseModel):
    id: str
    t_in: float
    t_out: float
    slot: Literal["lower_third", "tag", "badge", "bar", "fullscreen"]
    template: str
    data: dict[str, Any] = Field(default_factory=dict)
    text: dict[str, str] = Field(default_factory=dict, description="language code -> text")
    audiences: list[str] = Field(default_factory=list)


class ViewerProfile(BaseModel):
    persona: Literal["fan", "analyst", "academy", "scout"]
    language: str = "en"
    fav_club: str | None = None
    fav_player: str | None = None
    focus_metric: str | None = None
