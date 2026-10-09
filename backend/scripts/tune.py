"""Calibration harness: try tuning constants without editing the simulator.

Runs a batch of matches in parallel with some of the constants at the top of
`pitchside/generator/sim.py` overridden, then prints the validation table.

    uv run python scripts/tune.py --seeds 1-20 SHOT_BIAS=-0.03 TACKLE_RATE=0.003
    uv run python scripts/tune.py --list          # show tunable constants and current values

When a combination looks right, copy the values into sim.py and run `make batch` to confirm.
"""

from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from pitchside.generator import generate_match
from pitchside.generator import sim as sim_module
from pitchside.generator.cli import MAX_BATCH, _parse_seeds
from pitchside.generator.teams import CLUBS
from pitchside.generator.validate import batch_report, format_batch, match_stats

# Constants that are part of the simulator's interface, not tuning knobs.
NOT_TUNABLE = {"FPS", "DT", "L", "W", "GENERATOR_VERSION"}


def tunable_constants() -> dict[str, float]:
    """Upper-case numeric module constants in sim.py."""
    return {
        name: value
        for name, value in vars(sim_module).items()
        if name.isupper()
        and name not in NOT_TUNABLE
        and isinstance(value, int | float)
        and not isinstance(value, bool)
    }


def parse_overrides(pairs: list[str]) -> dict[str, float]:
    known = tunable_constants()
    overrides = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep:
            raise SystemExit(f"Expected NAME=value, got '{pair}'")
        if name not in known:
            raise SystemExit(f"Unknown constant '{name}'. Run with --list to see the options.")
        try:
            overrides[name] = float(raw)
        except ValueError:
            raise SystemExit(f"Value for {name} must be a number, got '{raw}'") from None
    return overrides


def _apply(overrides: dict[str, float]) -> None:
    """Runs once in each worker process, before it simulates anything."""
    for name, value in overrides.items():
        setattr(sim_module, name, value)


def _run(seed: int, half_length: float) -> dict:
    rng = np.random.default_rng(seed)
    home, away = (str(c) for c in rng.choice(sorted(CLUBS), 2, replace=False))
    return match_stats(generate_match(home, away, seed, half_length=half_length))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("overrides", nargs="*", help="Constants to override, e.g. SHOT_BIAS=-0.03")
    parser.add_argument(
        "--seeds", default="1000-1019", help="Seeds to simulate (default: 20 matches)"
    )
    parser.add_argument("--half-length", type=float, default=45.0)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--list", action="store_true", help="List tunable constants and exit")
    args = parser.parse_args()

    if args.list:
        for name, value in sorted(tunable_constants().items()):
            print(f"{name:<28} {value}")
        return

    overrides = parse_overrides(args.overrides)
    seeds = _parse_seeds(args.seeds)
    if len(seeds) > MAX_BATCH:
        raise SystemExit(f"Too many seeds ({len(seeds)}); the limit is {MAX_BATCH}.")

    print("Overrides:", ", ".join(f"{k}={v}" for k, v in overrides.items()) or "none")
    t0 = time.perf_counter()
    with ProcessPoolExecutor(
        max_workers=max(1, min(args.workers, len(seeds))), initializer=_apply, initargs=(overrides,)
    ) as pool:
        stats = list(pool.map(_run, seeds, [args.half_length] * len(seeds)))
    print(format_batch(batch_report(stats)))
    print(f"({len(seeds)} matches in {time.perf_counter() - t0:.0f}s)")


if __name__ == "__main__":
    main()
