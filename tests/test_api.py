import asyncio
import gzip
from types import SimpleNamespace

import azure.storage.blob.aio as blob_aio
import httpx
import pytest
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError
from fastapi.testclient import TestClient

from matchlens.services import api

KEY = ("replays", "live/m00042-abc.ndjson")
FIRST = b'{"type":"tick","frames":[{"t_s":0.0}]}\n{"type":"tick","frames":[]}\n'
REAL_BLOB_CLIENT = blob_aio.BlobClient


class FakeBlobClient:
    blobs: dict = {}
    downloads = 0
    down = False

    def __init__(self, endpoint, container, name, credential=None, **config):
        self.key = (container, name)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _check(self):
        if FakeBlobClient.down:
            raise ServiceRequestError("storage unreachable")
        if self.key not in self.blobs:
            raise ResourceNotFoundError("BlobNotFound")

    async def get_blob_properties(self):
        self._check()
        return SimpleNamespace(size=len(self.blobs[self.key]))

    async def download_blob(self, offset=0, length=None):
        self._check()
        FakeBlobClient.downloads += 1
        await asyncio.sleep(0.05)  # long enough for a burst of joiners to overlap
        data = self.blobs[self.key][offset:None if length is None else offset + length]

        class Stream:
            async def readall(self):
                return data

        return Stream()


@pytest.fixture(autouse=True)
def storage(monkeypatch):
    monkeypatch.setattr(blob_aio, "BlobClient", FakeBlobClient)
    monkeypatch.setattr(FakeBlobClient, "blobs", {KEY: FIRST})
    monkeypatch.setattr(FakeBlobClient, "downloads", 0)
    monkeypatch.setattr(FakeBlobClient, "down", False)
    monkeypatch.setattr(api, "recording_cache", api.RecordingCache())
    monkeypatch.setattr(api.app.state, "cred", None, raising=False)


@pytest.fixture
def client():
    return TestClient(api.app)  # no `with`: the lifespan would connect to Azure


def test_recording_is_served_gzipped_and_never_cached(client):
    r = client.get("/api/matches/m00042-abc/recording")
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-store"
    assert r.content == FIRST  # the client inflates it
    with client.stream("GET", "/api/matches/m00042-abc/recording") as raw:
        assert gzip.decompress(b"".join(raw.iter_raw())) == FIRST


def test_recording_404s_for_a_match_without_one_and_rejects_bad_ids(client):
    assert client.get("/api/matches/nope/recording").status_code == 404
    assert client.get("/api/matches/bad.id/recording").status_code == 400


def test_a_recording_created_before_its_first_tick_is_still_missing(client):
    FakeBlobClient.blobs[KEY] = b""
    assert client.get("/api/matches/m00042-abc/recording").status_code == 404


def test_a_storage_failure_is_a_502_not_a_missing_recording(client):
    FakeBlobClient.down = True
    assert client.get("/api/matches/m00042-abc/recording").status_code == 502


async def test_a_burst_of_joiners_shares_one_download_and_new_ticks_are_never_missed():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://api") as c:
        burst = await asyncio.gather(*(c.get("/api/matches/m00042-abc/recording") for _ in range(10)))
        assert [r.content for r in burst] == [FIRST] * 10
        assert FakeBlobClient.downloads == 1

        FakeBlobClient.blobs[KEY] = FIRST + b'{"type":"tick","frames":[{"t_s":1.0}]}\n'
        later = await c.get("/api/matches/m00042-abc/recording")
        assert later.content == FakeBlobClient.blobs[KEY] and FakeBlobClient.downloads == 2
        assert (await c.get("/api/matches/m00042-abc/recording")).content == later.content
        assert FakeBlobClient.downloads == 2  # a recording that stopped growing is never fetched again


async def test_a_recording_bigger_than_one_get_downloads_while_ticks_keep_landing(monkeypatch):
    """The real SDK against a storage stub that appends a tick after every GET, as a live match does."""
    import re
    from functools import partial

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    tick = b'{"type":"tick","frames":[{"t_s":9.0}]}\n'
    blob = {"data": FIRST * 4, "version": 0}

    def headers(**extra):
        return {"ETag": f'"0x{blob["version"]}"', "Last-Modified": "Sun, 25 Oct 2026 12:00:00 GMT",
                "x-ms-blob-type": "AppendBlob", "x-ms-version": "2025-01-05", **extra}

    async def storage(request: web.Request):
        data = blob["data"]
        if request.method == "HEAD":
            return web.Response(headers=headers(**{"Content-Length": str(len(data))}))
        if request.headers.get("If-Match", "*") not in ("*", headers()["ETag"]):
            return web.Response(status=412, headers=headers(**{"x-ms-error-code": "ConditionNotMet"}))
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers["x-ms-range"]).groups())
        end = min(end, len(data) - 1)
        body = data[start:end + 1]
        response = web.Response(status=206, body=body,
                                headers=headers(**{"Content-Range": f"bytes {start}-{end}/{len(data)}"}))
        blob["data"], blob["version"] = data + tick, blob["version"] + 1
        return response

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", storage)
    async with TestServer(app) as server:
        endpoint = str(server.make_url("/devstoreaccount1"))
        monkeypatch.setattr(api, "settings", lambda: SimpleNamespace(storage_blob_endpoint=endpoint))
        monkeypatch.setattr(api, "RECORDING_RANGE", len(FIRST))
        # a recording several GETs long: the SDK's own ranges would be this small too
        monkeypatch.setattr(blob_aio, "BlobClient", partial(REAL_BLOB_CLIENT, max_single_get_size=len(FIRST),
                                                            max_chunk_get_size=len(FIRST)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://api") as c:
            r = await c.get("/api/matches/m00042-abc/recording")
    assert r.status_code == 200 and r.content == FIRST * 4


class FakeCues:
    def __init__(self, n: int):
        self.docs = [{"id": f"c{i}", "display_at_s": float(i), "_etag": "x"} for i in range(n)]

    async def query_items(self, sql, parameters=None, partition_key=None):
        for d in reversed(self.docs):  # newest first, as the query orders them
            yield d


def test_cue_history_is_the_newest_60_unless_more_are_asked_for(client, monkeypatch):
    monkeypatch.setattr(api.app.state, "db", {"cues": FakeCues(150)}, raising=False)
    times = lambda r: [c["display_at_s"] for c in r.json()["cues"]]  # noqa: E731
    assert times(client.get("/api/matches/m1/cues")) == [float(i) for i in range(90, 150)]
    assert times(client.get("/api/matches/m1/cues?limit=1000")) == [float(i) for i in range(150)]
    assert client.get("/api/matches/m1/cues?limit=5000").status_code == 422
