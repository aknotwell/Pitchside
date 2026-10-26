"""API endpoint tests with in-memory fakes for Cosmos and Blob (no Azure needed)."""

import asyncio
import gzip
import json

import pytest
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from fastapi.testclient import TestClient

from matchlens.services import api as api_mod
from matchlens.services.api import Cached, RateLimiter, SharedBudget, app


class FakeContainer:
    def __init__(self, docs=None, rows=None):
        self.docs = {d["id"]: dict(d) for d in docs or []}
        self.rows, self.queries, self.etag = rows or [], 0, 0

    async def query_items(self, sql, **kw):
        self.queries += 1
        for r in self.rows:
            yield r

    async def read_item(self, item, partition_key):
        if item not in self.docs:
            raise ResourceNotFoundError("missing")
        return dict(self.docs[item])

    async def create_item(self, doc):
        if doc["id"] in self.docs:
            raise ResourceExistsError("exists")
        self.etag += 1
        self.docs[doc["id"]] = {**doc, "_etag": str(self.etag)}

    async def upsert_item(self, doc):
        self.docs[doc["id"]] = dict(doc)

    async def replace_item(self, item, body, etag, match_condition):
        if self.docs[item]["_etag"] != etag:
            err = HttpResponseError("precondition failed")
            err.status_code = 412
            raise err
        self.etag += 1
        self.docs[item] = {**body, "_etag": str(self.etag)}


class FakeDownloader:
    def __init__(self, data: bytes, chunk=4):
        self.data, self.size, self.chunk = data, len(data), chunk

    async def chunks(self):
        for i in range(0, len(self.data), self.chunk):
            yield self.data[i:i + self.chunk]


class FakeBlobs:
    def __init__(self, files):
        self.files, self.opened = files, []

    def get_blob_client(self, container, name):
        files, opened = self.files, self.opened

        class _B:
            async def download_blob(self):
                opened.append((container, name))
                if (container, name) not in files:
                    raise ResourceNotFoundError("missing")
                return FakeDownloader(files[(container, name)])
        return _B()


class FakeWps:
    async def get_client_access_token(self, groups, minutes_to_expire):
        return {"url": "wss://example/client?access_token=x"}


@pytest.fixture
def client():
    replay = gzip.compress(json.dumps({"matchId": "m1", "frames": []}).encode())
    app.state.db = {
        "matchState": FakeContainer([{"id": "m1", "matchId": "m1", "type": "matchState", "_rid": "x"}],
                                    rows=[{"id": "m1", "home": "A", "away": "B"}]),
        "cues": FakeContainer(), "moments": FakeContainer(),
        "runs": FakeContainer(rows=[{"matchId": "m1", "home": "A"}]),
    }
    app.state.blobs = FakeBlobs({("replays", "m1.json.gz"): replay, ("audio", "m1/e1-goal-en.mp3"): b"ID3audio"})
    app.state.wps = FakeWps()
    for limiter in (api_mod.reads, api_mod.asks, api_mod.starts):
        limiter.hits.clear()
    for c in (api_mod.match_list, api_mod.replay_list, api_mod.ops_view):
        c.clear()
    return TestClient(app)  # not entered: the Azure lifespan doesn't run


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_matches_and_unknown_match(client):
    r = client.get("/api/matches")
    assert r.status_code == 200 and r.json()["matches"][0]["id"] == "m1"
    assert r.headers["cache-control"] == "public, max-age=2"
    assert client.get("/api/matches/m1").json() == {"id": "m1", "matchId": "m1", "type": "matchState"}
    assert client.get("/api/matches/nope").status_code == 404


def test_list_endpoints_share_one_query_per_ttl(client):
    """#48: every viewer polls these; 50 polls inside the TTL cost one Cosmos query."""
    for _ in range(50):
        client.get("/api/matches")
        client.get("/api/replays")
    assert app.state.db["matchState"].queries == 1
    assert app.state.db["runs"].queries == 1


