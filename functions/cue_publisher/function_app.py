"""Cue publisher (Azure Functions, Flex Consumption).

Cosmos DB change feed on ``cues`` -> Azure Web PubSub group per match x persona x language.
Every cue, template or agent-written, reaches browsers through this one event-driven hop.
"""

import logging
import os

import azure.functions as func
from azure.identity import ManagedIdentityCredential
from azure.messaging.webpubsubservice import WebPubSubServiceClient

app = func.FunctionApp()
_client: WebPubSubServiceClient | None = None


def client() -> WebPubSubServiceClient:
    global _client
    if _client is None:
        cred = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
        _client = WebPubSubServiceClient(endpoint=os.environ["WEBPUBSUB_ENDPOINT"],
                                         hub=os.environ.get("WEBPUBSUB_HUB", "matchlens"), credential=cred)
    return _client


@app.function_name("cue_publisher")
@app.cosmos_db_trigger(arg_name="docs", connection="CosmosDb", database_name="matchlens", container_name="cues",
                       lease_container_name="leases", create_lease_container_if_not_exists=False,
                       feed_poll_delay=250, max_items_per_invocation=100)
def cue_publisher(docs: func.DocumentList) -> None:
    sent = 0
    for d in docs:
        cue = {k: v for k, v in dict(d).items() if not k.startswith("_")}
        group = f"m.{cue['matchId']}.cues.{cue.get('persona', 'all')}.{cue.get('lang', 'all')}"
        try:
            client().send_to_group(group, {"type": "cue", "cue": cue}, content_type="application/json")
            sent += 1
        except Exception as exc:  # one bad cue must not block the feed
            logging.warning("publish failed for %s: %s", cue.get("id"), exc)
    logging.info("published %d/%d cues", sent, len(docs))


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
    """Overlay cues shown to a viewer (persona 'analyst' or 'casual', lang 'en', 'es' or 'ja'),
    including whether each was written by an agent and verified."""
    import json

    items = db().get_container_client("cues").query_items(
        "SELECT c.id, c.display_at_s, c.template, c.title, c.text, c.source, c.verified FROM c "
        "WHERE (c.persona = @p OR c.persona = 'all') AND (c.lang = @l OR c.lang = 'all') ORDER BY c.display_at_s",
        parameters=[{"name": "@p", "value": persona}, {"name": "@l", "value": lang}], partition_key=match_id)
    return json.dumps(list(items)[-40:])
