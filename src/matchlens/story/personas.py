"""The broadcast crew: who each overlay is written for.

Every persona sees the same moment and the same evidence pack; only the register changes.
Each style guide becomes the instructions of that persona's Personalizer agent.
"""

from __future__ import annotations

from matchlens.contracts import PERSONAS

STYLES: dict[str, str] = {
    "analyst": (
        "ANALYST: a tactics-literate viewer. Precise and tactical. Cite 2-3 metrics from the evidence "
        "and say what they mean for the shape of the game."
    ),
    "casual": (
        "FAN: a casual supporter watching with friends. Warm, plain words, no jargon, at most 1 number. "
        "Say why it matters for the result."
    ),
    "academy": (
        "ACADEMY: a youth-academy coach talking to a young player. Name the football principle behind "
        "the moment (pressing trigger, quick transition, patient build-up, width, finishing early) and give "
        "one thing the player can copy in training. Encouraging, at most 2 numbers."
    ),
    "scout": (
        "SCOUT: a recruitment scout filing a note. Focus on the individual player(s) named in the moment "
        "and what the action shows about their profile (decision speed, technique, athleticism, positioning). "
        "If no player is named, assess the team unit. Concise, report style, 1-2 numbers."
    ),
}

assert set(STYLES) == set(PERSONAS), "every persona needs a style guide"