@pytest.mark.parametrize("q", ["persona=pirate&lang=en", "persona=casual&lang=klingon"])
def test_negotiate_rejects_unknown_persona_or_language(client, q):
    assert client.get(f"/api/negotiate?matchId=m1&{q}").status_code == 400


def test_negotiate(client):
    r = client.get("/api/negotiate?matchId=m1&persona=analyst&lang=es")
    assert r.status_code == 200 and r.json()["url"].startswith("wss://")


def test_replay_is_streamed(client):
    r = client.get("/api/replays/m1")
    assert r.status_code == 200 and r.json()["matchId"] == "m1"  # httpx undoes the gzip encoding
    assert r.headers["content-encoding"] == "gzip"
    assert client.get("/api/replays/nope").status_code == 404


def test_audio_is_streamed(client):
    r = client.get("/api/audio/m1/e1-goal-en.mp3")
    assert r.status_code == 200 and r.content == b"ID3audio" and r.headers["content-type"] == "audio/mpeg"


@pytest.mark.parametrize("path", ["/api/replays/..%2Fsecrets", "/api/replays/a.b", "/api/audio/m1/..%2F..%2Fx.mp3",
                                  "/api/audio/m1/notes.txt", "/api/audio/m%201/x.mp3"])
def test_blob_names_reject_path_traversal(client, path):
    assert client.get(path).status_code in (400, 404)
    assert all(".." not in name for _, name in app.state.blobs.opened)


@pytest.mark.parametrize("body", [{"matchId": "m1", "question": ""}, {"matchId": "", "question": "who scored?"},
                                  {"matchId": "m1", "question": "x" * 301}])
def test_ask_input_limits(client, body):
    assert client.post("/api/ask", json=body).status_code == 400


def test_read_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(api_mod, "reads", RateLimiter(3, 60))
    codes = [client.get("/api/health").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_forged_forwarded_for_does_not_get_a_fresh_bucket(client, monkeypatch):
    """#38: ACA appends the real peer, so a client can only forge entries *before* it."""
    monkeypatch.setattr(api_mod, "reads", RateLimiter(2, 60))
    codes = [client.get("/api/health", headers={"x-forwarded-for": f"10.0.0.{i}, 203.0.113.7"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]
    other = client.get("/api/health", headers={"x-forwarded-for": "10.0.0.1, 198.51.100.2"})
    assert other.status_code == 200


def test_rate_limiter_forgets_idle_clients(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(api_mod.time, "monotonic", lambda: now[0])
    rl = RateLimiter(5, 60)
    for i in range(100):
        rl.check(f"client-{i}")
    now[0] += 61
    rl.check("fresh")
    assert set(rl.hits) == {"fresh"}


def test_shared_budget_is_shared_and_etag_checked():
    runs = FakeContainer()
    a, b = SharedBudget("asks", 3, 3600), SharedBudget("asks", 3, 3600)  # two replicas
    took = [asyncio.run(x.take(runs)) for x in (a, b, a, b)]
    assert took == [True, True, True, False]

    # a concurrent replica bumps the doc between our read and our replace: we retry, not double-count
    runs2 = FakeContainer()
    budget = SharedBudget("starts", 10, 3600)
    asyncio.run(budget.take(runs2))
    orig = runs2.read_item

    async def racing_read(item, partition_key):
        doc = await orig(item, partition_key)
        if not getattr(racing_read, "done", False):
            racing_read.done = True
            runs2.etag += 1
            runs2.docs[item] = {**runs2.docs[item], "count": runs2.docs[item]["count"] + 1, "_etag": str(runs2.etag)}
        return doc
    runs2.read_item = racing_read
    assert asyncio.run(budget.take(runs2)) is True
    assert next(iter(runs2.docs.values()))["count"] == 3


def test_cached_loads_once_under_concurrency():
    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.01)
        return len(calls)

    async def go():
        c = Cached(5)
        return await asyncio.gather(*(c.get(load) for _ in range(20)))
    assert asyncio.run(go()) == [1] * 20 and len(calls) == 1
