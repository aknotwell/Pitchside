"""Replay packager (event-driven Container Apps Job).

FullTime CloudEvent -> Event Grid -> Storage queue -> this job (KEDA azure-queue scaler).
The simulator is deterministic, so the match is re-generated from its seed and parameters,
joined with every cue that aired (all personas and languages), and written to Blob as a
compact replay the web player can show even when nothing is live.

* The job waits (up to ``RECAP_WAIT_S``) for the storyteller's full-time recap, which is written
  on the cold path after the whistle, so the replay includes it.
* A replay must be the match that aired: if the re-simulation's parameters or final score differ,
  the request is refused rather than published.
* Storage queues have no dead-letter queue: unreadable requests, refused ones and requests that
  keep failing are moved to ``<queue>-poison`` and deleted, instead of retrying forever.
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
from matchlens.interpret.state import MatchInterpreter
from matchlens.sim.engine import simulate

log = logging.getLogger("matchlens.replay")

MAX_DEQUEUES = 3
VISIBILITY_S = 600  # longer than the recap wait plus packaging, so no second job picks the message up
RECAP_WAIT_S = 180.0
RECAP_POLL_S = 10.0
STATE_DROP = {"id", "matchId", "type", "meta", "window10"}  # fields the player doesn't read


class Refused(Exception):
    """A request that will never succeed: retrying it only burns job executions."""


def states_over_time(r) -> list[dict]:
    """Run the live interpreter over the re-simulated stream and keep every state snapshot it would
    have published (one per 5 s of match clock), so the replay's Stats panel follows the playhead."""
    it = MatchInterpreter(r.metadata.match_id)
    out = []
    for msg in r.stream():
        st = it.handle(msg).state
        if st is not None:
            out.append({k: v for k, v in st.items() if k not in STATE_DROP})
    return out


def package(match_id: str, seed: int, period_min: float, cues: list[dict], frame_hz: float = 5.0,
            aired: dict | None = None) -> dict:
    """``aired`` is the FullTime event's data; when given, the re-simulation must match it."""
    r = simulate(seed, match_id=match_id, params={"period_min": period_min})
    states = states_over_time(r)
    if aired:
        check_same_match(r.metadata.params_version, states[-1] if states else {}, aired)
    step = max(1, int(round(r.metadata.fps / frame_hz)))
    ids = r.player_ids
    frames = []
    for k in range(0, len(r.tracking_t), step):
        pl = r.tracking_players[k]
        b = r.tracking_ball[k]
        rs = r.tracking_restart[k]
        # ball height, then the pending restart, are appended so older replays (which omit them) still parse
        frames.append([round(float(r.tracking_t[k]), 1), round(float(b[0]), 1), round(float(b[1]), 1),
                       [None if pl[j][0] != pl[j][0] else [round(float(pl[j][0]), 1), round(float(pl[j][1]), 1)]
                        for j in range(len(ids))], round(float(b[2]), 1), list(rs) if rs else None])
    return {"schemaVersion": "1.0", "matchId": match_id, "meta": r.metadata.model_dump(mode="json"),
            "paramsVersion": r.metadata.params_version, "playerIds": ids, "frameHz": frame_hz, "frames": frames,
            "events": [e.model_dump(mode="json", include={"event_id", "clock_s", "display_clock", "type", "team_id",
                                                          "player_id", "outcome", "period"}) for e in r.events],
            "cues": sorted(cues, key=lambda c: c["display_at_s"]), "states": states,
            "createdAt": time.time()}


def check_same_match(params_version: str, final: dict, aired: dict) -> None:
    """Refuse a re-simulation that isn't the match that aired (fitted params or engine changed since)."""
    want = aired.get("paramsVersion")
    if want and params_version != want:
        raise Refused(f"params version {params_version} differs from the {want} that aired")
    score = aired.get("score")
    if score and final:
        got = {"h": final["home"]["goals"], "a": final["away"]["goals"]}
        if got != {k: int(v) for k, v in score.items()}:
            raise Refused(f"re-simulated score {got} differs from the {score} that aired")


def decode(content: str) -> dict:
    """Event Grid writes CloudEvents to Storage queues Base64-encoded."""
    text = content.strip()
    if not text.startswith("{"):
        text = base64.b64decode(text).decode("utf-8")
    return json.loads(text)


