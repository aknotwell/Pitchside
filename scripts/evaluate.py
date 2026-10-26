"""Evaluation gate: are the storyteller's explanations grounded, coherent and fast?

Generates golden moments from seeded (calibrated) synthetic matches, runs the real Agent
Framework storyteller against Foundry, then scores every English variant with the Azure AI
Evaluation SDK (Groundedness + Coherence, judged by a Foundry deployment, Entra auth) and our
own code verifier. Exits non-zero if a threshold is missed, so it can gate a deploy.

    uv run python scripts/evaluate.py --seeds 501 502 503 --max-moments 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential

from matchlens.interpret.state import MatchInterpreter
from matchlens.sim.engine import simulate
from matchlens.story.workflow import DEFAULT_SEGMENTS, FoundryExplainer, OverlayBudget, Storyteller

THRESHOLDS = {"groundedness_mean": 4.0, "coherence_mean": 3.5, "agent_rate": 0.85, "p95_ms": 30000}


def azd(key: str) -> str:
    return subprocess.run(["azd", "env", "get-value", key], check=True, capture_output=True, text=True).stdout.strip()


def golden_moments(seeds: list[int], limit: int):
    out, rosters = [], {}
    for s in seeds:
        r = simulate(s, match_id=f"eval-{s}")
        it = MatchInterpreter(r.metadata.match_id)
        for m in r.stream():
            out += it.handle(m).moments
        rosters[r.metadata.match_id] = {p.name for t in (r.metadata.home, r.metadata.away) for p in t.players}
    # Prefer variety: interleave kinds
    by_kind: dict[str, list] = {}
    for m in out:
        by_kind.setdefault(m.kind, []).append(m)
    picked = []
    while len(picked) < limit and any(by_kind.values()):
        for k in list(by_kind):
            if by_kind[k] and len(picked) < limit:
                picked.append(by_kind[k].pop(0))
    return picked, rosters


def evidence_text(m) -> str:
    lines = [f"Moment: {m.headline} (minute {int(m.clock_s // 60)}). Score {m.score.get('h', 0)}-{m.score.get('a', 0)}.",
             f"People and clubs: {', '.join(m.names.values())}."]
    for e in m.evidence:
        extra = f" (from {e.before} to {e.after})" if e.before is not None and e.after is not None else ""
        lines.append(f"{e.label}: {e.value}{(' ' + e.unit) if e.unit else ''}{extra}.")
    return "\n".join(lines)


async def run_storyteller(moments, rosters, project_endpoint: str, deployments: list[str]):
    async with AsyncAzureCliCredential() as cred:
        story = Storyteller(FoundryExplainer(project_endpoint, deployments, cred, timeout_s=30),
                            roster=lambda mid: rosters.get(mid, set()), segments=DEFAULT_SEGMENTS,
                            budget=OverlayBudget(window_s=0))
        results = []
        for m in moments:
            cues, info = await story.tell(m)
            results.append((m, cues, info))
            print(f"  {m.kind:15s} {info['source']:8s} {info['ms']:6d} ms")
        return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[501, 502, 503])
    ap.add_argument("--max-moments", type=int, default=12)
    ap.add_argument("--judge", default="gpt-4.1-mini")
    a = ap.parse_args()

    from azure.ai.evaluation import CoherenceEvaluator, GroundednessEvaluator

    project = azd("FOUNDRY_PROJECT_ENDPOINT")
    account = project.split("//")[1].split(".")[0]
    model_config = {"azure_endpoint": f"https://{account}.openai.azure.com/", "azure_deployment": a.judge,
                    "api_version": "2024-10-21"}
    cred = AzureCliCredential()
    grounded = GroundednessEvaluator(model_config, credential=cred)
    coherent = CoherenceEvaluator(model_config, credential=cred)

    print("generating golden moments…")
    moments, rosters = golden_moments(a.seeds, a.max_moments)
    print(f"running the storyteller on {len(moments)} moments…")
    results = asyncio.run(run_storyteller(moments, rosters, project, ["gpt-5-mini", "gpt-4.1-mini"]))

    print("scoring with Foundry evaluators…")
    rows = []
    for m, cues, info in results:
        ctx = evidence_text(m)
        for c in cues:
            if c.lang != "en":
                continue
            response = f"{c.title}. {c.text}"
            g = grounded(response=response, context=ctx)
            q = coherent(query=f"Explain why this football moment matters: {m.headline}", response=response)
            rows.append({"moment": m.moment_id, "kind": m.kind, "persona": c.persona, "source": c.source,
                         "ms": info["ms"], "groundedness": g.get("groundedness"), "coherence": q.get("coherence"),
                         "groundedness_reason": g.get("groundedness_reason"), "text": response})

    all_cues = [c for _, cues, _ in results for c in cues]
    agent_rate = sum(1 for c in all_cues if c.source == "agent") / max(1, len(all_cues))  # per persona variant
    lat = sorted(i["ms"] for _, _, i in results)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    summary = {
        "moments": len(results), "variants_scored": len(rows),
        "groundedness_mean": round(statistics.mean(r["groundedness"] for r in rows), 2),
        "coherence_mean": round(statistics.mean(r["coherence"] for r in rows), 2),
        "agent_rate": round(agent_rate, 2), "p50_ms": lat[len(lat) // 2], "p95_ms": p95,
    }
    checks = {
        "groundedness_mean": summary["groundedness_mean"] >= THRESHOLDS["groundedness_mean"],
        "coherence_mean": summary["coherence_mean"] >= THRESHOLDS["coherence_mean"],
        "agent_rate": summary["agent_rate"] >= THRESHOLDS["agent_rate"],
        "p95_ms": summary["p95_ms"] <= THRESHOLDS["p95_ms"],
    }
    out = Path("docs/evidence")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime())
    (out / f"eval-{stamp}.json").write_text(json.dumps({"summary": summary, "checks": checks, "rows": rows}, indent=1))
    md = [f"# Explanation quality evaluation {stamp}", "",
          f"{summary['moments']} golden moments from seeds {a.seeds}; storyteller = Agent Framework workflow on "
          f"`gpt-5-mini` (fallback `gpt-4.1-mini`); judge = Foundry `{a.judge}` via the Azure AI Evaluation SDK "
          "(Entra auth). English variants scored for every persona.", "",
          "| Metric | Value | Threshold | Pass |", "|---|---|---|---|",
          f"| Groundedness (1–5, mean) | {summary['groundedness_mean']} | ≥ {THRESHOLDS['groundedness_mean']} | {'✅' if checks['groundedness_mean'] else '❌'} |",
          f"| Coherence (1–5, mean) | {summary['coherence_mean']} | ≥ {THRESHOLDS['coherence_mean']} | {'✅' if checks['coherence_mean'] else '❌'} |",
          f"| Agent-written (passed code verifier) | {summary['agent_rate']:.0%} | ≥ {THRESHOLDS['agent_rate']:.0%} | {'✅' if checks['agent_rate'] else '❌'} |",
          f"| Workflow latency p50 / p95 | {summary['p50_ms']} / {summary['p95_ms']} ms | p95 ≤ {THRESHOLDS['p95_ms']} ms | {'✅' if checks['p95_ms'] else '❌'} |",
          "", "| Kind | Persona | Source | Grounded | Coherent | Text |", "|---|---|---|---|---|---|"]
    md += [f"| {r['kind']} | {r['persona']} | {r['source']} | {r['groundedness']} | {r['coherence']} | {r['text'][:140]} |"
           for r in rows]
    (out / f"eval-{stamp}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:10]))
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
