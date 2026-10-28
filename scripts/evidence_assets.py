"""Evidence for the web player: the realism tables, the newest load test and the newest eval, as one JSON file.

Reads docs/REALISM.md and docs/evidence/{loadtest,eval}-*.json; writes web/public/evidence.json.

    uv run python scripts/evidence_assets.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from itertools import takewhile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REALISM = Path("docs/REALISM.md")
OUT = Path("web/public/evidence.json")


def section(md: str, heading: str) -> str:
    start = md.index(f"\n## {heading}")
    end = md.find("\n## ", start + 1)
    return md[start:end if end != -1 else len(md)]


def first_table(text: str) -> list[dict[str, str]]:
    """The first markdown table in ``text``, one dict per row keyed by header, with bold stripped."""
    lines = takewhile(lambda ln: ln.startswith("|"), text[text.index("\n|") + 1:].splitlines())
    header, _, *rows = [[c.strip().replace("**", "") for c in ln.strip("|").split("|")] for ln in lines]
    return [dict(zip(header, r, strict=True)) for r in rows]


def realism(md: str) -> dict:
    return {
        "heldout": {"matches": int(re.search(r"\*\*(\d+) held-out matches\*\*", md).group(1)),
                    "rows": [{"metric": r["Metric"], "real": r["Real"], "synthetic": r["Calibrated (after)"],
                              "error": r["Error after"]} for r in first_table(section(md, "Against SkillCorner"))]},
        "test": {"rows": [{"metric": r["Metric"], "real": r["Real (test split)"], "interval": r["95% interval"],
                           "synthetic": r["Simulated"], "inside": r["Inside?"] == "yes"}
                          for r in first_table(section(md, "Test set"))]},
    }


def newest(kind: str) -> Path:
    return max((ROOT / "docs/evidence").glob(f"{kind}-*.json")).relative_to(ROOT)


def main() -> None:
    loadtest_path, eval_path = newest("loadtest"), newest("eval")
    load = json.loads((ROOT / loadtest_path).read_text())
    ev = json.loads((ROOT / eval_path).read_text())
    stamp = datetime.strptime(eval_path.stem.removeprefix("eval-"), "%Y%m%d-%H%M")
    out = {
        "realism": realism((ROOT / REALISM).read_text()),
        "loadtest": {k: load[k] for k in ("at", "matches", "speed", "peak_replicas", "latency")},
        "eval": {"at": stamp.strftime("%Y-%m-%dT%H:%M:00Z"), "summary": ev["summary"], "checks": ev["checks"]},
        "sources": {"realism": str(REALISM), "loadtest": str(loadtest_path), "eval": str(eval_path)},
    }
    (ROOT / OUT).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}: {len(out['realism']['heldout']['rows'])} held-out rows, "
          f"{len(out['realism']['test']['rows'])} test rows, {loadtest_path.name}, {eval_path.name}")


if __name__ == "__main__":
    main()
