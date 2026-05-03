"""
Thin wrapper around the official elasticsearch-py client.

Provides a single factory function that:
    - Builds an Elasticsearch client with sensible defaults
    - Verifies connectivity at startup
    - Applies the SIEM index template if missing
    - Logs failures clearly without crashing the caller
"""

from __future__ import annotations

import logging
from typing import Optional

from elasticsearch import Elasticsearch
from elasticsearch.exceptions import (
    ConnectionError as ESConnectionError,
    TransportError,
)

from shared.elasticsearch_index import TEMPLATE_NAME, build_template_body

log = logging.getLogger(__name__)


def build_elasticsearch_client(
    url: str,
    *,
    request_timeout: float = 10.0,
    retry_on_timeout: bool = True,
    max_retries: int = 3,
) -> Elasticsearch:
    """
    Create a configured ES client. Does NOT verify connectivity here —
    callers should call `verify_connection()` separately so they can
    decide how to react to failures (retry, exit, log).
    """
    return Elasticsearch(
        url,
        request_timeout=request_timeout,
        retry_on_timeout=retry_on_timeout,
        max_retries=max_retries,
    )


def verify_connection(client: Elasticsearch) -> bool:
    """Return True if the cluster responds and is at least yellow."""
    try:
        info = client.info()
        log.info(
            "Connected to Elasticsearch %s (cluster: %s)",
            info["version"]["number"],
            info["cluster_name"],
        )
        return True
    except ESConnectionError as exc:
        log.error("Cannot reach Elasticsearch: %s", exc)
        return False
    except TransportError as exc:
        log.error("Elasticsearch transport error: %s", exc)
        return False


def ensure_index_template(client: Elasticsearch) -> bool:
    """
    Idempotently apply the SIEM events index template.

    Returns True on success, False on failure. Failure is logged but
    not raised — the caller can decide whether ES is critical or
    optional for its workload.
    """
    try:
        body = build_template_body()
        client.indices.put_index_template(
            name=TEMPLATE_NAME,
            **body,
        )
        log.info("Applied index template: %s", TEMPLATE_NAME)
        return True
    except TransportError as exc:
        log.error("Failed to apply index template: %s", exc)
        return False