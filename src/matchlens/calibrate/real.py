"""Calibration targets from SkillCorner open data (A-League 2024/25, MIT licence).

Raw files are downloaded to ``data/raw/skillcorner`` (gitignored) and never committed.
Only aggregate statistics derived here are written to the repo.

The real data is split three ways with a fixed, committed seed, so the simulator is never judged on
the matches or players it was tuned to:

* **train**: targets for the automatic fit;
* **validation**: for choosing between fitting rounds and for hand-tuning;
* **test**: read once, for the reported numbers.

Matches are split by match and players by player, about 60/20/20.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

BASE = "https://raw.githubusercontent.com/SkillCorner/opendata/master/data"
RAW = Path("data/raw/skillcorner")
SOURCE = "SkillCorner Open Data (github.com/SkillCorner/opendata), MIT licence"
SPLIT_SEED = 20261010
SPLITS = ("train", "validation", "test")
SHARES = (0.6, 0.2, 0.2)


def _get(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)
    return dest


def split(ids: Sequence, seed: int = SPLIT_SEED) -> dict[str, list]:
    """Deterministic train/validation/test split of distinct ids (order-independent). A player who
    moved clubs has a row per club, under one id, so all of them land in the same split."""
    ids = sorted(set(ids), key=str)
    random.Random(seed).shuffle(ids)
    a = round(len(ids) * SHARES[0])
    b = a + round(len(ids) * SHARES[1])
    return dict(zip(SPLITS, (ids[:a], ids[a:b], ids[b:]), strict=True))


def fetch() -> tuple[list[Path], Path, Path]:
    matches = json.loads(_get(f"{BASE}/matches.json", RAW / "matches.json").read_text())
    events = [_get(f"{BASE}/matches/{m['id']}/{m['id']}_dynamic_events.csv", RAW / f"{m['id']}_dynamic_events.csv")
              for m in sorted(matches, key=lambda m: m["id"])]
    phys = _get(f"{BASE}/aggregates/aus1league_physicalaggregates_20242025.csv", RAW / "physical.csv")
    passing = _get(f"{BASE}/aggregates/aus1league_passingaggregates_20242025.csv", RAW / "passing.csv")
    return events, phys, passing


def _f(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def match_row(path: Path) -> dict:
    """Per-match counts from one dynamic-events file."""
    rows = [r for r in csv.DictReader(open(path, newline="", encoding="utf-8")) if r["event_type"] == "player_possession"]
    passes = [r for r in rows if r["end_type"] == "pass"]
    ok = [r for r in passes if r["pass_outcome"] == "successful"]
    team_poss = sum(1 for r in rows if r.get("first_player_possession_in_team_possession", "").lower() == "true")
    return {
        "passes": len(passes),
        "pass_completion": len(ok) / len(passes) if passes else 0.0,
        "shots": sum(1 for r in rows if r["end_type"] == "shot"),
        "possessions": len(rows),
        "team_possessions": team_poss,
        "pass_lengths": [d for d in (_f(r["pass_distance"]) for r in passes) if d is not None],
    }


def event_stats(per_match: list[dict]) -> dict:
    mean = lambda k: statistics.mean(m[k] for m in per_match)  # noqa: E731
    lengths = [x for m in per_match for x in m["pass_lengths"]]
    team_poss = sum(m["team_possessions"] for m in per_match)
    return {
        "matches": len(per_match),
        "passes_per_match": mean("passes"),
        "pass_completion": mean("pass_completion"),
        "shots_per_match": mean("shots"),
        "player_possessions_per_match": mean("possessions"),
        "team_possessions_per_match": mean("team_possessions"),
        "passes_per_team_possession": sum(m["passes"] for m in per_match) / team_poss if team_poss else 0.0,
        "pass_length_mean_m": statistics.mean(lengths),
        "pass_length_quantiles_m": statistics.quantiles(lengths, n=10),
        "_pass_lengths": lengths,
        "_per_match": per_match,
    }


def event_targets(paths: list[Path]) -> dict:
    return event_stats([match_row(p) for p in paths])


def player_rows(path: Path, min_matches: int = 8) -> list[dict]:
    """Season aggregates are per-match averages; keep outfield players with enough matches."""
    out = []
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        mins = _f(r.get("minutes_full_all"))
        if (not mins or (_f(r.get("count_match")) or 0) < min_matches
                or "goalkeeper" in (r.get("position_group") or "").lower()):
            continue
        mpm, sc = _f(r.get("total_metersperminute_full_all")), _f(r.get("sprint_count_full_all"))
        out.append({"player_id": r.get("player_id"), "dist90": mpm * 90 / 1000 if mpm else None,
                    "psv99": _f(r.get("psv99")) or None, "sprints90": sc / mins * 90 if sc is not None else None})
    return out


def physical_stats(players: list[dict]) -> dict:
    col = lambda k: [p[k] for p in players if p[k] is not None]  # noqa: E731
    return {
        "players": len(col("dist90")),
        "distance_km_per90_median": statistics.median(col("dist90")),
        "psv99_kmh_median": statistics.median(col("psv99")),
        "psv99_kmh_quantiles": statistics.quantiles(col("psv99"), n=10),
        "sprints_per90_median": statistics.median(col("sprints90")),
        "_psv99": col("psv99"),
        "_dist90": col("dist90"),
        "_players": players,
    }


def physical_targets(path: Path, min_matches: int = 8) -> dict:
    return physical_stats(player_rows(path, min_matches))


def bootstrap_ci(units: list, stat: Callable[[list], float], n: int = 2000, seed: int = 0,
                 level: float = 0.95) -> tuple[float, float]:
    """Percentile bootstrap: resample the units (matches or players) with replacement."""
    rng = random.Random(seed)
    vals = sorted(stat([rng.choice(units) for _ in units]) for _ in range(n))
    lo = vals[int((1 - level) / 2 * n)]
    hi = vals[min(n - 1, int((1 + level) / 2 * n))]
    return lo, hi


def event_ci(per_match: list[dict], key: str, **kw) -> tuple[float, float]:
    return bootstrap_ci(per_match, lambda ms: event_stats(ms)[key], **kw)


def physical_ci(players: list[dict], key: str, **kw) -> tuple[float, float]:
    return bootstrap_ci(players, lambda ps: physical_stats(ps)[key], **kw)


def passing_targets(path: Path, min_matches: int = 8) -> dict:
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    dist = [d for r in rows if (_f(r.get("performance_included_count")) or 0) >= min_matches
            and (d := _f(r.get("pass_avgdistance")))]
    return {"pass_avgdistance_m_median": statistics.median(dist)}


def targets(part: str | None = None) -> dict:
    """Targets from all the real data, or from one split (``train``, ``validation`` or ``test``)."""
    events, phys, passing = fetch()
    players = player_rows(phys)
    if part is not None:
        keep = set(split([p.name.split("_")[0] for p in events])[part])
        events = [p for p in events if p.name.split("_")[0] in keep]
        ids = set(split([p["player_id"] for p in players])[part])
        players = [p for p in players if p["player_id"] in ids]
    return {"source": SOURCE, "split": part or "all", "events": event_targets(events),
            "physical": physical_stats(players), "passing": passing_targets(passing)}


if __name__ == "__main__":
    import sys

    t = targets(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} if isinstance(v, dict) else v
                      for k, v in t.items()}, indent=1))
