"""Public API (FastAPI on Container Apps): match list, state, cues, Web PubSub negotiate, producer actions."""

from __future__ import annotations

import asyncio
import gzip
import logging
import math
import os
import re
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from matchlens.azure_io.common import async_credential, async_openai, settings, setup_telemetry
from matchlens.contracts import (
    LANGUAGES,
    PERSONAS,
    RECORDINGS_CONTAINER,
    cue_group,
    frames_group,
    recording_blob,
    state_group,
)

log = logging.getLogger("matchlens.api")
OPS_PK = "_ops"
ARM = "https://management.azure.com"


class RateLimiter:
    """Per-client sliding window, per replica. Judges get generous limits; bots can't run up the bill."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.swept = time.monotonic()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        if now - self.swept >= self.window_s:
            self._sweep(now)
        q = self.hits[key]
        while q and q[0] < now - self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def _sweep(self, now: float) -> None:
        """Forget clients whose hits have all expired, so memory doesn't grow with every address."""
        self.hits = defaultdict(deque, {k: q for k, q in self.hits.items() if q and q[-1] >= now - self.window_s})
        self.swept = now


class SharedBudget:
    """A fixed-window counter in Cosmos (``runs``, partition ``_ops``), shared by every API replica.
    Updates are etag-checked, so concurrent replicas can't both take the last unit."""

    def __init__(self, name: str, limit: int, window_s: int):
        self.name, self.limit, self.window_s = name, limit, window_s

    async def take(self, container) -> bool:
        from azure.core import MatchConditions
        from azure.core.exceptions import (
            HttpResponseError,
            ResourceExistsError,
            ResourceNotFoundError,
        )

        window = int(time.time()) // self.window_s
        doc_id = f"budget-{self.name}-{window}"
        for _ in range(5):
            try:
                doc = await container.read_item(doc_id, partition_key=OPS_PK)
            except ResourceNotFoundError:
                try:
                    await container.create_item({"id": doc_id, "matchId": OPS_PK, "type": "budget", "count": 1,
                                                 "ttl": 2 * self.window_s})
                    return True
                except ResourceExistsError:
                    continue
            if doc["count"] >= self.limit:
                return False
            try:
                await container.replace_item(doc_id, {**doc, "count": doc["count"] + 1}, etag=doc["_etag"],
                                             match_condition=MatchConditions.IfNotModified)
                return True
            except HttpResponseError as exc:
                if exc.status_code != 412:  # 412: another replica updated it first
                    raise
                continue
        return False  # heavy contention: err on the side of the budget


class Cached:
    """One shared answer for endpoints every viewer polls: at most one Cosmos query per TTL."""

    def __init__(self, ttl_s: float):
        self.ttl_s, self.at, self.value = ttl_s, float("-inf"), None
        self.lock = asyncio.Lock()

    async def get(self, load):
        if time.monotonic() - self.at < self.ttl_s:
            return self.value
        async with self.lock:
            if time.monotonic() - self.at >= self.ttl_s:  # another request may have refreshed it
                self.value = await load()
                self.at = time.monotonic()
        return self.value

    def clear(self) -> None:
        self.at = float("-inf")


