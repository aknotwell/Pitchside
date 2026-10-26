"""Shared Azure plumbing: settings, credentials and telemetry. Keyless everywhere."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger("matchlens")


@dataclass(frozen=True)
class Settings:
    eventhub_fqdn: str
    eventhub_name: str
    eventhub_consumer_group: str
    servicebus_fqdn: str
    servicebus_queue: str
    storage_blob_endpoint: str
    checkpoint_container: str
    cosmos_endpoint: str
    cosmos_database: str
    webpubsub_endpoint: str
    webpubsub_hub: str
    foundry_project_endpoint: str
    foundry_deployments: list[str]
    subscription_id: str
    resource_group: str
    sim_job_name: str
    appinsights_connection_string: str


@lru_cache
def settings() -> Settings:
    e = os.environ.get
    return Settings(
        eventhub_fqdn=e("EVENTHUB_FQDN", ""),
        eventhub_name=e("EVENTHUB_NAME", "match-stream"),
        eventhub_consumer_group=e("EVENTHUB_CONSUMER_GROUP", "interpreter"),
        servicebus_fqdn=e("SERVICEBUS_FQDN", ""),
        servicebus_queue=e("SERVICEBUS_QUEUE", "moments"),
        storage_blob_endpoint=e("STORAGE_BLOB_ENDPOINT", ""),
        checkpoint_container=e("CHECKPOINT_CONTAINER", "ehcheckpoints"),
        cosmos_endpoint=e("COSMOS_ENDPOINT", ""),
        cosmos_database=e("COSMOS_DATABASE", "matchlens"),
        webpubsub_endpoint=e("WEBPUBSUB_ENDPOINT", ""),
        webpubsub_hub=e("WEBPUBSUB_HUB", "matchlens"),
        foundry_project_endpoint=e("FOUNDRY_PROJECT_ENDPOINT", ""),
        foundry_deployments=[d for d in e("FOUNDRY_DEPLOYMENTS", "gpt-5-mini").split(",") if d],
        subscription_id=e("AZURE_SUBSCRIPTION_ID", ""),
        resource_group=e("AZURE_RESOURCE_GROUP", ""),
        sim_job_name=e("SIM_JOB_NAME", ""),
        appinsights_connection_string=e("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
    )


def async_credential():
    """Managed identity in Azure (AZURE_CLIENT_ID selects the user-assigned one); az login locally."""
    from azure.identity.aio import DefaultAzureCredential

    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def sync_credential():
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


_telemetry_done = False


def setup_telemetry(service: str) -> None:
    """OpenTelemetry -> Application Insights (Entra-authenticated; local auth is disabled)."""
    global _telemetry_done
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("azure", "azure.core.pipeline.policies.http_logging_policy", "azure.eventhub", "azure.servicebus",
                  "uamqp", "azure.identity", "azure.monitor.opentelemetry.exporter"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if _telemetry_done:
        return
    _telemetry_done = True
    cs = settings().appinsights_connection_string
    if not cs:
        log.info("telemetry: no APPLICATIONINSIGHTS_CONNECTION_STRING, console only")
        return
    os.environ.setdefault("OTEL_SERVICE_NAME", service)
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=cs, credential=sync_credential(),
                                logger_name="matchlens", enable_live_metrics=True)
        log.info("telemetry: Azure Monitor configured for %s", service)
    except Exception as exc:  # telemetry must never take a service down
        log.warning("telemetry setup failed: %s", exc)


def current_trace_id() -> str | None:
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.is_valid else None


async def create_once(container, doc: dict) -> bool:
    """Insert an immutable document; a replayed duplicate (409) is a no-op, so the change feed
    never fires twice for the same cue. Returns True if this call created it."""
    from azure.core.exceptions import ResourceExistsError

    try:
        await container.create_item(doc)
        return True
    except ResourceExistsError:
        return False


def async_openai(project_endpoint: str, credential):
    """Raw Azure OpenAI client on the Foundry account (Reflex needs token logprobs)."""
    from urllib.parse import urlsplit

    from azure.identity.aio import get_bearer_token_provider
    from openai import AsyncAzureOpenAI

    u = urlsplit(project_endpoint)
    # No SDK retries: Reflex passes to the agent instead of waiting out a 429 or a timeout.
    return AsyncAzureOpenAI(azure_endpoint=f"{u.scheme}://{u.netloc}/", api_version="2025-04-01-preview", max_retries=0,
                            azure_ad_token_provider=get_bearer_token_provider(
                                credential, "https://cognitiveservices.azure.com/.default"))
