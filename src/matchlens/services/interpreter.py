"""Interpreter: consumes ``match-stream`` and turns it into state, cues, moments and live frames.

* Event Hubs consumer group ``interpreter``; checkpoints in Blob (KEDA scales on the lag).
* Per-match state is a pure function of the stream. Before each checkpoint we snapshot every
  live match to Blob, so a new replica resumes exactly where the old one stopped. Anything
  replayed after a crash is de-duplicated (event ids, tracking timestamps, deterministic cue ids).
* Hot path: template cues are written straight to Cosmos ``cues`` (the change feed fans them out).
* Warm path: moments go to Service Bus (session = match id, so each match is explained in order).
"""

from __future__ import annotations

import asyncio
import logging
import pickle
import time

from opentelemetry import trace

from matchlens.azure_io.common import async_credential, settings, setup_telemetry
from matchlens.contracts import StreamMessage, frames_group, state_group
from matchlens.interpret.state import MatchInterpreter, Output

log = logging.getLogger("matchlens.interpreter")
tracer = trace.get_tracer("matchlens.interpreter")

SNAPSHOT_EVERY_S = 5.0


class InterpreterService:
    def __init__(self):
        self.s = settings()
        self.matches: dict[str, MatchInterpreter] = {}
        self.match_partition: dict[str, str] = {}
        self.last_checkpoint: dict[str, float] = {}

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
        self.snapshots = self.blobs.get_container_client(s.checkpoint_container)
        store = BlobCheckpointStore(blob_account_url=s.storage_blob_endpoint, container_name=s.checkpoint_container,
                                    credential=self.cred)
        consumer = EventHubConsumerClient(fully_qualified_namespace=s.eventhub_fqdn, eventhub_name=s.eventhub_name,
                                          consumer_group=s.eventhub_consumer_group, credential=self.cred,
                                          checkpoint_store=store)
        log.info("interpreter consuming %s/%s as %s", s.eventhub_fqdn, s.eventhub_name, s.eventhub_consumer_group)
        async with consumer, self.sender:
            await consumer.receive_batch(on_event_batch=self.on_batch, max_batch_size=400, max_wait_time=2.0,
                                         starting_position="-1")

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
                tasks.append(self.cues.upsert_item(cue.to_doc()))
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
            slim = {k: v for k, v in st.items() if k != "meta"}
            tasks.append(self._publish(state_group(mid), {"type": "state", "state": slim}))
        for mid, frames in latest_frames.items():
            tasks.append(self._publish(frames_group(mid), {"type": "frames", "matchId": mid, "frames": frames}))
        if sb_msgs:
            tasks.append(self.sender.send_messages(sb_msgs))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                log.warning("output failed: %s", r)

    async def _publish(self, group: str, payload: dict) -> None:
        await self.wps.send_to_group(group, payload, content_type="application/json")  # SDK serializes dicts


def main() -> None:
    setup_telemetry("interpreter")
    asyncio.run(InterpreterService().start())


if __name__ == "__main__":
    main()