reads = RateLimiter(240, 60)
asks = RateLimiter(6, 60)
global_asks = SharedBudget("asks", 300, 3600)  # token budget guard for the judging window
starts = RateLimiter(3, 3600)
global_starts = SharedBudget("starts", 12, 3600)
LIST_TTL_S = 2.0
LIST_CACHE = {"Cache-Control": f"public, max-age={int(LIST_TTL_S)}"}
match_list, replay_list, ops_view = Cached(LIST_TTL_S), Cached(LIST_TTL_S), Cached(LIST_TTL_S)
scorecard_view = Cached(30.0)
LIVE_QUIET_S = 300  # a match that hasn't ended but has sent nothing for this long has crashed (as the web player decides)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from azure.cosmos.aio import CosmosClient
    from azure.messaging.webpubsubservice.aio import WebPubSubServiceClient
    from azure.storage.blob.aio import BlobServiceClient

    setup_telemetry("api")
    s = settings()
    app.state.cred = async_credential()
    app.state.cosmos = CosmosClient(s.cosmos_endpoint, credential=app.state.cred)
    db = app.state.cosmos.get_database_client(s.cosmos_database)
    app.state.db = {n: db.get_container_client(n) for n in ("matchState", "cues", "moments", "runs")}
    app.state.wps = WebPubSubServiceClient(endpoint=s.webpubsub_endpoint, hub=s.webpubsub_hub, credential=app.state.cred)
    app.state.blobs = BlobServiceClient(s.storage_blob_endpoint, credential=app.state.cred)
    app.state.http = aiohttp.ClientSession()
    app.state.ask = None
    from matchlens.reflex.ask import Reflex

    app.state.reflex = Reflex(async_openai(s.foundry_project_endpoint, app.state.cred),
                              os.environ.get("REFLEX_DEPLOYMENT", "gpt-4.1-mini"))
    yield
    await app.state.reflex.client.close()
    await app.state.http.close()
    await app.state.blobs.close()
    await app.state.cosmos.close()
    await app.state.cred.close()


