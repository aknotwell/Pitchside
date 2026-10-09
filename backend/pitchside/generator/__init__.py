"""Synthetic football match generator."""

from __future__ import annotations

from .scenarios import Scenario
from .sim import MatchSim
from .teams import build_team


def generate_match(
    home: str,
    away: str,
    seed: int,
    scenario: Scenario | None = None,
    half_length: float = 45.0,
) -> MatchSim:
    """Simulate a full match between two clubs and return the finished simulation."""
    return MatchSim(build_team(home), build_team(away), seed, scenario, half_length).run()


__all__ = ["MatchSim", "Scenario", "build_team", "generate_match"]
