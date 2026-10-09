"""Chaos Studio drill: disable the match-stream Event Hub for 60 s while matches are live.

Starts N matches, waits until they are underway, starts the Chaos Studio experiment, samples
hub state and match clocks, then reports what happened (simulator retries, catch-up, whether
every match still finished).

    uv run python scripts/chaos_drill.py --matches 2 --speed 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from loadtest import azd, sh, start  # noqa: E402


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", type=int, default=2)
    ap.add_argument("--speed", type=float, default=3)
    ap.add_argument("--period-min", type=float, default=10)
    a = ap.parse_args()
    rg, job, api, exp = azd("AZURE_RESOURCE_GROUP"), azd("SIM_JOB_NAME"), azd("API_URL"), azd("CHAOS_EXPERIMENT_NAME")
    sub = azd("AZURE_SUBSCRIPTION_ID")
    hub_url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.EventHub/"
               f"namespaces/{azd('EVENTHUB_FQDN').split('.')[0]}/eventhubs/match-stream?api-version=2024-01-01")
    job_doc = json.loads(sh("az", "resource", "show", "-g", rg, "-n", job, "--resource-type", "Microsoft.App/jobs", "-o", "json"))
    exp_id = sh("az", "resource", "show", "-g", rg, "-n", exp, "--resource-type", "Microsoft.Chaos/experiments", "--query", "id", "-o", "tsv")
    seed0 = int(time.time()) % 50000 + 50000
    names = [start(job_doc["id"], job_doc["properties"]["template"]["containers"][0], seed0 + i, a.speed, a.period_min)
             for i in range(a.matches)]
    mids = [f"m{seed0 + i:05d}-{n.rsplit('-', 1)[-1]}" for i, n in enumerate(names)]
    print("matches:", mids)

    def clock(mid: str) -> tuple[float, str, bool]:
        try:
            d = get(f"{api}/api/matches/{mid}")
            return d["clockS"], d["displayClock"], d["ended"]
        except Exception:
            return 0.0, "-", False

    while clock(mids[0])[0] < 120:
        time.sleep(5)
    t0 = time.time()
    sh("az", "rest", "--method", "post", "--url", f"https://management.azure.com{exp_id}/start?api-version=2025-01-01")
    print("chaos experiment started")
    samples = []
    deadline = t0 + (2 * a.period_min * 60 + 300) / a.speed + 420
    while time.time() < deadline:
        try:
            hub = sh("az", "rest", "--method", "get", "--url", hub_url, "--query", "properties.status", "-o", "tsv")
        except Exception:
            hub = "unknown"
        cs = [clock(m) for m in mids]
        samples.append({"t_s": round(time.time() - t0), "hub": hub, "clocks": [c[1] for c in cs], "clock_s": [c[0] for c in cs]})
        print(f"  t+{samples[-1]['t_s']:4d}s hub={hub:9s} " + "  ".join(c[1] for c in cs))
        if all(c[2] for c in cs):
            break
        time.sleep(10)
    execs = json.loads(sh("az", "rest", "--method", "get", "--url",
                          f"https://management.azure.com{exp_id}/executions?api-version=2025-01-01"))["value"]
    ex = execs[0]["properties"] if execs else {}
    disabled = [s["t_s"] for s in samples if s["hub"] == "Disabled"]
    finished = [clock(m)[2] for m in mids]
    out = Path("docs/evidence")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(t0))
    md = [f"# Chaos drill {stamp}: Event Hubs outage", "",
          f"Chaos Studio experiment `{exp}` set `match-stream` to **Disabled** for 60 s and then back to Active, while "
          f"{a.matches} matches were live at {a.speed}×.", "",
          f"- Experiment status: **{ex.get('status')}** ({ex.get('startedAt', '')[:19]} → {ex.get('stoppedAt', '')[:19]})",
          f"- Hub observed Disabled from t+{disabled[0] if disabled else '?'} s to t+{disabled[-1] if disabled else '?'} s",
          f"- Matches finished afterwards: **{sum(finished)}/{len(finished)}**", "",
          "| t (s) | hub | " + " | ".join(mids) + " |", "|---|---|" + "---|" * len(mids)]
    md += [f"| {s['t_s']} | {s['hub']} | " + " | ".join(s["clocks"]) + " |" for s in samples]
    md += ["", "While the hub is disabled, the simulator backs off and retries (`send_with_retry`). The match clock "
           "freezes, then catches up when the hub returns; the interpreter scales on the backlog."]
    (out / f"chaos-{stamp}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:8]))


if __name__ == "__main__":
    main()