def parse_request(content: str) -> dict:
    """Return the FullTime data, or raise Refused for a request that can never be packaged."""
    try:
        event = decode(content)
        data = event.get("data") or {}
        if not isinstance(data.get("matchId"), str) or not data["matchId"]:
            raise ValueError("no matchId")
        data["seed"] = int(data["seed"])
    except Exception as exc:
        raise Refused(f"unreadable replay request: {type(exc).__name__}: {exc}") from exc
    return data


async def wait_for_recap(db, mid: str, timeout_s: float = RECAP_WAIT_S, every_s: float = RECAP_POLL_S) -> bool:
    """The storyteller writes the full-time recap (and its voice-over) on the cold path, about a
    minute after the whistle, then records a ``run-<moment id>`` doc. Wait for that doc."""
    from azure.core.exceptions import ResourceNotFoundError

    moments, runs = db.get_container_client("moments"), db.get_container_client("runs")
    deadline = time.monotonic() + timeout_s
    while True:
        ids = [i async for i in moments.query_items("SELECT VALUE c.id FROM c WHERE c.kind = 'full_time'",
                                                    partition_key=mid)]
        for moment_id in ids:
            try:
                await runs.read_item(f"run-{moment_id}", partition_key=mid)
                return True
            except ResourceNotFoundError:
                pass
        if time.monotonic() >= deadline:
            log.warning("no full-time recap for %s after %.0f s; packaging without it", mid, timeout_s)
            return False
        await asyncio.sleep(every_s)


async def handle(msg, db, blobs, poison) -> bool:
    """Package one request. Returns True when the message is finished with (packaged or poisoned);
    False leaves it on the queue to be retried after its visibility timeout."""
    try:
        if msg.dequeue_count > MAX_DEQUEUES:
            raise Refused(f"failed {msg.dequeue_count - 1} times")
        data = parse_request(msg.content)
        mid, seed = data["matchId"], data["seed"]
        log.info("packaging replay for %s (seed %d, %s min halves)", mid, seed, data.get("periodMin"))
        await wait_for_recap(db, mid)
        cues = [{k: v for k, v in c.items() if not k.startswith("_")}
                async for c in db.get_container_client("cues").query_items("SELECT * FROM c", partition_key=mid)]
        pkg = await asyncio.to_thread(package, mid, seed, float(data.get("periodMin") or 45), cues, aired=data)
    except Refused as exc:
        log.error("replay request refused, moving it to %s: %s", poison.queue_name, exc)
        await poison.send_message(json.dumps({"reason": str(exc), "dequeueCount": msg.dequeue_count,
                                              "content": msg.content}))
        return True
    except Exception:
        log.exception("replay packaging failed (attempt %d of %d)", msg.dequeue_count, MAX_DEQUEUES)
        return False
    body = gzip.compress(json.dumps(pkg, separators=(",", ":")).encode())
    await blobs.get_container_client("replays").upload_blob(f"{mid}.json.gz", body, overwrite=True)
    await db.get_container_client("runs").upsert_item({
        "id": f"replay-{mid}", "matchId": mid, "type": "replay", "at": time.time(), "bytes": len(body),
        "home": data.get("home"), "away": data.get("away"), "score": data.get("score"),
        "cues": len(cues), "frames": len(pkg["frames"]), "ttl": -1})  # the index outlives the container's 7-day TTL
    log.info("replay %s: %d frames, %d states, %d cues, %d KB", mid, len(pkg["frames"]), len(pkg["states"]),
             len(cues), len(body) // 1024)
    return True


async def run_once() -> int:
    from azure.cosmos.aio import CosmosClient
    from azure.storage.blob.aio import BlobServiceClient
    from azure.storage.queue.aio import QueueClient

    s = settings()
    cred = async_credential()
    queue_url = s.storage_blob_endpoint.replace(".blob.", ".queue.")
    name = os.environ.get("REPLAY_QUEUE", "replay-requests")
    done = 0
    async with cred, QueueClient(queue_url, name, credential=cred) as q, \
            QueueClient(queue_url, f"{name}-poison", credential=cred) as poison, \
            CosmosClient(s.cosmos_endpoint, credential=cred) as cosmos, \
            BlobServiceClient(s.storage_blob_endpoint, credential=cred) as blobs:
        db = cosmos.get_database_client(s.cosmos_database)
        async for msg in q.receive_messages(messages_per_page=1, visibility_timeout=VISIBILITY_S, max_messages=1):
            if await handle(msg, db, blobs, poison):
                await q.delete_message(msg)
                done += 1
    return done


def main() -> None:
    setup_telemetry("replay")
    n = asyncio.run(run_once())
    log.info("replay job finished: %d package(s)", n)


if __name__ == "__main__":
    main()
