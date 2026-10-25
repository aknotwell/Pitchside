"""Score Reflex on the labelled questions against a recorded match state, with the live model.

    uv run python scripts/reflex/bench.py [--set dev|test] [--runs 2] [--show]

Runs about 2 minutes per run. A pass is never wrong, only lost coverage. Wrong means Reflex answered with a capability the
label does not accept, or answered a question labelled for the agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential

from matchlens.azure_io.common import async_openai
from matchlens.reflex.ask import Reflex

HERE = Path(__file__).parent
STATE = json.loads((HERE.parent.parent / "tests" / "data" / "match_state.json").read_text())
ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "https://ai-bhdr37v3onmcs.services.ai.azure.com/api/projects/matchlens")


def label(res) -> str:
    d = res.decision
    return d.capability if d.outcome == "answer" and res.text else ("decline" if d.outcome == "decline" else "pass")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--set", default="dev", choices=["dev", "test"], help="dev tunes; test is scored once")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--rpm", type=float, default=30, help="questions per minute (Tier 1 adds a call)")
    args = ap.parse_args()
    items = [json.loads(line) for line in (HERE / f"{args.set}.jsonl").read_text().splitlines() if line.strip()]

    loop = asyncio.get_running_loop()

    async def state():
        return STATE

    async with DefaultAzureCredential(exclude_interactive_browser_credential=True) as cred:
        reflex = Reflex(async_openai(ENDPOINT, cred))
        runs = []
        for _ in range(args.runs):
            run = []
            for it in items:  # sequential and paced: the deployment allows 50 requests a minute
                t = loop.time()
                run.append(await reflex.answer(it["q"], it["lang"], state))
                await asyncio.sleep(max(0.0, 60 / args.rpm - (loop.time() - t)))
            runs.append(run)

    first = runs[0]
    labels = [[label(r) for r in run] for run in runs]
    answerable = [it for it in items if it["ok"]]
    answered = [(it, r) for it, r in zip(items, first, strict=True) if label(r) != "pass"]
    wrong = [(it, r) for it, r in answered if label(r) not in it["ok"]]
    covered = [it for it, r in answered if label(r) in it["ok"]]
    flips = sum(len({run[i] for run in labels}) > 1 for i in range(len(items)))
    tier1 = [r for r in first if r.tier == 1]
    ungrounded = [r for r in first if r.decision.reason == "ungrounded"]
    print(f"questions {len(items)} (answerable {len(answerable)}, agent-only {len(items) - len(answerable)})")
    print(f"answered  {len(answered)} = {len(answered) / len(items):.0%} of all, "
          f"{len(covered)}/{len(answerable)} = {len(covered) / len(answerable):.0%} of answerable")
    print(f"wrong     {len(wrong)} ({len(wrong) / max(1, len(answered)):.1%} of answered)")
    print(f"tier 1    {len(tier1)} answered, {len(ungrounded)} sent to the agent by the grounding check")
    for tier in (0, 1):
        ms = [r.ms for r in first if r.tier == tier and r.text]
        if ms:
            print(f"ms tier {tier} p50 {statistics.median(ms):.0f}  max {max(ms)}")
    print(f"probe ms  p50 {statistics.median(r.probe_ms for r in first):.0f}")
    print(f"rerun flips {flips}/{len(items)}")
    for i, it in enumerate(items):
        if len({run[i] for run in labels}) > 1:
            print(f"  FLIP {it['q']!r}: {[run[i] for run in labels]} {[run[i].decision.reason for run in runs]}")
    for it, r in wrong:
        print(f"  WRONG {it['q']!r}: got {label(r)}, ok {it['ok']}")
    for i, it in enumerate(items):
        if args.show or (it["ok"] and labels[0][i] == "pass"):
            r = first[i]
            print(f"  {labels[0][i]:>10} {r.decision.reason:<18} {r.decision.confidence:.2f} {it['q']!r} -> {r.text or ''}"[:220])


if __name__ == "__main__":
    asyncio.run(main())
