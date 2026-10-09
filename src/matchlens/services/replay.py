"""Replay packager (event-driven Container Apps Job).

FullTime CloudEvent -> Event Grid -> Storage queue -> this job (KEDA azure-queue scaler).
The simulator is deterministic, so the match is re-generated from its seed and parameters,
joined with every cue that aired (all personas and languages), and written to Blob as a
compact replay the web player can show even when nothing is live.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
import time

from matchlens.azure_io.common import async_credential, settings, setup_telemetry
from matchlens.sim.engine import simulate

log = logging.getLogger("matchlens.replay")


def package(match_id: str, seed: int, period_min: float, cues: list[dict], frame_hz: float = 2.0) -> dict:
    r = simulate(seed, match_id=match_id, params={"period_min": period_min})
    step = max(1, int(round(r.metadata.fps / frame_hz)))
    ids = r.player_ids
    frames = []
    for k in range(0, len(r.tracking_t), step):
        pl = r.tracking_players[k]
        b = r.tracking_ball[k]
        frames.append([round(float(r.tracking_t[k]), 1), round(float(b[0]), 1), round(float(b[1]), 1),
                       [None if pl[j][0] != pl[j][0] else [round(float(pl[j][0]), 1), round(float(pl[j][1]), 1)]
                        for j in range(len(ids))]])
    return {"schemaVersion": "1.0", "matchId": match_id, "meta": r.metadata.model_dump(mode="json"),
            "paramsVersion": r.metadata.params_version, "playerIds": ids, "frameHz": frame_hz, "frames": frames,
            "events": [e.model_dump(mode="json", include={"event_id", "clock_s", "display_clock", "type", "team_id",
                                                          "player_id", "outcome", "period"}) for e in r.events],
            "cues": sorted(cues, key=lambda c: c["display_at_s"]), "createdAt": time.time()}


def decode(content: str) -> dict:
    """Event Grid writes CloudEvents to Storage queues Base64-encoded."""
    text = content.strip()
    if not text.startswith("{"):
        text = base64.b64decode(text).decode("utf-8")
    return json.loads(text)


async def run_once() -> int:
    from azure.cosmos.aio import CosmosClient
    from azure.storage.blob.aio import BlobServiceClient
    from azure.storage.queue.aio import QueueClient

    s = settings()
    cred = async_credential()
    queue_url = s.storage_blob_endpoint.replace(".blob.", ".queue.")
    done = 0
    async with cred, QueueClient(queue_url, os.environ.get("REPLAY_QUEUE", "replay-requests"), credential=cred) as q, \
            CosmosClient(s.cosmos_endpoint, credential=cred) as cosmos, \
            BlobServiceClient(s.storage_blob_endpoint, credential=cred) as blobs:
        db = cosmos.get_database_client(s.cosmos_database)
        async for msg in q.receive_messages(messages_per_page=1, visibility_timeout=300, max_messages=1):
            try:
                event = decode(msg.content)
            except Exception:
                log.exception("unreadable replay request; leaving it for the dead-letter path")
                continue
            data = event.get("data") or {}
            mid, seed = data["matchId"], int(data["seed"])
            log.info("packaging replay for %s (seed %d, %s min halves)", mid, seed, data.get("periodMin"))
            cues = [{k: v for k, v in c.items() if not k.startswith("_")}
                    async for c in db.get_container_client("cues").query_items("SELECT * FROM c", partition_key=mid)]
            pkg = await asyncio.to_thread(package, mid, seed, float(data.get("periodMin") or 45), cues)
            body = gzip.compress(json.dumps(pkg, separators=(",", ":")).encode())
            await blobs.get_container_client("replays").upload_blob(f"{mid}.json.gz", body, overwrite=True)
            await db.get_container_client("runs").upsert_item({
                "id": f"replay-{mid}", "matchId": mid, "type": "replay", "at": time.time(), "bytes": len(body),
                "home": data.get("home"), "away": data.get("away"), "score": data.get("score"),
                "cues": len(cues), "frames": len(pkg["frames"])})
            await q.delete_message(msg)
            log.info("replay %s: %d frames, %d cues, %d KB", mid, len(pkg["frames"]), len(cues), len(body) // 1024)
            done += 1
    return done


def main() -> None:
    setup_telemetry("replay")
    n = asyncio.run(run_once())
    log.info("replay job finished: %d package(s)", n)


if __name__ == "__main__":
    main()
