import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from matchlens.services.storyteller import StorytellerService


# ---------------------------------------------------------------- storyteller roster (#45)
class _State:
    def __init__(self, docs):
        self.docs = list(docs)  # one response per read

    async def read_item(self, item, partition_key):
        doc = self.docs.pop(0)
        if isinstance(doc, Exception):
            raise doc
        return doc


def _doc(*names):
    return {"meta": {"home": {"players": [{"name": n} for n in names]}, "away": {"players": []}}}


def _svc(*responses) -> StorytellerService:
    svc = object.__new__(StorytellerService)
    svc.rosters = {}
    svc.state = _State(responses)
    return svc


def test_empty_roster_is_not_cached_and_the_next_read_fills_it():
    """Regression: an empty first read was cached for the whole match, so the verifier
    could no longer reject explanations naming the wrong player."""
    svc = _svc({"meta": None}, ResourceNotFoundError("not yet"), _doc("Ana Ruiz", "Kofi Mensah"))
    assert asyncio.run(svc._roster("m1")) is False and "m1" not in svc.rosters
    assert asyncio.run(svc._roster("m1")) is False and "m1" not in svc.rosters
    assert asyncio.run(svc._roster("m1")) is True
    assert svc.rosters["m1"] == {"Ana Ruiz", "Kofi Mensah"}
    assert asyncio.run(svc._roster("m1")) is True  # cached: no further reads


# ---------------------------------------------------------------- cue publisher (#44)
@pytest.fixture
def publisher(monkeypatch):
    """Import the Functions app with a stand-in azure.functions (not installed in the dev env)."""
    fake = types.ModuleType("azure.functions")

    class FunctionApp:
        def __getattr__(self, _name):
            return lambda *a, **k: (lambda f: f)
    fake.FunctionApp = FunctionApp
    fake.DocumentList = list
    monkeypatch.setitem(sys.modules, "azure.functions", fake)
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "functions" / "cue_publisher"))
    sys.modules.pop("function_app", None)
    mod = importlib.import_module("function_app")
    monkeypatch.setattr(mod, "SEND_BACKOFF_S", 0.0)
    return mod


def _http_error(code):
    err = HttpResponseError(f"{code}")
    err.status_code = code
    return err


class _Send:
    def __init__(self, failures):
        self.failures, self.sent = dict(failures), []

    def __call__(self, group, payload, content_type):
        cid = payload["cue"]["id"]
        left = self.failures.get(cid)
        if left:
            err, n = left
            if n:
                self.failures[cid] = (err, n - 1)
                raise err
        self.sent.append(cid)


CUES = [{"id": "c1", "matchId": "m1", "persona": "casual", "lang": "en"},
        {"id": "c2", "matchId": "m1", "persona": "all", "lang": "all"}]


def test_transient_send_failure_is_retried_and_delivered(publisher):
    send = _Send({"c1": (_http_error(429), 2)})
    assert publisher.publish(CUES, send) == 2
    assert send.sent == ["c1", "c2"]


def test_persistent_transient_failure_fails_the_batch_for_a_runtime_retry(publisher):
    send = _Send({"c1": (_http_error(503), 99)})
    with pytest.raises(RuntimeError, match="not delivered"):
        publisher.publish(CUES, send)
    assert send.sent == ["c2"]  # the rest of the batch still went out


def test_rejected_cue_is_skipped_not_retried(publisher):
    send = _Send({"c1": (_http_error(400), 99)})
    assert publisher.publish(CUES, send) == 1
