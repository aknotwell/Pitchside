"""Public API (FastAPI on Container Apps): match list, state, cues, Web PubSub negotiate, producer actions."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from matchlens.azure_io.common import async_credential, settings, setup_telemetry
from matchlens.contracts import LANGUAGES, PERSONAS, cue_group, frames_group, state_group

log = logging.getLogger("matchlens.api")
OPS_PK = "_ops"
ARM = "https://management.azure.com"


class RateLimiter:
    """Per-client sliding window. Judges get generous limits; bots can't run up the bill."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and q[0] < now - self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


reads = RateLimiter(240, 60)
asks = RateLimiter(6, 60)
global_asks = RateLimiter(300, 3600)  # token budget guard for the judging window
starts = RateLimiter(3, 3600)
global_starts = RateLimiter(12, 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from azure.cosmos.aio import CosmosClient
    from azure.messaging.webpubsubservice.aio import WebPubSubServiceClient

    setup_telemetry("api")
    s = settings()
    app.state.cred = async_credential()
    app.state.cosmos = CosmosClient(s.cosmos_endpoint, credential=app.state.cred)
    db = app.state.cosmos.get_database_client(s.cosmos_database)
    app.state.db = {n: db.get_container_client(n) for n in ("matchState", "cues", "moments", "runs")}
    app.state.wps = WebPubSubServiceClient(endpoint=s.webpubsub_endpoint, hub=s.webpubsub_hub, credential=app.state.cred)
    app.state.http = aiohttp.ClientSession()
    app.state.ask = None
    yield
    await app.state.http.close()
    await app.state.cosmos.close()
    await app.state.cred.close()


app = FastAPI(title="MatchLens API", version="0.1.0", lifespan=lifespan)
if os.environ.get("CORS_DEV"):
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def client_key(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]


@app.middleware("http")
async def limit(request: Request, call_next):
    if request.url.path.startswith("/api/") and not reads.check(client_key(request)):
        return JSONResponse({"error": "rate limited"}, status_code=429)
    return await call_next(request)


async def query(container, sql: str, params: list[dict] | None = None, pk: str | None = None, limit: int = 200):
    items = []
    kwargs = {"parameters": params or []}
    if pk is not None:
        kwargs["partition_key"] = pk
    async for it in container.query_items(sql, **kwargs):
        items.append({k: v for k, v in it.items() if not k.startswith("_")})
        if len(items) >= limit:
            break
    return items


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "matchlens-api", "time": time.time()}


@app.get("/api/matches")
async def matches(request: Request):
    sql = ("SELECT c.id, c.displayClock, c.clockS, c.period, c.ended, c.home.name AS home, c.away.name AS away, "
           "c.home.goals AS homeGoals, c.away.goals AS awayGoals, c._ts AS updated FROM c "
           "WHERE c.type = 'matchState' ORDER BY c._ts DESC OFFSET 0 LIMIT 20")
    items = []
    async for it in request.app.state.db["matchState"].query_items(sql):
        items.append(it)
    return {"matches": items, "now": int(time.time())}


@app.get("/api/matches/{match_id}")
async def match(match_id: str, request: Request):
    try:
        doc = await request.app.state.db["matchState"].read_item(match_id, partition_key=match_id)
    except Exception as exc:
        raise HTTPException(404, "match not found") from exc
    return {k: v for k, v in doc.items() if not k.startswith("_")}


@app.get("/api/matches/{match_id}/cues")
async def cues(match_id: str, request: Request, persona: str = Query("casual"), lang: str = Query("en")):
    sql = ("SELECT * FROM c WHERE (c.persona = @p OR c.persona = 'all') AND (c.lang = @l OR c.lang = 'all') "
           "ORDER BY c.display_at_s DESC")
    items = await query(request.app.state.db["cues"], sql, [{"name": "@p", "value": persona}, {"name": "@l", "value": lang}],
                        pk=match_id, limit=60)
    return {"cues": list(reversed(items))}


@app.get("/api/matches/{match_id}/moments")
async def moments(match_id: str, request: Request):
    items = await query(request.app.state.db["moments"], "SELECT * FROM c ORDER BY c.clock_s DESC", pk=match_id, limit=50)
    runs = await query(request.app.state.db["runs"], "SELECT * FROM c WHERE c.type = 'storyRun'", pk=match_id, limit=100)
    return {"moments": list(reversed(items)), "runs": runs}


@app.get("/api/negotiate")
async def negotiate(request: Request, matchId: str, persona: str = "casual", lang: str = "en"):
    if persona not in PERSONAS or lang not in LANGUAGES:
        raise HTTPException(400, "unknown persona or language")
    groups = [frames_group(matchId), state_group(matchId), cue_group(matchId, persona, lang), cue_group(matchId, "all", "all")]
    token = await request.app.state.wps.get_client_access_token(groups=groups, minutes_to_expire=180)
    return {"url": token["url"], "groups": groups}


async def arm_token(request: Request) -> str:
    tok = await request.app.state.cred.get_token(f"{ARM}/.default")
    return tok.token


