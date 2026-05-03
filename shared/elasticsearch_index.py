"""
Elasticsearch index template for the events index family.

The template is applied at startup by the Normalizer service. Every
new daily index (events-YYYY.MM.DD) automatically inherits the
mapping below, so we don't rely on dynamic field detection — which
would mis-classify IP addresses as text and timestamps as strings.

Field naming follows ECS (Elastic Common Schema) so the same field
names work in both Postgres and Elasticsearch with no translation.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Index name format: events-YYYY.MM.DD
INDEX_PATTERN = "events-*"
TEMPLATE_NAME = "siem-events"


def daily_index_name(when: datetime | None = None) -> str:
    """Return the daily index name for the given UTC datetime (default: now)."""
    when = when or datetime.now(timezone.utc)
    return f"events-{when.strftime('%Y.%m.%d')}"


# Field mapping — explicit types prevent ES from guessing wrong.
# Notes:
#   - "ip" type enables CIDR queries and ip_range aggregations
#   - "keyword" for exact-match identifiers and enums
#   - "text" for free-form fields the operator might full-text search
#   - "date" auto-parses ISO-8601 timestamps from the JSON
INDEX_MAPPINGS = {
    "properties": {
        # Event identity
        "event_id":          {"type": "keyword"},
        "timestamp":         {"type": "date"},
        "ingested_at":       {"type": "date"},

        # ECS event categorization
        "event_kind":        {"type": "keyword"},
        "event_category":    {"type": "keyword"},
        "event_action":      {"type": "keyword"},
        "event_outcome":     {"type": "keyword"},

        # Source / target
        "source_ip":         {"type": "ip"},
        "user_name":         {"type": "keyword"},
        "user_agent":        {
            "type": "text",
            "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
        },

        # HTTP-specific
        "http_method":       {"type": "keyword"},
        "url_path": {
            "type": "text",
            "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}},
        },
        "http_response_status_code": {"type": "short"},

        # Origin tracking
        "log_source":        {"type": "keyword"},
        "host_name":         {"type": "keyword"},

        # Free-form details (rule output, custom fields)
        "details": {
            "type": "object",
            "enabled": True,
            # Don't index nested fields by default — saves index space
            # for fields the operator may not search by name
            "dynamic": True,
        },
    },
}


# Index settings — tuned for a single-node demo cluster.
INDEX_SETTINGS = {
    # 1 shard, 0 replicas: single-node cluster has no replica peers.
    # In production this would be 3 shards / 1 replica per index.
    "number_of_shards": 1,
    "number_of_replicas": 0,

    # Refresh once per second (default). Lower would burn CPU; higher
    # would delay search visibility past the dashboard's polling interval.
    "refresh_interval": "1s",
}


def build_template_body() -> dict:
    """Compose the full index_template payload sent to ES."""
    return {
        "index_patterns": [INDEX_PATTERN],
        "priority": 100,
        "template": {
            "settings": INDEX_SETTINGS,
            "mappings": INDEX_MAPPINGS,
        },
        "_meta": {
            "description": "SIEM events — applied by Normalizer at startup",
            "managed_by": "siem-platform",
        },
    }