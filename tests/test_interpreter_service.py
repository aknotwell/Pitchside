import asyncio
import pickle
from types import SimpleNamespace

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from matchlens.contracts import StreamMessage
from matchlens.interpret.state import MatchInterpreter, Output
from matchlens.services import interpreter as svc_mod
from matchlens.services.interpreter import SNAPSHOT_VERSION, InterpreterService


def _service() -> InterpreterService:
    svc = object.__new__(InterpreterService)  # no Azure clients needed for bookkeeping
    svc.matches, svc.match_partition, svc.last_checkpoint = {}, {}, {}
    svc.pending_frames, svc.pending_state, svc.last_tick = {}, {}, {}
    svc.dirty, svc.failed, svc.consumer = set(), False, None
    svc.inflight, svc.dropped_ticks, svc.bg = 0, 0, set()
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


# ---------------------------------------------------------------- snapshots (#37)
class _Blob:
    def __init__(self, store, name, error=None):
        self.store, self.name, self.error = store, name, error

    async def download_blob(self):
        if self.error:
            raise self.error
        if self.name not in self.store:
            raise ResourceNotFoundError("missing")
        data = self.store[self.name]

        class _D:
            async def readall(self):
                return data
        return _D()

    async def upload_blob(self, data, overwrite):
        self.store[self.name] = data


class _Snapshots:
    def __init__(self, error=None):
        self.store, self.error, self.writes = {}, error, 0

    def get_blob_client(self, name):
        blob = _Blob(self.store, name, self.error)
        orig = blob.upload_blob

        async def upload(data, overwrite):
            self.writes += 1
            await orig(data, overwrite)
        blob.upload_blob = upload
        return blob


def test_missing_snapshot_is_a_new_match():
    svc = _service()
    svc.snapshots = _Snapshots()
    it = asyncio.run(svc._load("m1", "0", 10))
    assert it.match_id == "m1" and it.clock == 0


def test_transient_snapshot_error_is_raised_not_reset():
    """Regression: a blob 500 used to return a fresh 0-0 interpreter that overwrote the live state doc."""
    svc = _service()
    svc.snapshots = _Snapshots(error=HttpResponseError("500 server busy"))
    with pytest.raises(HttpResponseError):
        asyncio.run(svc._load("m1", "0", 10))


def test_snapshot_round_trip_and_incompatible_snapshot_is_rebuilt(monkeypatch):
    svc = _service()
    svc.snapshots = _Snapshots()
    it = MatchInterpreter("m1")
    it.clock = 1234.0
    asyncio.run(svc._save(it))
    assert asyncio.run(svc._load("m1", "0", 10)).clock == 1234.0

    rebuilt = MatchInterpreter("m1")
    rebuilt.clock = 999.0
    calls = []

    async def fake_rebuild(mid, pid, seq):
        calls.append((mid, pid, seq))
        return rebuilt
    monkeypatch.setattr(svc, "_rebuild", fake_rebuild)
    svc.snapshots.store["snapshots/m1.pkl"] = pickle.dumps(it)  # a pre-versioning snapshot
    assert asyncio.run(svc._load("m1", "0", 10)) is rebuilt
    svc.snapshots.store["snapshots/m1.pkl"] = pickle.dumps((SNAPSHOT_VERSION + 1, it))
    assert asyncio.run(svc._load("m1", "0", 11)) is rebuilt
    assert calls == [("m1", "0", 10), ("m1", "0", 11)]


# ---------------------------------------------------------------- at-least-once outputs (#36)
class _Container:
    def __init__(self, fail_times=0):
        self.items, self.fail_times = [], fail_times

    async def _maybe_fail(self):
        if self.fail_times:
            self.fail_times -= 1
            raise HttpResponseError("429 too many requests")

    async def create_item(self, doc):
        await self._maybe_fail()
        self.items.append(doc)

    async def upsert_item(self, doc):
        await self._maybe_fail()
        self.items.append(doc)


class _Sender:
    def __init__(self, fail_times=0):
        self.sent, self.fail_times = [], fail_times

    async def send_messages(self, msgs):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("service bus blip")
        self.sent.extend(msgs)


def _wired(sender_fails=0) -> InterpreterService:
    svc = _service()
    svc.cues, svc.moments, svc.state = _Container(), _Container(), _Container()
    svc.sender, svc.eventgrid = _Sender(sender_fails), None
    svc.snapshots = _Snapshots()
    svc._publish_nowait = lambda group, payload: None
    return svc


