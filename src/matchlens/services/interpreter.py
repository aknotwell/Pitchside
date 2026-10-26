"""Interpreter: consumes ``match-stream`` and turns it into state, cues, moments and live frames.

* Event Hubs consumer group ``interpreter``; checkpoints in Blob (KEDA scales on the lag).
* Per-match state is a pure function of the stream. Before each checkpoint we snapshot every
  match that changed to Blob, so a new replica resumes exactly where the old one stopped. Anything
  replayed after a crash is de-duplicated (event ids, tracking timestamps, deterministic cue ids).
* At least once: cues, moments, state docs and lifecycle events are retried, and if one still
  fails the replica stops without checkpointing, so the restarted one reads the batch again.
* Hot path: template cues are written straight to Cosmos ``cues`` (the change feed fans them out).
* Warm path: moments go to Service Bus (session = match id, so each match is explained in order).
* Every live tick is also appended to the match's recording, so late joiners can scrub back to kick-off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import signal
import time

from opentelemetry import trace

from matchlens.azure_io.common import async_credential, create_once, settings, setup_telemetry
from matchlens.contracts import RECORDINGS_CONTAINER, StreamMessage, frames_group, recording_blob
from matchlens.interpret.state import MatchInterpreter, Output

log = logging.getLogger("matchlens.interpreter")
tracer = trace.get_tracer("matchlens.interpreter")

SNAPSHOT_EVERY_S = 5.0
TICK_EVERY_S = 1.0  # one coalesced Web PubSub message per match per second
MAX_FRAMES_PER_TICK = 15
MAX_INFLIGHT_PUBLISHES = 64  # live ticks are ephemeral: drop them rather than slow the stream down
PUBLISH_TIMEOUT_S = 3.0
MAX_INFLIGHT_RECORDINGS = 64  # past this, ticks go unrecorded (and counted) rather than hold up the batch loop
RECORD_TIMEOUT_S = 5.0
SNAPSHOT_VERSION = 1  # bump when MatchInterpreter changes shape; older snapshots are rebuilt from the stream
EMIT_ATTEMPTS = 3
EMIT_BACKOFF_S = 0.5
REBUILD_CONSUMER_GROUP = "interpreter-rebuild"
REBUILD_TIMEOUT_S = 120.0
FINISHED_STATE_TTL_S = 30 * 86400  # cues, moments and runs keep 31 days (infra/modules/data.bicep)


class InterpreterService:
    def __init__(self):
        self.s = settings()
        self.matches: dict[str, MatchInterpreter] = {}
        self.match_partition: dict[str, str] = {}
        self.last_checkpoint: dict[str, float] = {}
        self.pending_frames: dict[str, list] = {}
        self.pending_state: dict[str, dict] = {}
        self.last_tick: dict[str, float] = {}
        self.dirty: set[str] = set()  # matches with events since their last snapshot
        self.failed = False
        self.consumer = None
        self.inflight = 0
        self.dropped_ticks = 0
        self.recording_inflight = 0
        self.lost_records = 0
        self.bg: set[asyncio.Task] = set()

    async def start(self) -> None:
        from azure.cosmos.aio import CosmosClient
        from azure.eventhub.aio import EventHubConsumerClient
        from azure.eventhub.extensions.checkpointstoreblobaio import BlobCheckpointStore
        from azure.messaging.webpubsubservice.aio import WebPubSubServiceClient
        from azure.servicebus.aio import ServiceBusClient
        from azure.storage.blob.aio import BlobServiceClient

        s = self.s
        self.cred = async_credential()
        self.cosmos = CosmosClient(s.cosmos_endpoint, credential=self.cred)
        db = self.cosmos.get_database_client(s.cosmos_database)
        self.cues = db.get_container_client("cues")
        self.state = db.get_container_client("matchState")
        self.moments = db.get_container_client("moments")
        self.sb = ServiceBusClient(s.servicebus_fqdn, credential=self.cred)
        self.sender = self.sb.get_queue_sender(s.servicebus_queue)
        self.wps = WebPubSubServiceClient(endpoint=s.webpubsub_endpoint, hub=s.webpubsub_hub, credential=self.cred)
        self.blobs = BlobServiceClient(s.storage_blob_endpoint, credential=self.cred)
        self.eventgrid = None
        if os.environ.get("EVENTGRID_ENDPOINT"):
            from azure.eventgrid.aio import EventGridPublisherClient

            self.eventgrid = EventGridPublisherClient(os.environ["EVENTGRID_ENDPOINT"], self.cred)
        self.snapshots = self.blobs.get_container_client(s.checkpoint_container)
        self.recordings = self.blobs.get_container_client(RECORDINGS_CONTAINER)
        store = BlobCheckpointStore(blob_account_url=s.storage_blob_endpoint, container_name=s.checkpoint_container,
                                    credential=self.cred)
        consumer = EventHubConsumerClient(fully_qualified_namespace=s.eventhub_fqdn, eventhub_name=s.eventhub_name,
                                          consumer_group=s.eventhub_consumer_group, credential=self.cred,
                                          checkpoint_store=store, load_balancing_interval=5,
                                          partition_ownership_expiration_interval=20)
        self.consumer = consumer
        log.info("interpreter consuming %s/%s as %s", s.eventhub_fqdn, s.eventhub_name, s.eventhub_consumer_group)
        loop = asyncio.get_running_loop()
        # Scale-in and redeploys send SIGTERM: close the consumer so partition ownership is released
        # immediately instead of waiting for it to expire.
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(consumer.close()))
        async with consumer, self.sender:
            await consumer.receive_batch(
                on_event_batch=self.on_batch, on_error=self.on_error,
                on_partition_initialize=self.on_partition_initialize, on_partition_close=self.on_partition_close,
                max_batch_size=400, max_wait_time=0.5, starting_position="-1")
            if self.bg:  # checkpoints run ahead of recording appends; asyncio.run would cancel these
                await asyncio.wait(self.bg, timeout=RECORD_TIMEOUT_S)
        if self.failed:
            raise SystemExit(1)  # Container Apps restarts us; the new process resumes from the last checkpoint

    async def on_error(self, partition_context, error) -> None:
        pid = getattr(partition_context, "partition_id", None)
        log.warning("event hub error (partition %s): %s: %s", pid, type(error).__name__, error)

    async def on_partition_initialize(self, partition_context) -> None:
        dropped = self.forget_partition(partition_context.partition_id)
        log.info("claimed partition %s (dropped %d stale in-memory matches)", partition_context.partition_id, dropped)

    async def on_partition_close(self, partition_context, reason) -> None:
        dropped = self.forget_partition(partition_context.partition_id)
        log.info("released partition %s (%s; dropped %d in-memory matches)", partition_context.partition_id, reason, dropped)

    def forget_partition(self, pid: str) -> int:
        """Drop in-memory state for every match on a partition we no longer own (or are re-claiming).

        While another replica owned the partition it processed more events and saved newer snapshots.
        Keeping our old copy would resume from the latest checkpoint with stale state, silently losing
        everything in between and overwriting the correct state doc. The next event reloads the snapshot.
        """
        mids = [mid for mid, p in self.match_partition.items() if p == pid]
        for mid in mids:
            self._drop(mid)
        self.last_checkpoint.pop(pid, None)
        return len(mids)

    def _drop(self, match_id: str) -> None:
        for d in (self.matches, self.match_partition, self.pending_frames, self.pending_state, self.last_tick):
            d.pop(match_id, None)
        self.dirty.discard(match_id)

    # ------------------------------------------------------------ snapshots
    def _snapshot_name(self, match_id: str) -> str:
        return f"snapshots/{match_id}.pkl"

    async def _load(self, match_id: str, pid: str, upto_seq: int) -> MatchInterpreter:
        """Restore a match we haven't seen on this replica. Only a missing snapshot means a new match:
        any other read error propagates (the batch fails and is read again after a restart), and a
        snapshot we can no longer unpickle is rebuilt from the stream. Never silently restart at 0-0."""
        from azure.core.exceptions import ResourceNotFoundError

        blob = self.snapshots.get_blob_client(self._snapshot_name(match_id))
        try:
            data = await (await blob.download_blob()).readall()
        except ResourceNotFoundError:
            return MatchInterpreter(match_id)
        try:
            version, it = pickle.loads(data)  # our own snapshot from our own private container
            if version != SNAPSHOT_VERSION:
                raise ValueError(f"snapshot version {version}, this build reads {SNAPSHOT_VERSION}")
        except Exception as exc:
            log.error("snapshot of %s is unreadable (%s: %s); rebuilding it from the stream",
                      match_id, type(exc).__name__, exc)
            return await self._rebuild(match_id, pid, upto_seq)
        log.info("restored %s from snapshot at clock %.0f s", match_id, it.clock)
        return it

    async def _rebuild(self, match_id: str, pid: str, upto_seq: int) -> MatchInterpreter:
        """Re-read the partition from its start (retention is a day, longer than any match) up to the
        event being processed, feeding this match's messages to a fresh interpreter. The outputs
        already aired, so they are discarded. Uses its own consumer group so the epoch receiver
        that owns the partition isn't disturbed."""
        from azure.eventhub.aio import EventHubConsumerClient

        it = MatchInterpreter(match_id)
        done = asyncio.Event()

        async def on_batch(_ctx, events) -> None:
            for ed in events:
                if done.is_set() or ed.sequence_number >= upto_seq:
                    done.set()
                    return
                try:
                    msg = StreamMessage.model_validate_json(ed.body_as_str())
                except Exception:
                    continue
                if msg.match_id == match_id:
                    it.handle(msg)

        s = self.s
        client = EventHubConsumerClient(fully_qualified_namespace=s.eventhub_fqdn, eventhub_name=s.eventhub_name,
                                        consumer_group=REBUILD_CONSUMER_GROUP, credential=self.cred)
        reader = asyncio.create_task(client.receive_batch(on_event_batch=on_batch, partition_id=pid,
                                                          starting_position="-1", max_batch_size=1000,
                                                          max_wait_time=1))
        try:
            await asyncio.wait_for(done.wait(), REBUILD_TIMEOUT_S)
        finally:
            await client.close()
            await asyncio.gather(reader, return_exceptions=True)
        log.warning("rebuilt %s from partition %s up to sequence %d (clock %.0f s)", match_id, pid, upto_seq, it.clock)
        return it

    async def _save(self, it: MatchInterpreter) -> None:
        blob = self.snapshots.get_blob_client(self._snapshot_name(it.match_id))
        await blob.upload_blob(pickle.dumps((SNAPSHOT_VERSION, it)), overwrite=True)

    # ------------------------------------------------------------ processing
    async def on_batch(self, partition_context, events) -> None:
        """Never let an exception escape: the SDK would stop this partition's receive loop while
        the load balancer keeps renewing its ownership, silently stalling the partition.

        The receiver can't rewind, so a failed batch stops the whole consumer before anything else
        is checkpointed. The process exits, Container Apps restarts it, and every partition resumes
        from its last checkpoint and snapshot, reading the failed batch again."""
        try:
            await self._on_batch(partition_context, events)
        except Exception:
            log.exception("batch failed on partition %s; restarting from the last checkpoint",
                          partition_context.partition_id)
            self.failed = True
            if self.consumer is not None:
                asyncio.ensure_future(self.consumer.close())  # awaiting here would wait on this very callback

    async def _on_batch(self, partition_context, events) -> None:
        if not events or self.failed:
            return
        pid = partition_context.partition_id
        outputs: list[tuple[MatchInterpreter, Output]] = []
        with tracer.start_as_current_span("interpreter.batch", attributes={"partition": pid, "events": len(events)}):
            for ed in events:
                try:
                    msg = StreamMessage.model_validate_json(ed.body_as_str())
                except Exception as exc:
                    log.warning("skipping malformed message: %s", exc)
                    continue
                it = self.matches.get(msg.match_id)
                if it is None:
                    it = (await self._load(msg.match_id, pid, ed.sequence_number) if msg.kind != "metadata"
                          else MatchInterpreter(msg.match_id))
                    self.matches[msg.match_id] = it
                    self.match_partition[msg.match_id] = pid
                self.dirty.add(msg.match_id)
                out = it.handle(msg)
                if out.cues or out.moments:
                    enq = ed.enqueued_time.timestamp() * 1000 if ed.enqueued_time else None
                    for cue in out.cues:
                        cue.data["enqueuedAtMs"] = enq  # for event -> screen latency
                    for m in out.moments:
                        m.enqueued_at_ms = enq
                outputs.append((it, out))
            await self._emit(outputs)
            now = time.monotonic()
            if now - self.last_checkpoint.get(pid, 0) >= SNAPSHOT_EVERY_S:
                changed = [it for mid, it in self.matches.items()
                           if self.match_partition.get(mid) == pid and mid in self.dirty]
                await asyncio.gather(*(self._save(it) for it in changed))
                await partition_context.update_checkpoint(events[-1])
                self.last_checkpoint[pid] = now
                self.dirty.difference_update(it.match_id for it in changed)
                for it in changed:
                    if it.ended:
                        self._drop(it.match_id)

    async def _emit(self, outputs: list[tuple[MatchInterpreter, Output]]) -> None:
        from azure.servicebus import ServiceBusMessage

        tasks = []  # zero-argument factories, so a failed write can be retried with a fresh request
        sb_msgs: list[tuple[str, str, str, str]] = []
        latest_state: dict[str, dict] = {}
        latest_frames: dict[str, list] = {}
        for it, out in outputs:
            for cue in out.cues:
                tasks.append(lambda doc=cue.to_doc(): create_once(self.cues, doc))
            for m in out.moments:
                doc = {"id": m.moment_id, "matchId": m.match_id, **m.model_dump(mode="json")}
                tasks.append(lambda doc=doc: self.moments.upsert_item(doc))
                sb_msgs.append((m.model_dump_json(), m.match_id, m.moment_id, m.kind))
            if out.state is not None:
                latest_state[it.match_id] = out.state
            if out.frames is not None:
                latest_frames.setdefault(it.match_id, []).extend(out.frames["frames"])
        for mid, st in latest_state.items():
            if st.get("ended"):
                st = {**st, "ttl": FINISHED_STATE_TTL_S}  # live ones never expire; finished ones go after 30 days
            tasks.append(lambda st=st: self.state.upsert_item(st))
            self.pending_state[mid] = {k: v for k, v in st.items() if k != "meta"}
        for mid, frames in latest_frames.items():
            self.pending_frames.setdefault(mid, []).extend(frames)
        now = time.monotonic()
        for mid in set(self.pending_frames) | set(self.pending_state):
            ended = bool(self.pending_state.get(mid, {}).get("ended"))
            if now - self.last_tick.get(mid, 0.0) < TICK_EVERY_S and not ended:
                continue
            frames = self.pending_frames.pop(mid, [])
            if len(frames) > MAX_FRAMES_PER_TICK:  # keep evenly spaced frames; the browser interpolates
                step = len(frames) / MAX_FRAMES_PER_TICK
                frames = [frames[int(k * step)] for k in range(MAX_FRAMES_PER_TICK - 1)] + [frames[-1]]
            tick = {"type": "tick", "matchId": mid, "frames": frames, "state": self.pending_state.pop(mid, None),
                    "wallMs": int(time.time() * 1000)}  # lets a late joiner read the pace off the recording
            self.last_tick[mid] = now
            self._record_nowait(mid, tick)  # even when back-pressure drops the publish below
            self._publish_nowait(frames_group(mid), tick)
        if sb_msgs:  # Service Bus dedupes on message_id, so a retried send is safe
            tasks.append(lambda: self.sender.send_messages([
                ServiceBusMessage(body, session_id=sid, message_id=mid, application_properties={"kind": kind})
                for body, sid, mid, kind in sb_msgs]))
        lifecycle = [(it, t, d) for it, out in outputs for t, d in out.lifecycle]
        if lifecycle and self.eventgrid is not None:
            from azure.core.messaging import CloudEvent

            events = [CloudEvent(source=f"/matchlens/matches/{d['matchId']}", type=f"MatchLens.Match.{t}", data=d,
                                 subject=d["matchId"]) for _, t, d in lifecycle]
            tasks.append(lambda: self.eventgrid.send(events))
        results = await asyncio.gather(*(self._deliver(t) for t in tasks), return_exceptions=True)
        failed = [r for r in results if isinstance(r, BaseException)]
        if failed:
            raise RuntimeError(f"{len(failed)} of {len(tasks)} outputs failed after {EMIT_ATTEMPTS} attempts") from failed[0]

    async def _deliver(self, make):
        for attempt in range(EMIT_ATTEMPTS):
            try:
                return await make()
            except Exception as exc:
                if attempt == EMIT_ATTEMPTS - 1:
                    raise
                log.warning("output failed (attempt %d): %s: %s", attempt + 1, type(exc).__name__, exc)
                await asyncio.sleep(EMIT_BACKOFF_S * 2 ** attempt)

    def _record_nowait(self, match_id: str, tick: dict) -> None:
        if self.recording_inflight >= MAX_INFLIGHT_RECORDINGS:
            self._lost_record("too many appends in flight")
            return
        self.recording_inflight += 1
        task = asyncio.create_task(self._record(match_id, tick))
        self.bg.add(task)
        task.add_done_callback(self.bg.discard)

    def _lost_record(self, why: object) -> None:
        self.lost_records += 1
        if self.lost_records % 100 == 1:
            log.warning("recording is failing (%s): %d ticks missing from recordings so far", why, self.lost_records)

    async def _record(self, match_id: str, tick: dict) -> None:
        try:
            await asyncio.wait_for(self._append(match_id, tick), timeout=RECORD_TIMEOUT_S)
        except Exception as exc:
            self._lost_record(repr(exc))
        finally:
            self.recording_inflight -= 1

    async def _append(self, match_id: str, tick: dict) -> None:
        """Append one tick to the match's recording. It holds only what was broadcast, never a
        re-simulation, so it can't reveal the future. A failover may repeat frames; the player dedupes."""
        from azure.core import MatchConditions
        from azure.core.exceptions import (
            ResourceExistsError,
            ResourceModifiedError,
            ResourceNotFoundError,
        )

        blob = self.recordings.get_blob_client(recording_blob(match_id))
        line = (json.dumps(tick, separators=(",", ":")) + "\n").encode()
        try:
            await blob.append_block(line)
        except ResourceNotFoundError:
            try:
                await blob.create_append_blob(match_condition=MatchConditions.IfMissing)
            except (ResourceExistsError, ResourceModifiedError):
                pass  # another append got there first (IfMissing can fail as 409 or as 412 ConditionNotMet)
            await blob.append_block(line)

    def _publish_nowait(self, group: str, payload: dict) -> None:
        if self.inflight >= MAX_INFLIGHT_PUBLISHES:
            self.dropped_ticks += 1
            if self.dropped_ticks % 100 == 1:
                log.warning("Web PubSub is slow: dropped %d live ticks so far", self.dropped_ticks)
            return
        self.inflight += 1
        task = asyncio.create_task(self._publish(group, payload))
        self.bg.add(task)
        task.add_done_callback(self.bg.discard)

    async def _publish(self, group: str, payload: dict) -> None:
        try:
            await asyncio.wait_for(self.wps.send_to_group(group, payload, content_type="application/json"),
                                   timeout=PUBLISH_TIMEOUT_S)  # the SDK serializes dicts
        except Exception as exc:
            log.debug("tick publish failed: %s", exc)
        finally:
            self.inflight -= 1


def main() -> None:
    setup_telemetry("interpreter")
    asyncio.run(InterpreterService().start())


if __name__ == "__main__":
    main()
