"""Command-line entry point: `uv run pitchside-gen --help`."""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import generate_match
from .output import write_match
from .scenarios import SCENARIOS, ScenarioError, load_scenario
from .teams import CLUBS
from .validate import batch_report, format_batch, match_stats, single_match_report

MAX_BATCH = 1000


def _parse_seeds(spec: str) -> list[int]:
    seeds: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            seeds.extend(range(int(a), int(b) + 1))
        else:
            seeds.append(int(part))
    return seeds


def cmd_match(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    sim = generate_match(
        args.home, args.away, args.seed, load_scenario(args.scenario), args.half_length
    )
    out = write_match(sim, Path(args.out), tracking=not args.no_tracking)
    report = single_match_report(sim)
    (out / "validation.json").write_text(json.dumps(report, indent=2))
    home, away = sim.teams
    print(
        f"{home.name} {sim.score[0]}-{sim.score[1]} {away.name}  "
        f"({len(sim.events)} events, {len(sim._rec_t)} frames, {time.perf_counter() - t0:.1f}s)"
    )
    summary = report["summary"]
    print(f"Validation: {summary['passed']}/{summary['total']} metrics in plausible range")
    for key, m in report["metrics"].items():
        if not m["ok"]:
            print(f"  out of range: {key} = {m['value']} (expected {m['plausible_range']})")
    print(f"Wrote {out}")


def _batch_match(seed: int, home: str, away: str, half_length: float, out: str | None):
    """Run one batch match (top-level so worker processes can pickle it)."""
    sim = generate_match(home, away, seed, half_length=half_length)
    if out is not None:
        write_match(sim, Path(out), tracking=False)
    return seed, home, away, sim.score, match_stats(sim)


def cmd_batch(args: argparse.Namespace) -> None:
    seeds = _parse_seeds(args.seeds)
    if len(seeds) > MAX_BATCH:
        raise SystemExit(f"Too many seeds ({len(seeds)}); the limit is {MAX_BATCH}.")
    clubs = sorted(CLUBS)
    jobs = []
    for seed in seeds:
        if args.home and args.away:
            home, away = args.home, args.away
        else:
            rng = np.random.default_rng(seed)
            home, away = (str(c) for c in rng.choice(clubs, 2, replace=False))
        jobs.append((seed, home, away, args.half_length, args.out if args.write_matches else None))

    t0 = time.perf_counter()
    workers = max(1, min(args.workers, len(jobs)))
    all_stats = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        # map() keeps seed order, so the report is the same however many workers run.
        for seed, home, away, score, stats in pool.map(_batch_match, *zip(*jobs, strict=True)):
            all_stats.append(stats)
            print(f"  seed {seed:>4}: {home} {score[0]}-{score[1]} {away}")
    report = batch_report(all_stats)
    print(format_batch(report))
    print(f"({len(seeds)} matches in {time.perf_counter() - t0:.0f}s on {workers} workers)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "batch_validation.json").write_text(json.dumps(report, indent=2))


def _half_length(value: str) -> float:
    minutes = float(value)
    if not 1 <= minutes <= 60:
        raise argparse.ArgumentTypeError("half length must be between 1 and 60 minutes")
    return minutes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pitchside-gen", description="Synthetic football match generator"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("match", help="Generate one match")
    pm.add_argument("--seed", type=int, default=42)
    pm.add_argument("--home", default="ashenford", choices=sorted(CLUBS))
    pm.add_argument("--away", default="greywater", choices=sorted(CLUBS))
    pm.add_argument(
        "--scenario", default="open", help=f"Built-in ({', '.join(SCENARIOS)}) or a JSON path"
    )
    pm.add_argument("--half-length", type=_half_length, default=45.0, help="Minutes per half")
    pm.add_argument("--out", default="../data/matches")
    pm.add_argument("--no-tracking", action="store_true", help="Skip writing tracking data")
    pm.set_defaults(func=cmd_match)

    pb = sub.add_parser("batch", help="Generate many matches and compare averages with targets")
    pb.add_argument("--seeds", default="1-20", help="e.g. 1-20 or 1,5,9")
    pb.add_argument("--home", choices=sorted(CLUBS))
    pb.add_argument("--away", choices=sorted(CLUBS))
    pb.add_argument("--half-length", type=_half_length, default=45.0)
    pb.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 1,
        help="Matches simulated in parallel (default: all CPU cores)",
    )
    pb.add_argument("--out", default="../data/matches/batch")
    pb.add_argument(
        "--write-matches", action="store_true", help="Also write events + meta per match"
    )
    pb.set_defaults(func=cmd_batch)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ScenarioError as exc:
        raise SystemExit(f"Invalid scenario: {exc}") from None


if __name__ == "__main__":
    main()
