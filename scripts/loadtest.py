"""Load test: N simultaneous live matches, measuring KEDA scale-out and event -> screen latency.

Starts N simulator job executions, samples interpreter/storyteller replica counts, and watches
a few matches over Web PubSub exactly like a browser. Latency = time the cue reaches the
WebSocket client minus the Event Hubs enqueue time of the event that caused it.

    uv run python scripts/loadtest.py --matches 8 --speed 5 --period-min 10 --watch 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def azd(key: str) -> str:
    return sh("azd", "env", "get-value", key)


def replicas(rg: str, app: str) -> int:
    try:
        return int(sh("az", "containerapp", "replica", "list", "-g", rg, "-n", app, "--query", "length(@)", "-o", "tsv") or 0)
    except subprocess.CalledProcessError:
        return -1


def start(job_id: str, container: dict, seed: int, speed: float, period_min: float) -> str:
    """Start one execution via ARM. Env overrides REPLACE the template env, so merge them."""
    env = {e["name"]: e for e in container.get("env", [])}
    for k, v in {"SIM_SEED": seed, "SIM_SPEED": speed, "SIM_PERIOD_MIN": period_min}.items():
        env[k] = {"name": k, "value": str(v)}
    body = {"containers": [{"name": container["name"], "image": container["image"], "env": list(env.values()),
                            "resources": container.get("resources")}]}
    out = sh("az", "rest", "--method", "post", "--url",
             f"https://management.azure.com{job_id}/start?api-version=2025-07-01",
             "--body", json.dumps(body), "--query", "name", "-o", "tsv")
    return out


async def watch(api: str, match_id: str, until: float, sink: list[dict]) -> None:
    import websockets

    url = json.load(urllib.request.urlopen(f"{api}/api/negotiate?matchId={match_id}&persona=analyst&lang=en"))["url"]
    seen: set[tuple[str, str]] = set()
    async with websockets.connect(url, max_size=None) as ws:
        while time.time() < until:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except TimeoutError:
                continue
            m = json.loads(raw)
            if isinstance(m, str):
                m = json.loads(m)
            if m.get("type") != "cue":
                continue
            cue = m["cue"]
            enq = (cue.get("data") or {}).get("enqueuedAtMs")
            key = (match_id, cue["id"])
            if key in seen:
                continue  # measure first delivery only (the client de-dupes by cue id too)
            seen.add(key)
            if enq:
                sink.append({"match": match_id, "template": cue["template"], "source": cue["source"],
                             "latency_ms": time.time() * 1000 - enq})


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", type=int, default=8)
    ap.add_argument("--speed", type=float, default=5)
    ap.add_argument("--period-min", type=float, default=10)
    ap.add_argument("--watch", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=0, help="test duration; default = match length + 2 min")
    a = ap.parse_args()

    rg, job, api = azd("AZURE_RESOURCE_GROUP"), azd("SIM_JOB_NAME"), azd("API_URL")
    interp = azd("INTERPRETER_APP_NAME")
    story = interp.replace("ca-interp-", "ca-story-")
    job_doc = json.loads(sh("az", "resource", "show", "-g", rg, "-n", job, "--resource-type", "Microsoft.App/jobs", "-o", "json"))
    job_id, container = job_doc["id"], job_doc["properties"]["template"]["containers"][0]
    match_wall_s = (2 * a.period_min * 60 + 300) / a.speed
    duration = a.minutes * 60 or match_wall_s + 120
    base_seed = int(time.time()) % 50000

    print(f"starting {a.matches} matches at {a.speed}x ({a.period_min} min halves, ~{match_wall_s / 60:.1f} min wall each)")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.matches) as pool:
        names = list(pool.map(lambda i: start(job_id, container, base_seed + i, a.speed, a.period_min), range(a.matches)))
    match_ids = [f"m{base_seed + i:05d}-{n.rsplit('-', 1)[-1]}" for i, n in enumerate(names)]
    print(f"started in {time.time() - t0:.1f} s: {match_ids}")

    samples: list[dict] = []
    cues: list[dict] = []

    async def sampler():
        while time.time() - t0 < duration:
            loop = asyncio.get_running_loop()
            ri, rs = await asyncio.gather(loop.run_in_executor(None, replicas, rg, interp),
                                          loop.run_in_executor(None, replicas, rg, story))
            samples.append({"t_s": round(time.time() - t0), "interpreter": ri, "storyteller": rs})
            print(f"  t+{samples[-1]['t_s']:4d}s interpreter={ri} storyteller={rs} cues_seen={len(cues)}")
            await asyncio.sleep(15)

    async def run():
        await asyncio.sleep(25)  # let the jobs start
        watchers = [watch(api, mid, t0 + duration, cues) for mid in match_ids[: a.watch]]
        results = await asyncio.gather(sampler(), *watchers, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                print(f"  task failed: {type(r).__name__}: {r}")

    asyncio.run(asyncio.wait_for(run(), timeout=duration + 120))

    def summary(sel):
        xs = [c["latency_ms"] for c in cues if sel(c)]
        if not xs:
            return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None, "mean_ms": None}
        return {"n": len(xs), "p50_ms": round(pct(xs, 50)), "p95_ms": round(pct(xs, 95)),
                "max_ms": round(max(xs)) if xs else None, "mean_ms": round(statistics.mean(xs)) if xs else None}

    report = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "matches": a.matches, "speed": a.speed, "period_min": a.period_min, "watched": a.watch,
        "peak_replicas": {"interpreter": max((s["interpreter"] for s in samples), default=0),
                          "storyteller": max((s["storyteller"] for s in samples), default=0)},
        "latency": {"template (hot path)": summary(lambda c: c["source"] == "template"),
                    "agent (warm/cold path)": summary(lambda c: c["source"] == "agent"),
                    "fallback": summary(lambda c: c["source"] == "fallback")},
        "samples": samples,
    }
    out = Path("docs/evidence")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(t0))
    (out / f"loadtest-{stamp}.json").write_text(json.dumps({**report, "cues": cues}, indent=1))
    md = [f"# Load test {report['at']}", "",
          f"{a.matches} simultaneous matches at {a.speed}× ({a.period_min}-minute halves); {a.watch} watched over Web PubSub.", "",
          f"Peak replicas: interpreter **{report['peak_replicas']['interpreter']}**, storyteller **{report['peak_replicas']['storyteller']}**.", "",
          "| Path | Cues | p50 | p95 | max |", "|---|---|---|---|---|"]
    for k, v in report["latency"].items():
        md.append(f"| {k} | {v['n']} | {v['p50_ms']} ms | {v['p95_ms']} ms | {v['max_ms']} ms |")
    md += ["", "Latency = WebSocket receive time − Event Hubs enqueue time of the triggering event.", "",
           "| t (s) | interpreter | storyteller |", "|---|---|---|"]
    md += [f"| {s['t_s']} | {s['interpreter']} | {s['storyteller']} |" for s in samples]
    (out / f"loadtest-{stamp}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:12]))


if __name__ == "__main__":
    main()
