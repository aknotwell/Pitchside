"""Interpreter: consumes ``match-stream`` and turns it into state, cues, moments and live frames.

* Event Hubs consumer group ``interpreter``; checkpoints in Blob (KEDA scales on the lag).
* Per-match state is a pure function of the stream. Before each checkpoint we snapshot every
  live match to Blob, so a new replica resumes exactly where the old one stopped. Anything
  replayed after a crash is de-duplicated (event ids, tracking timestamps, deterministic cue ids).
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
MAX_INFLIGHT_RECORDINGS = 64  # same rule for recordings: a slow append must not hold up the batch loop
RECORD_TIMEOUT_S = 5.0


class InterpreterService:
    def __init__(self):
        self.s = settings()
        self.matches: dict[str, MatchInterpreter] = {}
        self.match_partition: dict[str, str] = {}
        self.last_checkpoint: dict[str, float] = {}
        self.pending_frames: dict[str, list] = {}
        self.pending_state: dict[str, dict] = {}
        self.last_tick: dict[str, float] = {}
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
            self.matches.pop(mid, None)
            self.match_partition.pop(mid, None)
        self.last_checkpoint.pop(pid, None)
        return len(mids)

    # ------------------------------------------------------------ snapshots
    def _snapshot_name(self, match_id: str) -> str:
        return f"snapshots/{match_id}.pkl"

    async def _load(self, match_id: str) -> MatchInterpreter:
        try:
            blob = self.snapshots.get_blob_client(self._snapshot_name(match_id))
            data = await (await blob.download_blob()).readall()
            it = pickle.loads(data)  # our own snapshot from our own private container
            log.info("restored %s from snapshot at clock %.0f s", match_id, it.clock)
            return it
        except Exception:
            return MatchInterpreter(match_id)

    async def _save(self, it: MatchInterpreter) -> None:
        blob = self.snapshots.get_blob_client(self._snapshot_name(it.match_id))
        await blob.upload_blob(pickle.dumps(it), overwrite=True)

    # ------------------------------------------------------------ processing
    async def on_batch(self, partition_context, events) -> None:
        """Never let an exception escape: the SDK would stop this partition's receive loop while
        the load balancer keeps renewing its ownership, silently stalling the partition."""
        try:
            await self._on_batch(partition_context, events)
        except Exception:
            log.exception("batch failed on partition %s; will retry on the next batch", partition_context.partition_id)

    async def _on_batch(self, partition_context, events) -> None:
        if not events:
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
                    it = await self._load(msg.match_id) if msg.kind != "metadata" else MatchInterpreter(msg.match_id)
                    self.matches[msg.match_id] = it
                    self.match_partition[msg.match_id] = pid
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
                live = [it for mid, it in self.matches.items() if self.match_partition.get(mid) == pid]
                await asyncio.gather(*(self._save(it) for it in live))
                await partition_context.update_checkpoint(events[-1])
                self.last_checkpoint[pid] = now
                for it in [it for it in live if it.ended]:
                    self.matches.pop(it.match_id, None)

    async def _emit(self, outputs: list[tuple[MatchInterpreter, Output]]) -> None:
        from azure.servicebus import ServiceBusMessage

        tasks = []
        sb_msgs = []
        latest_state: dict[str, dict] = {}
        latest_frames: dict[str, list] = {}
        for it, out in outputs:
            for cue in out.cues:
                tasks.append(create_once(self.cues, cue.to_doc()))
            for m in out.moments:
                doc = m.model_dump(mode="json")
                tasks.append(self.moments.upsert_item({"id": m.moment_id, "matchId": m.match_id, **doc}))
                sb_msgs.append(ServiceBusMessage(m.model_dump_json(), session_id=m.match_id, message_id=m.moment_id,
                                                 application_properties={"kind": m.kind}))
            if out.state is not None:
                latest_state[it.match_id] = out.state
            if out.frames is not None:
                latest_frames.setdefault(it.match_id, []).extend(out.frames["frames"])
        for mid, st in latest_state.items():
            tasks.append(self.state.upsert_item(st))
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
        if sb_msgs:
            tasks.append(self.sender.send_messages(sb_msgs))
        lifecycle = [(it, t, d) for it, out in outputs for t, d in out.lifecycle]
        if lifecycle and self.eventgrid is not None:
            from azure.core.messaging import CloudEvent

            events = [CloudEvent(source=f"/matchlens/matches/{d['matchId']}", type=f"MatchLens.Match.{t}", data=d,
                                 subject=d["matchId"]) for _, t, d in lifecycle]
            tasks.append(self.eventgrid.send(events))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                log.warning("output failed: %s", r)

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
        task = asyncio.create_task(self._publish(group, payload))
        self.bg.add(task)
        task.add_done_callback(self.bg.discard)

    async def _publish(self, group: str, payload: dict) -> None:
        self.inflight += 1
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
