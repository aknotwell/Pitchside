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
    def __init__(self, store: dict, name: str, fail: bool = False, hang: bool = False):
        self.store, self.name, self.fail, self.hang = store, name, fail, hang

    async def append_block(self, data: bytes):
        import asyncio

        from azure.core.exceptions import ResourceNotFoundError

        if self.hang:
            await asyncio.Event().wait()  # an SDK stuck retrying
        if self.fail:
            raise OSError("storage is down")
        if self.name not in self.store:
            raise ResourceNotFoundError("BlobNotFound")
        self.store[self.name] += data

    async def create_append_blob(self, match_condition=None):
        import asyncio

        from azure.core.exceptions import ResourceModifiedError

        await asyncio.sleep(0.01)  # two first appends race here
        if self.name in self.store:
            raise ResourceModifiedError("ConditionNotMet")  # what IfMissing returns when the blob appeared
        self.store[self.name] = b""


class FakeContainer:
    def __init__(self, fail: bool = False, hang: bool = False):
        self.blobs: dict[str, bytes] = {}
        self.fail, self.hang = fail, hang

    def get_blob_client(self, name: str):
        return FakeAppendBlob(self.blobs, name, self.fail, self.hang)

    async def upsert_item(self, doc):
        return doc


def _emitter(recordings: FakeContainer, inflight: int = 0) -> tuple[InterpreterService, list]:
    svc = object.__new__(InterpreterService)
    svc.pending_frames, svc.pending_state, svc.last_tick = {}, {}, {}
    svc.inflight, svc.dropped_ticks, svc.bg = inflight, 0, set()
    svc.recording_inflight, svc.lost_records = 0, 0
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
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1_700_000_000 + now[0]))
    await svc._emit([(it, _out(0.0, 0.0))])
    now[0] += interp.TICK_EVERY_S
    await svc._emit([(it, _out(0.2, 0.2))])
    await asyncio.gather(*svc.bg)

    lines = _lines(rec)
    assert [ln["frames"][0]["t_s"] for ln in lines] == [0.0, 0.2]
    assert lines[0] == published[0][1] and lines[0]["type"] == "tick" and "meta" not in lines[0]["state"]
    assert [ln["wallMs"] for ln in lines] == [1_700_000_100_000, 1_700_000_101_000]


async def test_recording_survives_a_replica_restart_and_backpressure(monkeypatch):
    """A new replica finds the blob already there; a dropped live publish is still recorded."""
    import asyncio

    import matchlens.services.interpreter as interp

    rec = FakeContainer()
    rec.blobs["live/m1.ndjson"] = b'{"type":"tick","frames":[]}\n'
    svc, published = _emitter(rec, inflight=interp.MAX_INFLIGHT_PUBLISHES)
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0, time=lambda: 1_700_000_050.0))
    await svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0))])
    await asyncio.gather(*svc.bg)

    assert published == [] and svc.dropped_ticks == 1
    assert [len(ln["frames"]) for ln in _lines(rec)] == [0, 1]


async def test_a_recording_failure_never_stops_the_live_feed(monkeypatch):
    import asyncio

    import matchlens.services.interpreter as interp

    svc, published = _emitter(FakeContainer(fail=True))
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0, time=lambda: 1_700_000_050.0))
    await svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0))])
    await asyncio.gather(*svc.bg)

    assert [p[1]["frames"][0]["t_s"] for p in published] == [30.0]


async def test_two_first_appends_racing_to_create_the_blob_both_land(monkeypatch):
    import asyncio

    import matchlens.services.interpreter as interp

    rec = FakeContainer()
    svc, _ = _emitter(rec)
    now = [100.0]
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1_700_000_000 + now[0]))
    it = MatchInterpreter("m1")
    await svc._emit([(it, _out(0.0, 0.0))])
    now[0] += interp.TICK_EVERY_S
    await svc._emit([(it, _out(0.2, 0.2))])
    await asyncio.gather(*svc.bg)

    assert svc.lost_records == 0
    assert sorted(ln["frames"][0]["t_s"] for ln in _lines(rec)) == [0.0, 0.2]