@pytest.fixture
def sample_moment():
    from matchlens.contracts import Moment

    return Moment(moment_id="mo-1", match_id="m1", kind="goal", clock_s=10.0, period=1, headline="Goal",
                  evidence=[])


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(svc_mod, "EMIT_BACKOFF_S", 0.0)


def test_failed_send_is_retried_and_delivered(sample_moment):
    svc = _wired(sender_fails=1)
    it = MatchInterpreter(sample_moment.match_id)
    asyncio.run(svc._emit([(it, Output(moments=[sample_moment]))]))
    assert [m.message_id for m in svc.sender.sent] == [sample_moment.moment_id]


def test_persistent_failure_raises(sample_moment):
    svc = _wired(sender_fails=99)
    it = MatchInterpreter(sample_moment.match_id)
    with pytest.raises(RuntimeError, match="outputs failed"):
        asyncio.run(svc._emit([(it, Output(moments=[sample_moment]))]))


class _Ctx:
    def __init__(self, pid="0"):
        self.partition_id, self.checkpoints = pid, []

    async def update_checkpoint(self, ev):
        self.checkpoints.append(ev)


def _event(msg: StreamMessage, seq: int):
    return SimpleNamespace(body_as_str=lambda: msg.model_dump_json(), enqueued_time=None, sequence_number=seq)


def _meta_msg(mid="m1"):
    from matchlens.sim.engine import simulate

    r = simulate(3, match_id=mid, params={"period_min": 1})
    return StreamMessage(kind="metadata", match_id=mid, clock_s=0, payload=r.metadata.model_dump(mode="json"))


def test_failed_batch_does_not_checkpoint_and_stops_the_consumer():
    svc = _wired()
    svc.state = _Container(fail_times=99)
    closed = []

    class _Consumer:
        async def close(self):
            closed.append(True)
    svc.consumer = _Consumer()
    ctx = _Ctx()

    async def go():
        await svc.on_batch(ctx, [_event(_meta_msg(), 1)])
        await asyncio.sleep(0)
        await svc.on_batch(ctx, [_event(_meta_msg(), 2)])  # later batches are ignored too
    asyncio.run(go())
    assert svc.failed and closed == [True] and ctx.checkpoints == []


# ---------------------------------------------------------------- snapshot only what changed (#50)
def test_idle_matches_are_not_snapshotted_and_finished_ones_are_forgotten(monkeypatch):
    svc = _wired()
    ctx = _Ctx()
    clock = [100.0]
    monkeypatch.setattr(svc_mod.time, "monotonic", lambda: clock[0])

    asyncio.run(svc._on_batch(ctx, [_event(_meta_msg("m1"), 1), _event(_meta_msg("m2"), 2)]))
    assert svc.snapshots.writes == 2 and len(ctx.checkpoints) == 1 and not svc.dirty

    clock[0] += 10  # only m2 moves on: m1 is idle
    end = StreamMessage(kind="end", match_id="m2", clock_s=60, payload={})
    asyncio.run(svc._on_batch(ctx, [_event(end, 3)]))
    assert svc.snapshots.writes == 3 and len(ctx.checkpoints) == 2
    assert set(svc.matches) == {"m1"} and set(svc.match_partition) == {"m1"}
    for d in (svc.pending_frames, svc.pending_state, svc.last_tick):
        assert "m2" not in d


def test_rebuild_replays_only_this_match_up_to_the_current_event(monkeypatch):
    import azure.eventhub.aio as eh

    svc = _service()
    svc.s, svc.cred = SimpleNamespace(eventhub_fqdn="x", eventhub_name="y"), None
    stream = [_event(_meta_msg("m1"), 0), _event(_meta_msg("other"), 1),
              _event(StreamMessage(kind="end", match_id="m1", clock_s=60, payload={}), 2),
              _event(StreamMessage(kind="end", match_id="m1", clock_s=60, payload={}), 5)]

    class _Client:
        def __init__(self, **kw):
            assert kw["consumer_group"] == svc_mod.REBUILD_CONSUMER_GROUP
            self.closed = asyncio.Event()

        async def receive_batch(self, on_event_batch, partition_id, **kw):
            assert partition_id == "3" and kw["starting_position"] == "-1"
            await on_event_batch(None, stream)
            await self.closed.wait()

        async def close(self):
            self.closed.set()
    monkeypatch.setattr(eh, "EventHubConsumerClient", _Client)

    it = asyncio.run(svc._rebuild("m1", "3", 5))
    assert it.meta is not None and it.ended
