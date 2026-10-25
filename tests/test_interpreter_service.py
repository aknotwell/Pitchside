from types import SimpleNamespace

from matchlens.interpret.state import MatchInterpreter, Output
from matchlens.services.interpreter import InterpreterService


def _service() -> InterpreterService:
    svc = object.__new__(InterpreterService)  # no Azure clients needed for partition bookkeeping
    svc.matches, svc.match_partition, svc.last_checkpoint = {}, {}, {}
    return svc


def test_partition_handover_drops_stale_match_state():
    """Regression: a replica that lost a partition and later re-claimed it kept its old in-memory
    state, resumed from a newer checkpoint, and silently lost every event processed in between."""
    svc = _service()
    for mid, pid in (("m1", "0"), ("m2", "0"), ("m3", "1")):
        svc.matches[mid] = MatchInterpreter(mid)
        svc.match_partition[mid] = pid
    svc.last_checkpoint.update({"0": 1.0, "1": 2.0})

    assert svc.forget_partition("0") == 2
    assert set(svc.matches) == {"m3"} and set(svc.match_partition) == {"m3"}
    assert "0" not in svc.last_checkpoint and "1" in svc.last_checkpoint
    assert svc.forget_partition("0") == 0


class FakeAppendBlob:
    def __init__(self, store: dict, name: str, fail: bool = False):
        self.store, self.name, self.fail = store, name, fail

    async def append_block(self, data: bytes):
        from azure.core.exceptions import ResourceNotFoundError

        if self.fail:
            raise OSError("storage is down")
        if self.name not in self.store:
            raise ResourceNotFoundError("BlobNotFound")
        self.store[self.name] += data

    async def create_append_blob(self, match_condition=None):
        from azure.core.exceptions import ResourceExistsError

        if self.name in self.store:
            raise ResourceExistsError("BlobAlreadyExists")
        self.store[self.name] = b""


class FakeContainer:
    def __init__(self, fail: bool = False):
        self.blobs: dict[str, bytes] = {}
        self.fail = fail

    def get_blob_client(self, name: str):
        return FakeAppendBlob(self.blobs, name, self.fail)

    async def upsert_item(self, doc):
        return doc


def _emitter(recordings: FakeContainer, inflight: int = 0) -> tuple[InterpreterService, list]:
    svc = object.__new__(InterpreterService)
    svc.pending_frames, svc.pending_state, svc.last_tick = {}, {}, {}
    svc.inflight, svc.dropped_ticks, svc.bg = inflight, 0, set()
    svc.state, svc.recordings, svc.eventgrid = FakeContainer(), recordings, None
    published = []

    async def send_to_group(group, payload, content_type):
        published.append((group, payload))

    svc.wps = type("WPS", (), {"send_to_group": staticmethod(send_to_group)})()
    return svc, published


def _out(t: float, clock: float) -> Output:
    return Output(frames={"frames": [{"t_s": t, "ball": [0, 0, 0], "players": []}]},
                  state={"id": "m1", "clockS": clock, "ended": False, "meta": {"big": True}})


def _lines(container: FakeContainer, mid: str = "m1") -> list[dict]:
    import json

    return [json.loads(x) for x in container.blobs[f"live/{mid}.ndjson"].decode().splitlines()]


async def test_every_tick_is_appended_to_the_match_recording(monkeypatch):
    import asyncio

    import matchlens.services.interpreter as interp

    rec = FakeContainer()
    svc, published = _emitter(rec)
    it = MatchInterpreter("m1")
    now = [100.0]
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: now[0]))
    await svc._emit([(it, _out(0.0, 0.0))])
    now[0] += interp.TICK_EVERY_S
    await svc._emit([(it, _out(0.2, 0.2))])
    await asyncio.gather(*svc.bg)

    lines = _lines(rec)
    assert [ln["frames"][0]["t_s"] for ln in lines] == [0.0, 0.2]
    assert lines[0] == published[0][1] and lines[0]["type"] == "tick" and "meta" not in lines[0]["state"]


async def test_recording_survives_a_replica_restart_and_backpressure(monkeypatch):
    """A new replica finds the blob already there; a dropped live publish is still recorded."""
    import matchlens.services.interpreter as interp

    rec = FakeContainer()
    rec.blobs["live/m1.ndjson"] = b'{"type":"tick","frames":[]}\n'
    svc, published = _emitter(rec, inflight=interp.MAX_INFLIGHT_PUBLISHES)
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0))
    await svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0))])

    assert published == [] and svc.dropped_ticks == 1
    assert [len(ln["frames"]) for ln in _lines(rec)] == [0, 1]


async def test_a_recording_failure_never_stops_the_live_feed(monkeypatch):
    import asyncio

    import matchlens.services.interpreter as interp

    svc, published = _emitter(FakeContainer(fail=True))
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0))
    await svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0))])
    await asyncio.gather(*svc.bg)

    assert [p[1]["frames"][0]["t_s"] for p in published] == [30.0]