async def test_a_stuck_recording_never_holds_up_the_batch(monkeypatch):
    """The batch loop (and so checkpointing) moves on at once; the append is abandoned after its timeout."""
    import asyncio

    import matchlens.services.interpreter as interp

    svc, published = _emitter(FakeContainer(hang=True))
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0, time=lambda: 1_700_000_050.0))
    monkeypatch.setattr(interp, "RECORD_TIMEOUT_S", 0.05)
    await asyncio.wait_for(svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0))]), timeout=0.02)
    await asyncio.gather(*svc.bg)

    assert [p[1]["frames"][0]["t_s"] for p in published] == [30.0]
    assert svc.lost_records == 1 and svc.recording_inflight == 0


async def test_recordings_past_the_in_flight_cap_are_dropped_and_counted(monkeypatch):
    import asyncio

    import matchlens.services.interpreter as interp

    rec = FakeContainer()
    svc, published = _emitter(rec)
    svc.recording_inflight = interp.MAX_INFLIGHT_RECORDINGS
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0, time=lambda: 1_700_000_050.0))
    await svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0))])
    await asyncio.gather(*svc.bg)

    assert rec.blobs == {} and svc.lost_records == 1
    assert [p[1]["frames"][0]["t_s"] for p in published] == [30.0]


async def test_shutdown_lets_recording_appends_in_flight_land(monkeypatch):
    """Checkpoints run ahead of the background appends, so one dropped at shutdown is lost for good."""
    import signal
    from unittest.mock import MagicMock

    import azure.cosmos.aio
    import azure.eventhub.aio
    import azure.eventhub.extensions.checkpointstoreblobaio
    import azure.messaging.webpubsubservice.aio
    import azure.servicebus.aio
    import azure.storage.blob.aio

    import matchlens.services.interpreter as interp

    rec = FakeContainer()
    svc = interp.InterpreterService()

    class Consumer:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def close(self):
            pass

        async def receive_batch(self, **kwargs):
            svc._record_nowait("m1", {"type": "tick", "frames": []})  # then SIGTERM closes the consumer

    for module, name in ((azure.cosmos.aio, "CosmosClient"), (azure.servicebus.aio, "ServiceBusClient"),
                         (azure.messaging.webpubsubservice.aio, "WebPubSubServiceClient"),
                         (azure.eventhub.extensions.checkpointstoreblobaio, "BlobCheckpointStore")):
        monkeypatch.setattr(module, name, MagicMock())
    monkeypatch.setattr(azure.eventhub.aio, "EventHubConsumerClient", Consumer)
    monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient",
                        lambda *a, **k: SimpleNamespace(get_container_client=lambda name: rec))
    monkeypatch.setattr(interp, "async_credential", MagicMock)
    monkeypatch.setattr(interp, "signal", SimpleNamespace(SIGTERM=signal.SIGUSR1, SIGINT=signal.SIGUSR2))
    monkeypatch.delenv("EVENTGRID_ENDPOINT", raising=False)

    await svc.start()

    assert _lines(rec, "m1") == [{"type": "tick", "frames": []}]


async def test_publishes_started_in_one_batch_count_toward_the_cap(monkeypatch):
    import asyncio

    import matchlens.services.interpreter as interp

    svc, published = _emitter(FakeContainer(), inflight=interp.MAX_INFLIGHT_PUBLISHES - 1)
    monkeypatch.setattr(interp, "time", SimpleNamespace(monotonic=lambda: 50.0, time=lambda: 1_700_000_050.0))
    await svc._emit([(MatchInterpreter("m1"), _out(30.0, 30.0)), (MatchInterpreter("m2"), _out(30.0, 30.0))])
    await asyncio.gather(*svc.bg)

    assert len(published) == 1 and svc.dropped_ticks == 1
