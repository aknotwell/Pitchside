"""Container entrypoint: one image, MATCHLENS_SERVICE picks the role."""

from __future__ import annotations

import importlib
import os
import sys

SERVICES = {"api", "interpreter", "storyteller", "simulator", "replay"}


def main() -> None:
    name = os.environ.get("MATCHLENS_SERVICE") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if name not in SERVICES:
        sys.exit(f"MATCHLENS_SERVICE must be one of {sorted(SERVICES)}, got {name!r}")
    importlib.import_module(f"matchlens.services.{name}").main()


if __name__ == "__main__":
    main()
