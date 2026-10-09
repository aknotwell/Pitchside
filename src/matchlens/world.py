"""A fictional league: clubs, kits, squads and tactics generated from a seed.

Nothing here refers to a real club, player or competition. Every player gets a
role (poacher, deep-lying playmaker, overlapping full-back...) and 1-99 trait
ratings built from a positional profile, the role, team quality and individual
variation; every team gets a tactical style. The match engine reads both.
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
    "Ferran", "Gael", "Idris", "Joaquin", "Kenji", "Lars", "Milo", "Nuno", "Oskar", "Remy",
    "Sami", "Teo", "Viktor", "Yusuf", "Anders", "Bruno", "Dmitri", "Enzo", "Hakan", "Jae",
]
LAST = [
    "Albrecht", "Brandt", "Castell", "Duarte", "Eklund", "Ferro", "Galloway", "Haldane",
    "Ivers", "Jarvik", "Kessler", "Lindqvist", "Marlow", "Norberg", "Okafor", "Pell",
    "Quarry", "Rourke", "Stavros", "Tennant", "Ulloa", "Varga", "Whitlock", "Yardley",
    "Zelenko", "Ashby", "Bellamy", "Corwin", "Draycott", "Ellery", "Fenwick", "Grieve",
    "Holm", "Ibarra", "Kowal", "Lusk", "Moreau", "Nakamura", "Orsini", "Petrov",
    "Rasmussen", "Sorensen", "Tavares", "Underhill", "Vance", "Wexler", "Achterberg", "Brisco",
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
    "4-2-3-1": [
        ("GK", -48.0, 0.0),
        ("RB", -30.0, -24.0), ("CB", -35.0, -8.0), ("CB", -35.0, 8.0), ("LB", -30.0, 24.0),
        ("DM", -20.0, -7.0), ("DM", -20.0, 7.0),
        ("RW", 4.0, -22.0), ("CM", 0.0, 0.0), ("LW", 4.0, 22.0),
        ("ST", 13.0, 0.0),
    ],
    "3-5-2": [
        ("GK", -48.0, 0.0),
        ("CB", -34.0, -14.0), ("CB", -37.0, 0.0), ("CB", -34.0, 14.0),
        ("RM", -12.0, -28.0), ("CM", -16.0, -9.0), ("DM", -22.0, 0.0), ("CM", -16.0, 9.0), ("LM", -12.0, 28.0),
        ("ST", 10.0, -7.0), ("ST", 10.0, 7.0),
    ],
}
BENCH_POSITIONS = ["GK", "CB", "CM", "RW", "ST", "LB", "DM"]

TRAITS = (
    "pace", "acceleration", "stamina", "passing", "vision", "crossing", "dribbling", "first_touch",
    "finishing", "long_shots", "heading", "tackling", "marking", "positioning", "aggression",
    "composure", "work_rate", "flair", "reflexes",
)

# Positional profiles: one value per trait, in TRAITS order.
PROFILES: dict[str, tuple[int, ...]] = {
    #      pac acc sta pas vis cro dri fto fin lsh hea tac mar pos agg com wrk fla ref
    "GK": (40, 45, 50, 50, 45, 20, 25, 50, 12, 12, 30, 20, 30, 66, 40, 60, 40, 20, 70),
    "CB": (55, 52, 66, 55, 45, 35, 42, 55, 30, 30, 75, 75, 72, 70, 62, 60, 60, 25, 10),
    "FB": (72, 70, 75, 62, 52, 66, 60, 60, 35, 40, 55, 66, 64, 62, 55, 58, 72, 40, 10),
    "DM": (58, 58, 74, 70, 64, 50, 58, 68, 42, 55, 60, 70, 66, 70, 60, 68, 72, 35, 10),
    "CM": (63, 64, 76, 72, 68, 58, 66, 70, 52, 60, 52, 58, 55, 60, 52, 66, 70, 50, 10),
    "W": (78, 78, 68, 64, 62, 68, 74, 70, 62, 58, 42, 38, 36, 50, 42, 60, 62, 70, 10),
    "ST": (72, 72, 64, 58, 56, 45, 66, 70, 74, 60, 68, 32, 30, 45, 55, 66, 58, 58, 10),
}
GROUP = {"GK": "GK", "CB": "CB", "RB": "FB", "LB": "FB", "DM": "DM", "CM": "CM",
         "RW": "W", "LW": "W", "RM": "W", "LM": "W", "ST": "ST"}

# Roles: name -> trait adjustments. Behaviour in the engine keys off the role name.
ROLES: dict[str, list[tuple[str, dict[str, int]]]] = {
    "GK": [("shot-stopper", {"reflexes": 12, "passing": -5}),
           ("sweeper keeper", {"passing": 15, "vision": 10, "pace": 10, "composure": 5})],
    "CB": [("stopper", {"aggression": 12, "tackling": 6, "heading": 5, "passing": -6}),
           ("ball-playing defender", {"passing": 14, "vision": 12, "composure": 8, "aggression": -6}),
           ("covering defender", {"pace": 10, "positioning": 8, "marking": 5})],
    "FB": [("overlapping full-back", {"stamina": 10, "crossing": 10, "pace": 6, "work_rate": 8, "marking": -6}),
           ("defensive full-back", {"tackling": 8, "marking": 10, "crossing": -8, "positioning": 6}),
           ("inverted full-back", {"passing": 10, "vision": 8, "dribbling": 4, "crossing": -6})],
    "DM": [("anchor", {"positioning": 10, "tackling": 8, "flair": -10, "dribbling": -6}),
           ("deep-lying playmaker", {"passing": 14, "vision": 16, "tackling": -8}),
           ("ball-winner", {"aggression": 16, "tackling": 10, "work_rate": 8, "passing": -6})],
    "CM": [("box-to-box", {"stamina": 14, "work_rate": 12, "finishing": 6}),
           ("playmaker", {"vision": 16, "passing": 12, "flair": 8, "tackling": -8}),
           ("ball-winner", {"aggression": 14, "tackling": 12, "flair": -8})],
    "W": [("inverted winger", {"finishing": 10, "long_shots": 10, "dribbling": 6, "crossing": -8}),
          ("touchline winger", {"crossing": 14, "pace": 8, "finishing": -6}),
          ("trickster", {"flair": 18, "dribbling": 12, "composure": -6, "work_rate": -8})],
    "ST": [("poacher", {"finishing": 12, "pace": 6, "passing": -10, "work_rate": -6}),
           ("target forward", {"heading": 16, "first_touch": 6, "pace": -12, "passing": 4}),
           ("false nine", {"passing": 14, "vision": 14, "dribbling": 6, "finishing": -6}),
           ("pressing forward", {"work_rate": 16, "aggression": 10, "stamina": 10})],
}

# Team styles: (name, press, line, width, tempo, directness). Each is jittered per match.
STYLES: list[tuple[str, float, float, float, float, float]] = [
    ("high press", 0.80, 0.75, 0.60, 1.10, 0.40),
    ("possession", 0.60, 0.65, 0.75, 0.90, 0.15),
    ("counter-attacking", 0.30, 0.30, 0.50, 1.15, 0.75),
    ("direct", 0.45, 0.45, 0.60, 1.05, 0.85),
    ("balanced", 0.50, 0.50, 0.55, 1.00, 0.45),
    ("low block", 0.20, 0.15, 0.45, 0.95, 0.60),
]


def _traits(rng: random.Random, position: str, role_adj: dict[str, int], quality: float) -> dict[str, int]:
    base = PROFILES[GROUP[position]]
    out = {}
    for name, v in zip(TRAITS, base, strict=True):
        x = v + role_adj.get(name, 0) + quality + rng.gauss(0, 7)
        lo = 5 if (GROUP[position] == "GK") != (name == "reflexes") else 25
        out[name] = int(max(lo, min(97, round(x))))
    return out


def make_team(rng: random.Random, team_id: str, club: tuple[str, str, str], formation: str) -> Team:
    name, short, color = club
    used: set[str] = set()
    players: list[Player] = []
    positions = [p for p, _, _ in FORMATIONS[formation]] + BENCH_POSITIONS
    team_quality = rng.gauss(0, 3)
    for i, pos in enumerate(positions):
        while True:
            full = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if full not in used:
                used.add(full)
                break
        role, adj = rng.choice(ROLES[GROUP[pos]])
        traits = _traits(rng, pos, adj, team_quality + rng.gauss(0, 4))
        foot = "L" if rng.random() < 0.25 else "R"
        if pos in ("LB", "LM", "LW") and role != "inverted winger" and rng.random() < 0.6:
            foot = "L"
        if role == "inverted winger":
            foot = "R" if pos in ("LW", "LM") else "L"
        number = i + 1
        players.append(Player(id=f"{team_id}{i + 1}", name=full, number=number, position=pos, team_id=team_id,
                              role=role, foot=foot, traits=traits))
    style_name, *vals = rng.choice(STYLES)
    keys = ("press", "line", "width", "tempo", "directness")
    style = {k: round(max(0.05, min(1.3 if k == "tempo" else 0.95, v + rng.gauss(0, 0.07))), 3)
             for k, v in zip(keys, vals, strict=True)}
    return Team(id=team_id, name=name, short=short, color=color, formation=formation, players=players,
                style_name=style_name, style=style)


def make_fixture(seed: int) -> tuple[Team, Team]:
    rng = random.Random(seed)
    home_club, away_club = rng.sample(CLUBS, 2)
    home = make_team(rng, "h", home_club, rng.choice(list(FORMATIONS)))
    away = make_team(rng, "a", away_club, rng.choice(list(FORMATIONS)))
    return home, away
