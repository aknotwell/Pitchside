"""Simulator job: plays one synthetic match into the ``match-stream`` Event Hub.

Env: SIM_SEED (0 = random), SIM_SPEED (1 = real time), SIM_PERIOD_MIN (45), MATCH_ID (optional).
One ACA job execution = one live match; N parallel executions = the load test.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time

from opentelemetry import trace

from matchlens.azure_io.common import async_credential, current_trace_id, settings, setup_telemetry
from matchlens.contracts import StreamMessage
from matchlens.sim.engine import simulate

log = logging.getLogger("matchlens.simulator")
tracer = trace.get_tracer("matchlens.simulator")


def group_by_second(messages: list[StreamMessage]) -> list[list[StreamMessage]]:
    groups: list[list[StreamMessage]] = []
    current: list[StreamMessage] = []
    sec = None
    for m in messages:
        s = int(m.clock_s)
        if sec is None or s == sec or m.kind == "metadata":
            current.append(m)
            sec = s if sec is None else sec
        else:
            groups.append(current)
            current = [m]
            sec = s
    if current:
        groups.append(current)
    return groups


async def publish(messages: list[StreamMessage], speed: float, match_id: str) -> None:
    from azure.eventhub import EventData
    from azure.eventhub.aio import EventHubProducerClient

    s = settings()
    cred = async_credential()
    producer = EventHubProducerClient(fully_qualified_namespace=s.eventhub_fqdn, eventhub_name=s.eventhub_name,
                                      credential=cred)
    groups = group_by_second(messages)
    start_wall = time.monotonic()
    start_clock = groups[0][0].clock_s if groups else 0.0
    sent = 0
    async with producer:
        for gi, g in enumerate(groups):
            due = (g[0].clock_s - start_clock) / speed
            delay = due - (time.monotonic() - start_wall)
            if delay > 0:
                await asyncio.sleep(delay)
            with tracer.start_as_current_span("sim.publish_second", attributes={"match.id": match_id, "match.clock_s": g[0].clock_s}):
                trace_id = current_trace_id()
                batch = await producer.create_batch(partition_key=match_id)
                for m in g:
                    m.trace_id = trace_id
                    ed = EventData(m.model_dump_json())
                    ed.properties = {"kind": m.kind, "match_id": match_id}
                    try:
                        batch.add(ed)
                    except ValueError:  # batch full: flush and start a new one
                        await producer.send_batch(batch)
                        batch = await producer.create_batch(partition_key=match_id)
                        batch.add(ed)
                await producer.send_batch(batch)
                sent += len(g)
            if gi % 300 == 0:
                log.info("match %s: clock %.0f s, %d messages sent", match_id, g[0].clock_s, sent)
    await cred.close()
    log.info("match %s finished: %d messages in %.0f s wall", match_id, sent, time.monotonic() - start_wall)


def main() -> None:
    setup_telemetry("simulator")
    seed = int(os.environ.get("SIM_SEED", "0")) or random.randint(1, 99999)
    speed = float(os.environ.get("SIM_SPEED", "1"))
    period_min = float(os.environ.get("SIM_PERIOD_MIN", "45"))
    execution = os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME", "")
    suffix = execution.rsplit("-", 1)[-1] if execution else f"{int(time.time()) % 100000}"
    match_id = os.environ.get("MATCH_ID") or f"m{seed:05d}-{suffix}"
    log.info("simulating match %s (seed %d, speed %.1fx, %s min halves)", match_id, seed, speed, period_min)
    with tracer.start_as_current_span("sim.generate", attributes={"match.id": match_id, "sim.seed": seed}):
        result = simulate(seed, match_id=match_id, params={"period_min": period_min})
        messages = list(result.stream())
    log.info("generated %d events, %d stream messages", len(result.events), len(messages))
    asyncio.run(publish(messages, speed, match_id))


if __name__ == "__main__":
    main()
