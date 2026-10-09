"""A fictional league: clubs, kits and squads generated from a seed.

Nothing here refers to a real club, player or competition.
"""

from __future__ import annotations

import random

from matchlens.contracts import Player, Team

CLUBS: list[tuple[str, str, str]] = [
    ("Harbour City", "HAR", "#1f6feb"),
    ("Ironvale Athletic", "IRV", "#d1242f"),
    ("Kestrel Town", "KES", "#2da44e"),
    ("Saltmarsh Rovers", "SAL", "#bf8700"),
    ("Northgate United", "NOR", "#8250df"),
    ("Copperfield", "COP", "#e16f24"),
    ("Westmere Wanderers", "WES", "#0a7ea4"),
    ("Brackenridge", "BRA", "#57606a"),
]

FIRST = [
    "Aldo", "Bram", "Caio", "Dario", "Emre", "Femi", "Goran", "Hugo", "Ilan", "Jonas",
    "Kofi", "Luca", "Mateo", "Nils", "Otto", "Pavel", "Quinn", "Rafa", "Soren", "Tomas",
    "Umar", "Vito", "Wes", "Yanis", "Zeki", "Arlo", "Bastian", "Cyril", "Dane", "Elio",
]
LAST = [
    "Albrecht", "Brandt", "Castell", "Duarte", "Eklund", "Ferro", "Galloway", "Haldane",
    "Ivers", "Jarvik", "Kessler", "Lindqvist", "Marlow", "Norberg", "Okafor", "Pell",
    "Quarry", "Rourke", "Stavros", "Tennant", "Ulloa", "Varga", "Whitlock", "Yardley",
    "Zelenko", "Ashby", "Bellamy", "Corwin", "Draycott", "Ellery",
]

# Formation anchors in the team-attacking frame (attacking towards +x), metres.
FORMATIONS: dict[str, list[tuple[str, float, float]]] = {
    "4-3-3": [
        ("GK", -48.0, 0.0),
        ("RB", -30.0, -24.0), ("CB", -35.0, -8.0), ("CB", -35.0, 8.0), ("LB", -30.0, 24.0),
        ("CM", -14.0, -12.0), ("DM", -20.0, 0.0), ("CM", -14.0, 12.0),
        ("RW", 8.0, -24.0), ("ST", 12.0, 0.0), ("LW", 8.0, 24.0),
    ],
    "4-4-2": [
        ("GK", -48.0, 0.0),
        ("RB", -30.0, -24.0), ("CB", -35.0, -8.0), ("CB", -35.0, 8.0), ("LB", -30.0, 24.0),
        ("RM", -10.0, -24.0), ("CM", -16.0, -7.0), ("CM", -16.0, 7.0), ("LM", -10.0, 24.0),
        ("ST", 10.0, -7.0), ("ST", 10.0, 7.0),
    ],
}
BENCH_POSITIONS = ["CB", "CM", "RW", "ST", "LB"]


def make_team(rng: random.Random, team_id: str, club: tuple[str, str, str], formation: str) -> Team:
    name, short, color = club
    used: set[str] = set()
    players: list[Player] = []
    positions = [p for p, _, _ in FORMATIONS[formation]] + BENCH_POSITIONS
    for i, pos in enumerate(positions):
        while True:
            full = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if full not in used:
                used.add(full)
                break
        number = i + 1 if i < 11 else 12 + (i - 11)
        players.append(Player(id=f"{team_id}{i + 1}", name=full, number=number, position=pos, team_id=team_id))
    return Team(id=team_id, name=name, short=short, color=color, formation=formation, players=players)


def make_fixture(seed: int) -> tuple[Team, Team]:
    rng = random.Random(seed)
    home_club, away_club = rng.sample(CLUBS, 2)
    home = make_team(rng, "h", home_club, rng.choice(list(FORMATIONS)))
    away = make_team(rng, "a", away_club, rng.choice(list(FORMATIONS)))
    return home, away
