"""
Centralized Redis key/stream names used across services.

Keeping these in one place prevents typos that silently break communication
between services (e.g. one service publishing to "raw_logs" while another
consumes from "raw-logs").
"""

# Streams (Redis Streams API)
STREAM_RAW_LOGS = "raw_logs"
STREAM_NORMALIZED_EVENTS = "normalized_events"
STREAM_DEAD_LETTER = "dead_letter_logs"  # Malformed payloads quarantined here for forensic analysis

# Consumer groups
GROUP_NORMALIZER = "normalizer-group"
GROUP_CORRELATOR = "correlator-group"

# Keys for state held by the Correlation Engine (sliding-window state, etc.)
PREFIX_CORRELATION_STATE = "siem:correlation:"