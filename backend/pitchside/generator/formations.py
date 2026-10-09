"""Formation anchor positions.

Anchors are in the team's *attacking frame*: x runs from the team's own goal line (0)
to the opponent's (105), and +y is the left-hand side when facing the opponent's goal.
They describe a neutral mid-block with the ball near the centre circle; the simulator
shifts and compresses them as the ball and possession move.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    position: str
    x: float
    y: float

    @property
    def line(self) -> str:
        if self.position == "GK":
            return "gk"
        if self.position in ("LB", "CB", "RB"):
            return "def"
        if self.position in ("DM", "CM", "AM"):
            return "mid"
        return "att"


def _f(*slots: tuple[str, float, float]) -> list[Slot]:
    return [Slot(p, x, y) for p, x, y in slots]


FORMATIONS: dict[str, list[Slot]] = {
    "4-3-3": _f(
        ("GK", 5, 34),
        ("LB", 32, 60),
        ("CB", 26, 43),
        ("CB", 26, 25),
        ("RB", 32, 8),
        ("DM", 40, 34),
        ("CM", 48, 47),
        ("CM", 48, 21),
        ("LW", 66, 60),
        ("ST", 70, 34),
        ("RW", 66, 8),
    ),
    "4-2-3-1": _f(
        ("GK", 5, 34),
        ("LB", 32, 60),
        ("CB", 26, 43),
        ("CB", 26, 25),
        ("RB", 32, 8),
        ("DM", 40, 42),
        ("CM", 40, 26),
        ("LW", 62, 58),
        ("AM", 60, 34),
        ("RW", 62, 10),
        ("ST", 72, 34),
    ),
    "4-4-2": _f(
        ("GK", 5, 34),
        ("LB", 30, 60),
        ("CB", 25, 43),
        ("CB", 25, 25),
        ("RB", 30, 8),
        ("LW", 52, 60),
        ("CM", 44, 42),
        ("CM", 44, 26),
        ("RW", 52, 8),
        ("ST", 68, 40),
        ("ST", 68, 28),
    ),
    "3-5-2": _f(
        ("GK", 5, 34),
        ("CB", 26, 50),
        ("CB", 24, 34),
        ("CB", 26, 18),
        ("LB", 48, 62),
        ("DM", 40, 34),
        ("CM", 48, 46),
        ("CM", 48, 22),
        ("RB", 48, 6),
        ("ST", 68, 40),
        ("ST", 68, 28),
    ),
}

BENCH_POSITIONS = ["GK", "CB", "LB", "CM", "AM", "LW", "ST"]

# The simulator relies on the goalkeeper being the first slot of every formation.
for _name, _slots in FORMATIONS.items():
    assert len(_slots) == 11 and _slots[0].position == "GK", f"{_name}: GK must be slot 0 of 11"
    assert sum(s.position == "GK" for s in _slots) == 1, f"{_name}: exactly one GK"