@app.post("/api/matches")
async def start_match(request: Request):
    """Start one simulator job execution (= one live match)."""
    key = client_key(request)
    if not starts.check(key) or not global_starts.check("all"):
        raise HTTPException(429, "match start limit reached, try again later")
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    speed = max(1.0, min(30.0, float(body.get("speed", 1))))
    seed = int(body.get("seed", 0)) or int(time.time()) % 99991
    period_min = max(5.0, min(45.0, float(body.get("periodMin", 45))))
    s = settings()
    job = f"{ARM}/subscriptions/{s.subscription_id}/resourceGroups/{s.resource_group}/providers/Microsoft.App/jobs/{s.sim_job_name}"
    headers = {"Authorization": f"Bearer {await arm_token(request)}"}
    http: aiohttp.ClientSession = request.app.state.http
    async with http.get(f"{job}?api-version=2024-03-01", headers=headers) as r:
        if r.status != 200:
            raise HTTPException(502, f"cannot read simulator job: {r.status}")
        container = (await r.json())["properties"]["template"]["containers"][0]
    env = {e["name"]: e for e in container.get("env", [])}
    env["SIM_SEED"] = {"name": "SIM_SEED", "value": str(seed)}
    env["SIM_SPEED"] = {"name": "SIM_SPEED", "value": str(speed)}
    env["SIM_PERIOD_MIN"] = {"name": "SIM_PERIOD_MIN", "value": str(period_min)}
    payload = {"containers": [{"name": container["name"], "image": container["image"], "env": list(env.values()),
                               "resources": container.get("resources")}]}
    async with http.post(f"{job}/start?api-version=2024-03-01", headers=headers, json=payload) as r:
        if r.status not in (200, 202):
            raise HTTPException(502, f"job start failed: {r.status} {await r.text()}")
        res = await r.json() if r.content_type == "application/json" else {}
    return {"started": True, "execution": res.get("name"), "seed": seed, "speed": speed, "periodMin": period_min}


async def get_asker(request: Request):
    if request.app.state.ask is not None:
        return request.app.state.ask
    from azure.keyvault.secrets.aio import SecretClient

    from matchlens.story.ask import AskTheMatch

    s = settings()
    async with SecretClient(os.environ["KEYVAULT_URI"], credential=request.app.state.cred) as kv:
        key = (await kv.get_secret("mcp-key")).value
    request.app.state.ask = AskTheMatch(s.foundry_project_endpoint, os.environ.get("ASK_DEPLOYMENT", "gpt-4.1-mini"),
                                        request.app.state.cred, os.environ["MCP_URL"], key)
    return request.app.state.ask


@app.post("/api/ask")
async def ask(request: Request):
    """Ask the match: an Agent Framework agent answering through the match-data MCP server."""
    if not asks.check(client_key(request)) or not global_asks.check("all"):
        raise HTTPException(429, "question limit reached, try again in a minute")
    body = await request.json()
    question = str(body.get("question", "")).strip()
    match_id = str(body.get("matchId", "")).strip()
    lang = body.get("lang", "en") if body.get("lang") in LANGUAGES else "en"
    if not question or not match_id or len(question) > 300:
        raise HTTPException(400, "matchId and a question (max 300 characters) are required")
    try:
        asker = await get_asker(request)
        return await asker.ask(match_id, question, lang)
    except TimeoutError as exc:
        raise HTTPException(504, "the agent took too long") from exc
    except Exception as exc:
        log.warning("ask failed: %s", exc)
        raise HTTPException(502, "the agent is unavailable right now") from exc


AUDIO_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_.-]{1,120}\.mp3$")


@app.get("/api/audio/{match_id}/{name}")
async def audio(match_id: str, name: str, request: Request):
    """Stream a voiced recap from private Blob storage (read with the API's managed identity)."""
    from azure.storage.blob.aio import BlobClient
    from fastapi.responses import Response

    path = f"{match_id}/{name}"
    if not AUDIO_NAME.match(path):
        raise HTTPException(400, "bad audio name")
    blob = BlobClient(settings().storage_blob_endpoint, "audio", path, credential=request.app.state.cred)
    try:
        async with blob:
            data = await (await blob.download_blob()).readall()
    except Exception as exc:
        raise HTTPException(404, "audio not found") from exc
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/replays")
async def replays(request: Request):
    """Finished matches packaged by the event-driven replay job."""
    items = []
    async for it in request.app.state.db["runs"].query_items(
            "SELECT TOP 20 c.matchId, c.home, c.away, c.score, c.at, c.cues, c.frames FROM c "
            "WHERE c.type = 'replay' ORDER BY c.at DESC"):
        items.append(it)
    return {"replays": items}


@app.get("/api/replays/{match_id}")
async def replay(match_id: str, request: Request):
    from azure.storage.blob.aio import BlobClient
    from fastapi.responses import Response

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", match_id):
        raise HTTPException(400, "bad match id")
    blob = BlobClient(settings().storage_blob_endpoint, "replays", f"{match_id}.json.gz", credential=request.app.state.cred)
    try:
        async with blob:
            data = await (await blob.download_blob()).readall()
    except Exception as exc:
        raise HTTPException(404, "replay not found") from exc
    return Response(data, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=3600"})


@app.post("/api/chaos")
async def chaos(request: Request):
    """Arm the chaos drill: the next moment kills a storyteller replica mid-workflow."""
    if not starts.check("chaos:" + client_key(request)):
        raise HTTPException(429, "chaos limit reached")
    doc = {"id": "chaos", "matchId": OPS_PK, "armed": True, "target": "storyteller", "armedAt": time.time()}
    await request.app.state.db["runs"].upsert_item(doc)
    return doc


@app.get("/api/ops")
async def ops(request: Request):
    """Producer console: chaos state and the latest storyteller runs across matches."""
    try:
        chaos_doc = await request.app.state.db["runs"].read_item("chaos", partition_key=OPS_PK)
    except Exception:
        chaos_doc = {"armed": False}
    runs = []
    async for it in request.app.state.db["runs"].query_items(
            "SELECT TOP 20 c.moment_id, c.kind, c.source, c.ms, c.resumed_from, c.skipped, c.at, c.matchId FROM c "
            "WHERE c.type = 'storyRun' ORDER BY c.at DESC"):
        runs.append(it)
    return {"chaos": {k: v for k, v in chaos_doc.items() if not k.startswith("_")}, "runs": runs}


def main() -> None:
    import uvicorn

    uvicorn.run("matchlens.services.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
