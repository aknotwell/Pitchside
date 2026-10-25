"""Storyteller worker: Service Bus ``moments`` (sessions) -> Agent Framework workflow -> Cosmos ``cues``.

* Session per match keeps each match's explanations in order; matches run in parallel across
  workers and replicas (KEDA scales replicas on queue depth).
* Peek-lock: a moment is completed only after its cues are written. If the replica dies, the
  lock expires, Service Bus redelivers, and the workflow resumes from its Cosmos checkpoint.
* Chaos drill: the producer console sets a flag; the next moment crashes this replica *after*
  the model call has been checkpointed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from opentelemetry import trace

from matchlens.azure_io.common import async_credential, create_once, settings, setup_telemetry
from matchlens.contracts import Moment
from matchlens.story.workflow import CHECKPOINT_TYPES, FoundryExplainer, Storyteller, TemplateWriter

log = logging.getLogger("matchlens.storyteller")
tracer = trace.get_tracer("matchlens.storyteller")

WORKERS = int(os.environ.get("STORY_WORKERS", "4"))
OPS_PK = "_ops"


class StorytellerService:
    def __init__(self):
        self.s = settings()
        self.rosters: dict[str, set[str]] = {}

    async def start(self) -> None:
        from agent_framework_azure_cosmos import CosmosCheckpointStorage
        from azure.cosmos.aio import CosmosClient
        from azure.servicebus.aio import ServiceBusClient

        s = self.s
        self.cred = async_credential()
        self.cosmos = CosmosClient(s.cosmos_endpoint, credential=self.cred)
        db = self.cosmos.get_database_client(s.cosmos_database)
        self.cues = db.get_container_client("cues")
        self.state = db.get_container_client("matchState")
        self.runs = db.get_container_client("runs")
        storage = CosmosCheckpointStorage(container_client=db.get_container_client("checkpoints"),
                                          allowed_checkpoint_types=CHECKPOINT_TYPES)
        if s.foundry_project_endpoint:
            writer = FoundryExplainer(s.foundry_project_endpoint, s.foundry_deployments, self.cred)
        else:
            log.warning("FOUNDRY_PROJECT_ENDPOINT is not set: writing every persona from templates")
            writer = TemplateWriter()
        self.story = Storyteller(writer, roster=lambda mid: self.rosters.get(mid, set()),
                                 checkpoint_storage=storage, fault=self._chaos_fault)
        self.chaos_armed = False
        self.sb = ServiceBusClient(s.servicebus_fqdn, credential=self.cred)
        from azure.storage.blob.aio import BlobServiceClient

        from matchlens.story.speech import Speech

        self.speech = Speech(self.cred)
        self.audio = BlobServiceClient(s.storage_blob_endpoint, credential=self.cred).get_container_client("audio")
        log.info("storyteller: %d session workers on %s/%s, models %s", WORKERS, s.servicebus_fqdn,
                 s.servicebus_queue, s.foundry_deployments)
        async with self.sb:
            await asyncio.gather(*(self._worker(i) for i in range(WORKERS)), self._chaos_poller())

    # ------------------------------------------------------------ chaos drill
    async def _chaos_poller(self) -> None:
        while True:
            try:
                doc = await self.runs.read_item("chaos", partition_key=OPS_PK)
                if doc.get("armed") and doc.get("target") == "storyteller":
                    self.chaos_armed = True
            except Exception:
                pass
            await asyncio.sleep(3)

    def _chaos_fault(self, approved) -> None:
        if not self.chaos_armed:
            return
        self.chaos_armed = False
        log.error("CHAOS: killing storyteller replica mid-workflow (moment %s)", approved.moment.moment_id)
        asyncio.get_event_loop().create_task(self._disarm_and_die())
        raise RuntimeError("chaos drill: replica killed")

    async def _disarm_and_die(self) -> None:
        try:
            await self.runs.upsert_item({"id": "chaos", "matchId": OPS_PK, "armed": False,
                                         "lastFiredAt": time.time(), "target": "storyteller"})
        finally:
            os._exit(17)  # hard exit: no graceful shutdown, exactly like a crashed node

    # ------------------------------------------------------------ audio recaps
    async def _voice(self, moment: Moment, cues) -> None:
        """Voice the fan (casual) recap in every language; attach the audio path to every cue of that language."""
        if not self.speech.enabled:
            return

        async def one(lang: str, text: str):
            try:
                audio = await self.speech.synthesize(text, lang)
                name = f"{moment.match_id}/{moment.moment_id}-{lang}.mp3"
                await self.audio.upload_blob(name, audio, overwrite=True)
                return lang, f"/api/audio/{name}"
            except Exception as exc:
                log.warning("speech failed for %s/%s: %s", moment.moment_id, lang, exc)
                return lang, None

        casual = {c.lang: f"{c.title}. {c.text}" for c in cues if c.persona == "casual"}
        paths = dict(await asyncio.gather(*(one(lang, text) for lang, text in casual.items())))
        for c in cues:
            if paths.get(c.lang):
                c.data["audio"] = paths[c.lang]

    # ------------------------------------------------------------ work
    async def _roster(self, match_id: str) -> None:
        if match_id in self.rosters:
            return
        try:
            doc = await self.state.read_item(match_id, partition_key=match_id)
            meta = doc.get("meta") or {}
            names = {p["name"] for t in ("home", "away") for p in (meta.get(t) or {}).get("players", [])}
            self.rosters[match_id] = names
        except Exception:
            self.rosters[match_id] = set()

    async def _worker(self, n: int) -> None:
        from azure.servicebus import NEXT_AVAILABLE_SESSION
        from azure.servicebus.exceptions import OperationTimeoutError, SessionCannotBeLockedError

        while True:
            try:
                async with self.sb.get_queue_receiver(self.s.servicebus_queue, session_id=NEXT_AVAILABLE_SESSION,
                                                      max_wait_time=20) as receiver:
                    session = receiver.session.session_id
                    log.info("worker %d took session %s", n, session)
                    while True:
                        msgs = await receiver.receive_messages(max_message_count=1, max_wait_time=30)
                        if not msgs:
                            break  # release idle session so others can take it
                        for msg in msgs:
                            await self._handle(receiver, msg)
                            await receiver.session.renew_lock()
            except (OperationTimeoutError, SessionCannotBeLockedError):
                continue
            except Exception as exc:
                log.warning("worker %d: %s", n, exc)
                await asyncio.sleep(2)

    async def _handle(self, receiver, msg) -> None:
        moment = Moment.model_validate_json(str(msg))
        with tracer.start_as_current_span("storyteller.moment", attributes={
                "match.id": moment.match_id, "moment.kind": moment.kind, "moment.id": moment.moment_id}):
            await self._roster(moment.match_id)
            try:
                cues, info = await self.story.tell(moment)
            except Exception as exc:
                log.warning("workflow failed for %s: %s (abandoning for redelivery)", moment.moment_id, exc)
                await receiver.abandon_message(msg)
                return
            if moment.kind in ("half_time", "full_time") and cues:
                await self._voice(moment, cues)
            await asyncio.gather(*(create_once(self.cues, c.to_doc()) for c in cues))
            await self.runs.upsert_item({"id": f"run-{moment.moment_id}", "matchId": moment.match_id,
                                         "type": "storyRun", "at": time.time(), **info})
            await receiver.complete_message(msg)
            log.info("moment %s (%s): %d cues via %s in %d ms%s", moment.moment_id, moment.kind, info["cues"],
                     info["source"], info["ms"], f", resumed from {info['resumed_from']}" if info["resumed_from"] else "")


def main() -> None:
    setup_telemetry("storyteller")
    asyncio.run(StorytellerService().start())


if __name__ == "__main__":
    main()
