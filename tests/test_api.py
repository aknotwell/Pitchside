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


class FakeBlobClient:
    blobs: dict = {}
    downloads = 0
    down = False

    def __init__(self, endpoint, container, name, credential=None):
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

    async def download_blob(self):
        self._check()
        FakeBlobClient.downloads += 1
        await asyncio.sleep(0.05)  # long enough for a burst of joiners to overlap
        data = self.blobs[self.key]

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