app = FastAPI(title="MatchLens API", version="0.1.0", lifespan=lifespan)
if os.environ.get("CORS_DEV"):
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def client_key(request: Request) -> str:
    """The caller's address. Container Apps ingress *appends* the peer it saw to X-Forwarded-For,
    so only the last entry is trustworthy; anything before it is whatever the client sent."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip() or "?"
    return request.client.host if request.client else "?"


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

    async def load():
        return [it async for it in request.app.state.db["matchState"].query_items(sql)]
    items = await match_list.get(load)
    return JSONResponse({"matches": items, "now": int(time.time())}, headers=LIST_CACHE)


@app.get("/api/matches/{match_id}")
async def match(match_id: str, request: Request):
    try:
        doc = await request.app.state.db["matchState"].read_item(match_id, partition_key=match_id)
    except Exception as exc:
        raise HTTPException(404, "match not found") from exc
    return {k: v for k, v in doc.items() if not k.startswith("_")}


@app.get("/api/matches/{match_id}/cues")
async def cues(match_id: str, request: Request, persona: str = Query("casual"), lang: str = Query("en"),
               limit: int = Query(60, ge=1, le=1000)):
    """The newest `limit` cues, oldest first. The live player asks for the whole match so it can scrub back."""
    sql = ("SELECT * FROM c WHERE (c.persona = @p OR c.persona = 'all') AND (c.lang = @l OR c.lang = 'all') "
           "ORDER BY c.display_at_s DESC")
    items = await query(request.app.state.db["cues"], sql, [{"name": "@p", "value": persona}, {"name": "@l", "value": lang}],
                        pk=match_id, limit=limit)
    return {"cues": list(reversed(items))}


@app.get("/api/matches/{match_id}/moments")
async def moments(match_id: str, request: Request):
    items = await query(request.app.state.db["moments"], "SELECT * FROM c ORDER BY c.clock_s DESC", pk=match_id, limit=50)
    runs = await query(request.app.state.db["runs"], "SELECT * FROM c WHERE c.type = 'storyRun'", pk=match_id, limit=100)
    return {"moments": list(reversed(items)), "runs": runs}


@app.get("/api/matches/{match_id}/moments/{moment_id}")
async def moment_detail(match_id: str, moment_id: str, request: Request):
    """One moment with its storyteller run (and agent trace) and every cue it produced."""
    from azure.core.exceptions import ResourceNotFoundError

    db = request.app.state.db

    async def read(container: str, item: str):
        try:
            doc = await db[container].read_item(item, partition_key=match_id)
        except ResourceNotFoundError:
            return None
        return {k: v for k, v in doc.items() if not k.startswith("_")}

    moment, run, cue_docs = await asyncio.gather(
        read("moments", moment_id), read("runs", f"run-{moment_id}"),
        query(db["cues"], "SELECT * FROM c WHERE c.moment_id = @m", [{"name": "@m", "value": moment_id}],
              pk=match_id, limit=100))
    if moment is None and run is None and not cue_docs:
        raise HTTPException(404, "moment not found")
    return {"moment": moment, "run": run, "cues": cue_docs}


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
    if not starts.check(key) or not await global_starts.take(request.app.state.db["runs"]):
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


AGENT_HEAD_START_S = 2.5  # covers most probes and Tier 1 calls; a slower Reflex races the agent


@app.post("/api/ask")
async def ask(request: Request):
    """Ask the match. Reflex answers first when it can. The MCP agent starts when Reflex passes, or
    after a short head start, and is cancelled when Reflex answers; the first useful answer wins."""
    if not asks.check(client_key(request)) or not await global_asks.take(request.app.state.db["runs"]):
        raise HTTPException(429, "question limit reached, try again in a minute")
    body = await request.json()
    question = str(body.get("question", "")).strip()
    match_id = str(body.get("matchId", "")).strip()
    lang = body.get("lang", "en") if body.get("lang") in LANGUAGES else "en"
    if not question or not match_id or len(question) > 300:
        raise HTTPException(400, "matchId and a question (max 300 characters) are required")

    async def run_agent():
        return await (await get_asker(request)).ask(match_id, question, lang)

    async def read_state():
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return await request.app.state.db["matchState"].read_item(match_id, partition_key=match_id)
        except CosmosResourceNotFoundError:
            return None

    reflex = request.app.state.reflex
    reflex_task = asyncio.create_task(reflex.answer(question, lang, read_state))
    agent_task: asyncio.Task | None = None
    try:
        await asyncio.wait({reflex_task}, timeout=AGENT_HEAD_START_S)
        while True:
            if reflex_task.done():
                r = reflex_task.result()
                if r.text:
                    return {"answer": r.text, "tools": [], "model": "reflex" if r.tier == 0 else reflex.deployment,
                            "reflex": r.telemetry()}
            if agent_task is None:
                agent_task = asyncio.create_task(run_agent())
            pending = {agent_task} | ({reflex_task} if not reflex_task.done() else set())
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if agent_task in done and (agent_task.exception() is None or reflex_task.done()):
                break
            if agent_task in done:  # the agent failed; a Reflex answer still on its way can rescue the turn
                await asyncio.wait({reflex_task})
        telemetry = reflex_task.result().telemetry() if reflex_task.done() else {"outcome": "pass", "reason": "agent_first"}
        try:
            return {**agent_task.result(), "reflex": telemetry}
        except TimeoutError as exc:
            raise HTTPException(504, "the agent took too long") from exc
        except Exception as exc:
            log.warning("ask failed: %s", exc)
            raise HTTPException(502, "the agent is unavailable right now") from exc
    finally:
        for t in (reflex_task, agent_task):
            if t is not None and not t.done():
                t.cancel()


AUDIO_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_.-]{1,120}\.mp3$")


async def stream_blob(request: Request, container: str, name: str, what: str, headers: dict) -> Response:
    """Stream a private blob (read with the API's managed identity) chunk by chunk, so a replay
    never sits whole in the API's memory. One shared client and connection pool."""
    from azure.core.exceptions import ResourceNotFoundError

    blob = request.app.state.blobs.get_blob_client(container, name)
    try:
        downloader = await blob.download_blob()
    except ResourceNotFoundError as exc:
        raise HTTPException(404, f"{what} not found") from exc
    return StreamingResponse(downloader.chunks(), headers={"Content-Length": str(downloader.size), **headers})


@app.get("/api/audio/{match_id}/{name}")
async def audio(match_id: str, name: str, request: Request):
    """Stream a voiced recap."""
    path = f"{match_id}/{name}"
    if not AUDIO_NAME.match(path):
        raise HTTPException(400, "bad audio name")
    return await stream_blob(request, "audio", path, "audio",
                             {"Content-Type": "audio/mpeg", "Cache-Control": "public, max-age=86400"})


@app.get("/api/replays")
async def replays(request: Request):
    """Finished matches packaged by the event-driven replay job."""
    async def load():
        return [it async for it in request.app.state.db["runs"].query_items(
            "SELECT TOP 20 c.matchId, c.home, c.away, c.score, c.at, c.cues, c.frames FROM c "
            "WHERE c.type = 'replay' ORDER BY c.at DESC")]
    return JSONResponse({"replays": await replay_list.get(load)}, headers=LIST_CACHE)


MATCH_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@app.get("/api/replays/{match_id}")
async def replay(match_id: str, request: Request):
    if not MATCH_ID.fullmatch(match_id):
        raise HTTPException(400, "bad match id")
    return await stream_blob(request, "replays", f"{match_id}.json.gz", "replay",
                             {"Content-Type": "application/json", "Content-Encoding": "gzip",
                              "Cache-Control": "public, max-age=3600"})


class RecordingCache:
    """Each match's recording, gzipped, so a burst of joiners costs one download and one compression.

    The blob only grows, so a copy at least as long as the blob was when a request arrived holds
    everything that request could have seen."""

    class Entry:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.size, self.body = -1, b""

    def __init__(self, max_matches: int = 16):
        self.max_matches = max_matches
        self.entries: OrderedDict[str, RecordingCache.Entry] = OrderedDict()

    async def get(self, match_id: str, size: int, download) -> bytes:
        e = self.entries.setdefault(match_id, RecordingCache.Entry())
        self.entries.move_to_end(match_id)
        while len(self.entries) > self.max_matches:
            self.entries.popitem(last=False)
        async with e.lock:  # concurrent requests wait for the first one's copy instead of making their own
            if e.size < size:
                data = await download(size)
                e.body = await asyncio.to_thread(gzip.compress, data, 1)  # level 6 is ~30% smaller and ~4x slower
                e.size = len(data)
            return e.body


recording_cache = RecordingCache()


@app.get("/api/matches/{match_id}/recording")
async def recording(match_id: str, request: Request, start: int | None = Query(None, alias="from", ge=0)):
    """Every live tick broadcast so far (one JSON line each), so a late joiner can scrub back to kick-off.
    `from` asks for only the ticks after that many bytes, so a reconnecting viewer fetches just what it
    missed. A reply is the recording's bytes from `from` (or 0) to a tick boundary, so `from` plus its
    decoded length is the next `from`. 204 means nothing new."""
    from azure.core.exceptions import ResourceNotFoundError

    if not MATCH_ID.fullmatch(match_id):
        raise HTTPException(400, "bad match id")
    blob = request.app.state.blobs.get_blob_client(RECORDINGS_CONTAINER, recording_blob(match_id))

    async def download(end: int, begin: int = 0) -> bytes:
        """Bytes [begin, end), one GET per range. One download_blob() of several ranges pins the later
        ones to the first's ETag, which the next live append changes. A range no longer than the client's
        max_single_get_size is one GET. A prefix of an append blob never changes, and each tick is one
        atomic append, so it ends on a whole line."""
        step = blob._config.max_single_get_size
        return b"".join([await (await blob.download_blob(offset=o, length=min(step, end - o))).readall()
                         for o in range(begin, end, step)])

    try:
        size = (await blob.get_blob_properties()).size
        if not size:  # created, but its first tick not yet appended
            raise ResourceNotFoundError("recording is empty")
        if start is None:
            body = await recording_cache.get(match_id, size, download)
        elif start < size:  # a tail is small: no cache, and quick to compress
            body = await asyncio.to_thread(gzip.compress, await download(size, start), 1)
        else:  # nothing new, or an offset past the end
            body = None
    except ResourceNotFoundError as exc:  # usual at kick-off: nothing has been appended yet
        raise HTTPException(404, "recording not found") from exc
    except Exception as exc:
        log.warning("recording %s unavailable: %s", match_id, exc)
        raise HTTPException(502, "recording unavailable") from exc
    if start is not None and start > size:
        raise HTTPException(400, "from is past the end of the recording")
    if body is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return Response(body, media_type="application/x-ndjson", headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"})


@app.post("/api/chaos")
async def chaos(request: Request):
    """Arm the chaos drill: the next moment kills a storyteller replica mid-workflow."""
    if not starts.check("chaos:" + client_key(request)):
        raise HTTPException(429, "chaos limit reached")
    doc = {"id": "chaos", "matchId": OPS_PK, "armed": True, "target": "storyteller", "armedAt": time.time()}
    await request.app.state.db["runs"].upsert_item(doc)
    ops_view.clear()
    return doc


@app.get("/api/ops")
async def ops(request: Request):
    """Producer console: chaos state and the latest storyteller runs across matches."""
    async def load():
        try:
            chaos_doc = await request.app.state.db["runs"].read_item("chaos", partition_key=OPS_PK)
        except Exception:
            chaos_doc = {"armed": False}
        runs = [it async for it in request.app.state.db["runs"].query_items(
            "SELECT TOP 20 c.moment_id, c.kind, c.source, c.ms, c.resumed_from, c.skipped, c.at, c.matchId FROM c "
            "WHERE c.type = 'storyRun' ORDER BY c.at DESC")]
        return {"chaos": {k: v for k, v in chaos_doc.items() if not k.startswith("_")}, "runs": runs}
    return JSONResponse(await ops_view.get(load), headers=LIST_CACHE)


def percentile(values: list[int], p: float) -> int | None:
    """Nearest rank: the smallest value with at least ``p`` of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


RECENT_FIELDS = ("moment_id", "matchId", "kind", "source", "ms", "at", "resumed_from", "personas", "steps")


def score_runs(runs: list[dict]) -> dict:
    """Storyteller runs, newest first, summed into the scorecard's run, verifier and recent sections."""
    sources = [r.get("source") for r in runs]
    verifier = [s for r in runs for s in r.get("steps") or [] if s.get("step") == "verifier"]
    return {
        "runs": {"total": sum(1 for r in runs if not r.get("skipped")), "agent": sources.count("agent"), "partial": sources.count("partial"),
                 "fallback": sources.count("fallback"), "skipped": sum(1 for r in runs if r.get("skipped")),
                 "resumed": sum(1 for r in runs if r.get("resumed_from")),
                 **{f"p{p}_ms": percentile([r["ms"] for r in runs if not r.get("skipped") and r.get("ms") is not None],
                                           p / 100) for p in (50, 95)}},
        "verifier": {"checked": len(verifier), "rejected": sum(1 for s in verifier if s.get("outcome") == "rejected"),
                     "retried_ok": sum(1 for s in verifier if s.get("attempt", 0) > 0 and s.get("outcome") == "accepted")},
        "recent": [{k: r.get(k) for k in RECENT_FIELDS} for r in runs if r.get("steps")][:12],
    }


@app.get("/api/scorecard")
async def scorecard(request: Request):
    """The last 24 h of the storyteller in numbers, for the judges' scorecard. Three Cosmos reads per 30 s."""
    db = request.app.state.db

    async def load():
        now = time.time()

        async def chaos_doc():
            try:
                doc = await db["runs"].read_item("chaos", partition_key=OPS_PK)
            except Exception:
                return {"armed": False}
            return {k: v for k, v in doc.items() if not k.startswith("_")}

        runs, states, chaos_state = await asyncio.gather(
            query(db["runs"], "SELECT c.moment_id, c.matchId, c.kind, c.source, c.ms, c.at, c.resumed_from, c.skipped, "
                  "c.personas, c.steps FROM c WHERE c.type = 'storyRun' AND c.at > @since ORDER BY c.at DESC",
                  [{"name": "@since", "value": now - 86400}], limit=5000),
            query(db["matchState"], "SELECT c.ended, c._ts AS updated FROM c WHERE c.type = 'matchState' AND c._ts > @since",
                  [{"name": "@since", "value": int(now) - 86400}], limit=1000),
            chaos_doc())
        return {"generated_at": now, "window_h": 24, **score_runs(runs),
                "matches": {"live": sum(1 for m in states if not m.get("ended") and now - m["updated"] < LIVE_QUIET_S),
                            "finished_24h": sum(1 for m in states if m.get("ended"))},
                "chaos": chaos_state}

    return JSONResponse(await scorecard_view.get(load), headers={"Cache-Control": "public, max-age=30"})


def main() -> None:
    import uvicorn

    uvicorn.run("matchlens.services.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
