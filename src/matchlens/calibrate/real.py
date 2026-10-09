"""Calibration targets from SkillCorner open data (A-League 2024/25, MIT licence).

Raw files are downloaded to ``data/raw/skillcorner`` (gitignored) and never committed.
Only aggregate statistics derived here are written to the repo.
"""

from __future__ import annotations

import csv
import json
import statistics
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/SkillCorner/opendata/master/data"
RAW = Path("data/raw/skillcorner")
SOURCE = "SkillCorner Open Data (github.com/SkillCorner/opendata), MIT licence"


def _get(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)
    return dest


def fetch() -> tuple[list[Path], Path, Path]:
    matches = json.loads(_get(f"{BASE}/matches.json", RAW / "matches.json").read_text())
    events = [_get(f"{BASE}/matches/{m['id']}/{m['id']}_dynamic_events.csv", RAW / f"{m['id']}_dynamic_events.csv")
              for m in matches]
    phys = _get(f"{BASE}/aggregates/aus1league_physicalaggregates_20242025.csv", RAW / "physical.csv")
    passing = _get(f"{BASE}/aggregates/aus1league_passingaggregates_20242025.csv", RAW / "passing.csv")
    return events, phys, passing


def _f(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def event_targets(paths: list[Path]) -> dict:
    per_match = []
    pass_lengths: list[float] = []
    for p in paths:
        rows = [r for r in csv.DictReader(open(p, newline="", encoding="utf-8")) if r["event_type"] == "player_possession"]
        passes = [r for r in rows if r["end_type"] == "pass"]
        ok = [r for r in passes if r["pass_outcome"] == "successful"]
        lengths = [d for d in (_f(r["pass_distance"]) for r in passes) if d is not None]
        pass_lengths += lengths
        per_match.append({
            "passes": len(passes),
            "pass_completion": len(ok) / len(passes) if passes else 0.0,
            "shots": sum(1 for r in rows if r["end_type"] == "shot"),
            "possessions": len(rows),
        })
    mean = lambda k: statistics.mean(m[k] for m in per_match)  # noqa: E731
    return {
        "matches": len(per_match),
        "passes_per_match": mean("passes"),
        "pass_completion": mean("pass_completion"),
        "shots_per_match": mean("shots"),
        "player_possessions_per_match": mean("possessions"),
        "pass_length_mean_m": statistics.mean(pass_lengths),
        "pass_length_quantiles_m": statistics.quantiles(pass_lengths, n=10),
        "_pass_lengths": pass_lengths,
    }


def physical_targets(path: Path, min_matches: int = 8) -> dict:
    """Season aggregates are per-match averages; keep outfield players with enough matches."""
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    dist90, psv, sprints90 = [], [], []
    for r in rows:
        mins = _f(r.get("minutes_full_all"))
        if (not mins or (_f(r.get("count_match")) or 0) < min_matches
                or "goalkeeper" in (r.get("position_group") or "").lower()):
            continue
        mpm = _f(r.get("total_metersperminute_full_all"))
        if mpm:
            dist90.append(mpm * 90 / 1000)
        v = _f(r.get("psv99"))
        if v:
            psv.append(v)
        sc = _f(r.get("sprint_count_full_all"))
        if sc is not None:
            sprints90.append(sc / mins * 90)
    return {
        "players": len(dist90),
        "distance_km_per90_median": statistics.median(dist90),
        "psv99_kmh_median": statistics.median(psv),
        "psv99_kmh_quantiles": statistics.quantiles(psv, n=10),
        "sprints_per90_median": statistics.median(sprints90),
        "_psv99": psv,
        "_dist90": dist90,
    }


def passing_targets(path: Path, min_matches: int = 8) -> dict:
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    dist = [d for r in rows if (_f(r.get("performance_included_count")) or 0) >= min_matches
            and (d := _f(r.get("pass_avgdistance")))]
    return {"pass_avgdistance_m_median": statistics.median(dist)}


def targets() -> dict:
    events, phys, passing = fetch()
    return {"source": SOURCE, "events": event_targets(events), "physical": physical_targets(phys),
            "passing": passing_targets(passing)}


if __name__ == "__main__":
    t = targets()
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} if isinstance(v, dict) else v
                      for k, v in t.items()}, indent=1))
