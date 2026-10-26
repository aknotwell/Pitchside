"""Cue publisher (Azure Functions, Flex Consumption).

Cosmos DB change feed on ``cues`` -> Azure Web PubSub group per match x persona x language.
Every cue, template or agent-written, reaches browsers through this one event-driven hop.
"""

import logging
import os
import time

import azure.functions as func
from azure.identity import ManagedIdentityCredential
from azure.messaging.webpubsubservice import WebPubSubServiceClient

app = func.FunctionApp()
_client: WebPubSubServiceClient | None = None
SEND_ATTEMPTS = 3
SEND_BACKOFF_S = 0.5


def client() -> WebPubSubServiceClient:
    global _client
    if _client is None:
        cred = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
        _client = WebPubSubServiceClient(endpoint=os.environ["WEBPUBSUB_ENDPOINT"],
                                         hub=os.environ.get("WEBPUBSUB_HUB", "matchlens"), credential=cred)
    return _client


def is_transient(exc: Exception) -> bool:
    """Most Web PubSub failures are transient (429, 5xx, network). Any other 4xx won't get better."""
    from azure.core.exceptions import HttpResponseError

    code = exc.status_code if isinstance(exc, HttpResponseError) else None
    return not (code and 400 <= code < 500 and code != 429)


def send_with_retry(send, group: str, payload: dict) -> None:
    for attempt in range(SEND_ATTEMPTS):
        try:
            send(group, payload, content_type="application/json")
            return
        except Exception as exc:
            if attempt == SEND_ATTEMPTS - 1 or not is_transient(exc):
                raise
        time.sleep(SEND_BACKOFF_S * 2 ** attempt)


@app.function_name("cue_publisher")
@app.cosmos_db_trigger(arg_name="docs", connection="CosmosDb", database_name="matchlens", container_name="cues",
                       lease_container_name="leases", create_lease_container_if_not_exists=False,
                       feed_poll_delay=250, max_items_per_invocation=100)
@app.retry(strategy="fixed_delay", max_retry_count="3", delay_interval="00:00:05")
def cue_publisher(docs: func.DocumentList) -> None:
    publish(docs, client().send_to_group)


def publish(docs, send) -> int:
    """Send every cue. A cue that still fails after retries with a transient error fails the whole
    invocation, so the Functions runtime retries the batch before the change-feed lease moves on
    (the browser dedupes resent cues by id). A cue Web PubSub rejects outright is logged and skipped:
    one bad cue must not block the feed."""
    sent, transient = 0, []
    for d in docs:
        cue = {k: v for k, v in dict(d).items() if not k.startswith("_")}
        group = f"m.{cue['matchId']}.cues.{cue.get('persona', 'all')}.{cue.get('lang', 'all')}"
        try:
            send_with_retry(send, group, {"type": "cue", "cue": cue})
            sent += 1
            enq = (cue.get("data") or {}).get("enqueuedAtMs")
            if enq:  # server-side SLO signal: event enqueued -> cue pushed to Web PubSub
                logging.info("cue_published match=%s source=%s template=%s latency_ms=%d",
                             cue["matchId"], cue.get("source"), cue.get("template"), time.time() * 1000 - enq)
        except Exception as exc:
            logging.warning("publish failed for %s: %s", cue.get("id"), exc)
            if is_transient(exc):
                transient.append(exc)
    logging.info("published %d/%d cues", sent, len(docs))
    if transient:
        raise RuntimeError(f"{len(transient)} cue(s) not delivered; retrying the batch") from transient[0]
    return sent


# ---------------------------------------------------------------------------------------------
# Match-data MCP server (Functions MCP extension, streamable HTTP at /runtime/webhooks/mcp).
# Read-only, aggregates only. Used by GitHub Copilot agent mode, Foundry agents and any MCP client.
# ---------------------------------------------------------------------------------------------
_db = None


def db():
    global _db
    if _db is None:
        from azure.cosmos import CosmosClient

        cred = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
        _db = CosmosClient(os.environ["CosmosDb__accountEndpoint"], credential=cred).get_database_client(
            os.environ.get("COSMOS_DATABASE", "matchlens"))
    return _db


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


@app.mcp_tool()
def list_matches() -> str:
    """List the 20 most recent synthetic matches with score, clock and whether they have ended."""
    import json

    q = ("SELECT TOP 20 c.id, c.displayClock, c.ended, c.home.name AS home, c.away.name AS away, "
         "c.home.goals AS homeGoals, c.away.goals AS awayGoals FROM c WHERE c.type = 'matchState' ORDER BY c._ts DESC")
    return json.dumps(list(db().get_container_client("matchState").query_items(q, enable_cross_partition_query=True)))


@app.mcp_tool()
def get_match_state(match_id: str) -> str:
    """Live deterministic metrics for one match: score, xG, possession, pass accuracy, PPDA, momentum,
    control-vs-chaos and top speeds. All numbers are computed by code from the event stream."""
    import json

    doc = _clean(db().get_container_client("matchState").read_item(match_id, partition_key=match_id))
    doc.pop("meta", None)
    return json.dumps(doc)


@app.mcp_tool()
def get_moments(match_id: str) -> str:
    """Detected moments (goals, big chances, momentum swings, pressing surges, recaps) with their
    evidence packs, newest last."""
    import json

    items = db().get_container_client("moments").query_items(
        "SELECT c.moment_id, c.kind, c.clock_s, c.headline, c.evidence, c.score FROM c ORDER BY c.clock_s",
        partition_key=match_id)
    return json.dumps(list(items)[-25:])


@app.mcp_tool()
def get_overlays(match_id: str, persona: str = "analyst", lang: str = "en") -> str:
    """Overlay cues shown to a viewer (persona 'analyst', 'casual', 'academy' or 'scout', lang 'en', 'es' or 'ja'),
    including whether each was written by an agent and verified."""
    import json

    items = db().get_container_client("cues").query_items(
        "SELECT c.id, c.display_at_s, c.template, c.title, c.text, c.source, c.verified FROM c "
        "WHERE (c.persona = @p OR c.persona = 'all') AND (c.lang = @l OR c.lang = 'all') ORDER BY c.display_at_s",
        parameters=[{"name": "@p", "value": persona}, {"name": "@l", "value": lang}], partition_key=match_id)
    return json.dumps(list(items)[-40:])
