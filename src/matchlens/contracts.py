"""Versioned data contracts shared by every MatchLens service.

Coordinates are metres on a 105 x 68 pitch with the origin at the centre spot.
The home team always attacks towards +x (the simulator does not swap ends at
half-time), so ``x_att = x if home else -x`` gives a team-relative frame.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
HALF_LENGTH = PITCH_LENGTH / 2
HALF_WIDTH = PITCH_WIDTH / 2

EventType = Literal[
    "period_start",
    "period_end",
    "pass",
    "carry",
    "pressure",
    "tackle",
    "interception",
    "recovery",
    "shot",
    "foul",
    "card",
    "substitution",
    "ball_out",
    "save",
]

Persona = Literal["analyst", "casual"]
LANGUAGES: tuple[str, ...] = ("en", "es", "ja")
PERSONAS: tuple[Persona, ...] = ("analyst", "casual")


class Player(BaseModel):
    id: str
    name: str
    number: int
    position: str
    team_id: str


class Team(BaseModel):
    id: str
    name: str
    short: str
    color: str
    formation: str
    players: list[Player]


class MatchMetadata(BaseModel):
    schema_version: str = SCHEMA_VERSION
    match_id: str
    home: Team
    away: Team
    seed: int
    params_version: str
    speed: float = 1.0
    fps: int = 10
    competition: str = "Northern Shield League (fictional)"


class MatchEvent(BaseModel):
    schema_version: str = SCHEMA_VERSION
    event_id: str
    match_id: str
    seq: int
    period: int
    clock_s: float = Field(
        description="Monotonic seconds of play since kick-off; period 2 starts where period 1 ended."
    )
    display_clock: str = Field(default="", description="Broadcast clock, e.g. '45+2:10' or '67:04'.")
    type: EventType
    team_id: str | None = None
    player_id: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    related_player_id: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    source: Literal["sim", "cv"] = "sim"
    confidence: float = 1.0


class PlayerPos(BaseModel):
    id: str
    x: float
    y: float


class Frame(BaseModel):
    t_s: float
    ball: tuple[float, float, float]
    players: list[PlayerPos]


class TrackingBatch(BaseModel):
    schema_version: str = SCHEMA_VERSION
    match_id: str
    period: int
    t0_s: float
    fps: int
    frames: list[Frame]


class StreamMessage(BaseModel):
    """Envelope for everything on the ``match-stream`` Event Hub (partition key = match_id)."""

    kind: Literal["metadata", "event", "tracking", "end"]
    match_id: str
    clock_s: float
    trace_id: str | None = None
    payload: dict[str, Any]


class EvidenceItem(BaseModel):
    key: str
    label: str
    value: float | int | str
    unit: str = ""
    before: float | None = None
    after: float | None = None
    supports: bool = True


MomentKind = Literal[
    "goal",
    "big_chance",
    "momentum_swing",
    "pressing_surge",
    "half_time",
    "full_time",
]


class Moment(BaseModel):
    schema_version: str = SCHEMA_VERSION
    moment_id: str
    match_id: str
    kind: MomentKind
    clock_s: float
    period: int
    team_id: str | None = None
    team_name: str | None = None
    player_ids: list[str] = Field(default_factory=list)
    names: dict[str, str] = Field(
        default_factory=dict, description="Every proper noun the explainer may use, id -> display name."
    )
    headline: str
    evidence: list[EvidenceItem]
    event_ids: list[str] = Field(default_factory=list)
    score: dict[str, int] = Field(default_factory=dict)
    trace_id: str | None = None


class CueEvidence(BaseModel):
    event_ids: list[str] = Field(default_factory=list)
    metrics: list[EvidenceItem] = Field(default_factory=list)


class OverlayCue(BaseModel):
    schema_version: str = SCHEMA_VERSION
    id: str = Field(description="cue id; also the Cosmos document id")
    match_id: str = Field(alias="matchId")
    display_at_s: float
    ttl_s: float = 8.0
    priority: int = 2
    template: str
    persona: Persona | Literal["all"] = "all"
    lang: str = "en"
    title: str
    text: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: CueEvidence = Field(default_factory=CueEvidence)
    verified: bool = True
    source: Literal["template", "agent", "fallback"] = "template"
    moment_id: str | None = None
    trace_id: str | None = None

    model_config = {"populate_by_name": True}

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


def cue_group(match_id: str, persona: str, lang: str) -> str:
    """Web PubSub group that receives a match's cues for one persona and language."""
    return f"m.{match_id}.cues.{persona}.{lang}"


def frames_group(match_id: str) -> str:
    return f"m.{match_id}.frames"


def state_group(match_id: str) -> str:
    return f"m.{match_id}.state"
