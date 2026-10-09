"""Cue publisher (Azure Functions, Flex Consumption).

Cosmos DB change feed on ``cues`` -> Azure Web PubSub group per match x persona x language.
Every cue, template or agent-written, reaches browsers through this one event-driven hop.
"""

import json
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
                       lease_container_name="leases", create_lease_container_if_not_exists=False)
def cue_publisher(docs: func.DocumentList) -> None:
    sent = 0
    for d in docs:
        cue = {k: v for k, v in dict(d).items() if not k.startswith("_")}
        group = f"m.{cue['matchId']}.cues.{cue.get('persona', 'all')}.{cue.get('lang', 'all')}"
        try:
            client().send_to_group(group, json.dumps({"type": "cue", "cue": cue}, separators=(",", ":")),
                                   content_type="application/json")
            sent += 1
        except Exception as exc:  # one bad cue must not block the feed
            logging.warning("publish failed for %s: %s", cue.get("id"), exc)
    logging.info("published %d/%d cues", sent, len(docs))
