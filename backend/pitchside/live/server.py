"""Stream a match to the browser while it is being simulated.

One WebSocket per viewer at `/ws/match`. The query string picks the match, e.g.
`/ws/match?home=ashenford&away=greywater&seed=42&speed=4`. Messages, all JSON:

* `{"type": "meta", "meta": MatchMeta}` once, first. The final score and the scenario's
  scripted events are left out: they would tell the viewer what is going to happen.
* `{"type": "frame", "frame": ..., "events": [...], "score": {...}}` every 0.1 s.
* `{"type": "end", "score": {...}}` at the final whistle, then the socket closes.

Run with `uv run pitchside-live` (or `make live`).
"""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, status

from pitchside.generator import MatchSim, build_team
from pitchside.generator.output import match_meta
from pitchside.generator.scenarios import SCENARIOS
from pitchside.generator.teams import CLUBS

from .feed import LiveFeed

TICK = 0.1  # wall-clock seconds between frames sent to the viewer
MAX_SPEED = 20.0
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

app = FastAPI(title="Pitchside live")


def origin_allowed(ws: WebSocket) -> bool:
    """Browsers don't apply CORS to WebSockets, so any open web page could connect here.

    Allow non-browser clients (no Origin header), local pages such as the Vite dev
    server, and pages served by this same host.
    """
    origin = ws.headers.get("origin")
    if origin is None:
        return True
    parts = urlsplit(origin)
    return parts.hostname in LOCAL_HOSTS or parts.netloc == ws.headers.get("host")


@app.websocket("/ws/match")
async def match_ws(
    ws: WebSocket,
    home: str = "ashenford",
    away: str = "greywater",
    seed: int = Query(42, ge=0, le=2**31 - 1),
    scenario: str = "open",
    speed: float = Query(1.0, gt=0, le=MAX_SPEED),
    half_length: float = Query(45.0, ge=1, le=45),
) -> None:
    if not origin_allowed(ws):
        await ws.close(status.WS_1008_POLICY_VIOLATION, "origin not allowed")
        return
    # Only built-in scenarios: load_scenario() would also accept a file path.
    if home not in CLUBS or away not in CLUBS or home == away or scenario not in SCENARIOS:
        await ws.close(status.WS_1008_POLICY_VIOLATION, "unknown or duplicate club, or scenario")
        return
    await ws.accept()

    sim = MatchSim(build_team(home), build_team(away), seed, SCENARIOS[scenario], half_length)
    feed = LiveFeed(sim)
    meta = match_meta(sim).model_copy(
        update={"final_score": None, "scenario": {"name": sim.scenario.name}}
    )
    loop = asyncio.get_running_loop()
    try:
        await ws.send_json({"type": "meta", "meta": meta.model_dump(mode="json")})
        start = loop.time()
        while not feed.finished:
            events = feed.advance_to((loop.time() - start) * speed)
            await ws.send_json(
                {"type": "frame", "frame": feed.frame(), "events": events, "score": feed.score}
            )
            await asyncio.sleep(TICK)
        await ws.send_json({"type": "end", "score": feed.score})
        await ws.close()
    except WebSocketDisconnect:
        pass  # viewer left; the simulation is simply dropped


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pitchside-live", description="Live match server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    uvicorn.run(app, host=args.host, port=args.port)
