import gzip

import azure.storage.blob.aio as blob_aio
import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient

from matchlens.services import api

BLOBS = {("replays", "live/m00042-abc.ndjson"): b'{"type":"tick","frames":[{"t_s":0.0}]}\n{"type":"tick","frames":[]}\n'}


class FakeBlobClient:
    def __init__(self, endpoint, container, name, credential=None):
        self.key = (container, name)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def download_blob(self):
        if self.key not in BLOBS:
            raise ResourceNotFoundError("BlobNotFound")
        data = BLOBS[self.key]

        class Stream:
            async def readall(self):
                return data

        return Stream()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(blob_aio, "BlobClient", FakeBlobClient)
    monkeypatch.setattr(api.app.state, "cred", None, raising=False)
    return TestClient(api.app)  # no `with`: the lifespan would connect to Azure


def test_recording_is_served_gzipped_and_never_cached(client):
    r = client.get("/api/matches/m00042-abc/recording")
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-store"
    assert r.content == BLOBS[("replays", "live/m00042-abc.ndjson")]  # the client inflates it
    with client.stream("GET", "/api/matches/m00042-abc/recording") as raw:
        assert gzip.decompress(b"".join(raw.iter_raw())) == BLOBS[("replays", "live/m00042-abc.ndjson")]


def test_recording_404s_for_a_match_without_one_and_rejects_bad_ids(client):
    assert client.get("/api/matches/nope/recording").status_code == 404
    assert client.get("/api/matches/bad.id/recording").status_code == 400
