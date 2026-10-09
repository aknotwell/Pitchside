"""Fictional clubs and deterministic squad generation.

Squads are seeded from the club id, so a club has the same players in every match.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

from pitchside.schemas import Player, Ratings, Tactics, Team

from .formations import BENCH_POSITIONS, FORMATIONS


@dataclass(frozen=True)
class ClubSpec:
    id: str
    name: str
    short_name: str
    colors: tuple[str, str]
    formation: str
    strength: int
    tactics: Tactics


CLUBS: dict[str, ClubSpec] = {
    c.id: c
    for c in [
        ClubSpec(
            "ashenford",
            "Ashenford Athletic",
            "ASH",
            ("#B3202A", "#FFFFFF"),
            "4-3-3",
            80,
            Tactics(press_height=0.75, tempo=0.7, width=0.6, directness=0.35),
        ),
        ClubSpec(
            "greywater",
            "Greywater Rovers",
            "GRY",
            ("#1F4E8C", "#F2C230"),
            "4-2-3-1",
            77,
            Tactics(press_height=0.45, tempo=0.5, width=0.5, directness=0.5),
        ),
        ClubSpec(
            "kingsmere",
            "Kingsmere City",
            "KGM",
            ("#6CABDD", "#0B1F3A"),
            "4-3-3",
            83,
            Tactics(press_height=0.7, tempo=0.6, width=0.7, directness=0.3),
        ),
        ClubSpec(
            "lowmarsh",
            "Lowmarsh Albion",
            "LOW",
            ("#2E7D32", "#FFFFFF"),
            "3-5-2",
            72,
            Tactics(press_height=0.3, tempo=0.4, width=0.45, directness=0.7),
        ),
        ClubSpec(
            "port-alder",
            "Port Alder FC",
            "PAL",
            ("#111111", "#E0E0E0"),
            "4-2-3-1",
            75,
            Tactics(press_height=0.55, tempo=0.55, width=0.55, directness=0.45),
        ),
        ClubSpec(
            "ravenholm",
            "Ravenholm Town",
            "RAV",
            ("#5B2A86", "#F5F5F5"),
            "4-4-2",
            70,
            Tactics(press_height=0.35, tempo=0.45, width=0.6, directness=0.65),
        ),
        ClubSpec(
            "calderbrook",
            "Calderbrook United",
            "CAL",
            ("#E65100", "#1A1A1A"),
            "4-3-3",
            74,
            Tactics(press_height=0.6, tempo=0.65, width=0.5, directness=0.5),
        ),
        ClubSpec(
            "sableport",
            "Sableport Wanderers",
            "SAB",
            ("#00897B", "#FFFFFF"),
            "4-4-2",
            71,
            Tactics(press_height=0.4, tempo=0.5, width=0.4, directness=0.6),
        ),
    ]
}

FIRST_NAMES = [
    "Aaron",
    "Adel",
    "Alex",
    "Amadou",
    "Andrés",
    "Bastian",
    "Ben",
    "Callum",
    "Cristian",
    "Dami",
    "Daniel",
    "Dario",
    "Eli",
    "Emre",
    "Enzo",
    "Felix",
    "Finn",
    "Gabriel",
    "Hugo",
    "Ibrahim",
    "Isaac",
    "Jamal",
    "Jonas",
    "Jordan",
    "Kai",
    "Kenji",
    "Kofi",
    "Lars",
    "Leon",
    "Lucas",
    "Luka",
    "Marco",
    "Mateo",
    "Max",
    "Milan",
    "Moussa",
    "Nico",
    "Noah",
    "Oliver",
    "Omar",
    "Oscar",
    "Pablo",
    "Rafael",
    "Rhys",
    "Ruben",
    "Sami",
    "Santiago",
    "Sebastian",
    "Theo",
    "Tobias",
    "Tomás",
    "Victor",
    "Yannick",
    "Youssef",
    "Zane",
]

LAST_NAMES = [
    "Abara",
    "Alderton",
    "Almeida",
    "Asante",
    "Bakker",
    "Barlow",
    "Bellamy",
    "Brandt",
    "Castell",
    "Cordero",
    "Dalton",
    "Delacroix",
    "Diallo",
    "Eastwood",
    "Edevane",
    "Falk",
    "Ferraz",
    "Fontaine",
    "Garrow",
    "Halvorsen",
    "Hartigan",
    "Ivanov",
    "Jarrell",
    "Kessler",
    "Kovač",
    "Lindqvist",
    "Lucero",
    "Mabaso",
    "Marsh",
    "Moreau",
    "Nakamura",
    "Nyberg",
    "Okafor",
    "Oduya",
    "Palmeiro",
    "Quinlan",
    "Ravel",
    "Renner",
    "Rosales",
    "Saarinen",
    "Sandoval",
    "Sorensen",
    "Strand",
    "Takeda",
    "Thornell",
    "Ulloa",
    "Vance",
    "Varga",
    "Vidal",
    "Wexford",
    "Whitlock",
    "Yilmaz",
    "Zeller",
    "Zubiri",
]

# Rating offsets from club strength, per position.
_POSITION_PROFILE: dict[str, dict[str, int]] = {
    "GK": dict(
        pace=-20,
        passing=-12,
        dribbling=-30,
        finishing=-50,
        defending=-30,
        decisions=0,
        stamina=-10,
        goalkeeping=5,
    ),
    "CB": dict(
        pace=-6,
        passing=-6,
        dribbling=-15,
        finishing=-30,
        defending=6,
        decisions=0,
        stamina=0,
        goalkeeping=-60,
    ),
    "LB": dict(
        pace=4,
        passing=-3,
        dribbling=-5,
        finishing=-25,
        defending=0,
        decisions=-2,
        stamina=6,
        goalkeeping=-60,
    ),
    "RB": dict(
        pace=4,
        passing=-3,
        dribbling=-5,
        finishing=-25,
        defending=0,
        decisions=-2,
        stamina=6,
        goalkeeping=-60,
    ),
    "DM": dict(
        pace=-6,
        passing=2,
        dribbling=-6,
        finishing=-18,
        defending=3,
        decisions=3,
        stamina=4,
        goalkeeping=-60,
    ),
    "CM": dict(
        pace=-3,
        passing=4,
        dribbling=0,
        finishing=-10,
        defending=-5,
        decisions=2,
        stamina=5,
        goalkeeping=-60,
    ),
    "AM": dict(
        pace=0,
        passing=5,
        dribbling=5,
        finishing=0,
        defending=-20,
        decisions=3,
        stamina=0,
        goalkeeping=-60,
    ),
    "LW": dict(
        pace=8,
        passing=-2,
        dribbling=6,
        finishing=-3,
        defending=-20,
        decisions=-2,
        stamina=2,
        goalkeeping=-60,
    ),
    "RW": dict(
        pace=8,
        passing=-2,
        dribbling=6,
        finishing=-3,
        defending=-20,
        decisions=-2,
        stamina=2,
        goalkeeping=-60,
    ),
    "ST": dict(
        pace=3,
        passing=-6,
        dribbling=0,
        finishing=7,
        defending=-25,
        decisions=0,
        stamina=0,
        goalkeeping=-60,
    ),
}


def _rng_for(club_id: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(club_id.encode()))


def build_team(club_id: str) -> Team:
    if club_id not in CLUBS:
        raise KeyError(f"Unknown club '{club_id}'. Choose from: {', '.join(sorted(CLUBS))}")
    spec = CLUBS[club_id]
    rng = _rng_for(club_id)
    positions = [slot.position for slot in FORMATIONS[spec.formation]] + BENCH_POSITIONS

    used_names: set[str] = set()
    players: list[Player] = []
    numbers = iter([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19])
    # Shirt numbers follow squad order, with the goalkeepers getting 1 and 13.
    gk_numbers = iter([1, 13])
    outfield_numbers = (n for n in numbers if n not in (1, 13))

    for position in positions:
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        number = next(gk_numbers) if position == "GK" else next(outfield_numbers)
        base = spec.strength + rng.normal(0, 3)
        profile = _POSITION_PROFILE[position]
        ratings = {
            k: int(np.clip(round(base + off + rng.normal(0, 4)), 15 if off < -40 else 30, 99))
            for k, off in profile.items()
        }
        players.append(
            Player(
                id=f"{spec.short_name.lower()}-{number:02d}",
                name=name,
                number=number,
                position=position,
                ratings=Ratings(**ratings),
            )
        )

    return Team(
        id=spec.id,
        name=spec.name,
        short_name=spec.short_name,
        colors=spec.colors,
        formation=spec.formation,
        tactics=spec.tactics,
        starting_xi=[p.id for p in players[:11]],
        bench=[p.id for p in players[11:]],
        players=players,
    )
