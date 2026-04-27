"""
Deterministic idempotency keys for normalized events.

The Normalizer is an at-least-once consumer. To make persistence
exactly-once, every ECSEvent carries a 64-character hex SHA-256 digest
of the (source, format, payload) tuple it was derived from.

Identical raw inputs from the same source produce identical keys, so a
duplicate insert collapses into a no-op via ON CONFLICT DO NOTHING.
Different sources or formats produce different keys even if their
payload bytes coincide -- that prevents cross-source collisions in the
unlikely event that two systems emit the exact same line.
"""

from __future__ import annotations

import hashlib

from shared.ecs_models import RawLogMessage


def compute_idempotency_key(raw: RawLogMessage) -> str:
    """
    Return a 64-character hex SHA-256 digest derived from
    (source, format, payload).

    The digest length matches the `events.idempotency_key CHAR(64)` column.
    """
    # `use_enum_values=True` on RawLogMessage means these are already strings
    # at runtime, but we coerce defensively to be format-agnostic.
    source = str(raw.source)
    fmt = str(raw.format)
    payload = raw.payload

    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\x1f")  # ASCII unit separator: avoids accidental concatenation collisions
    h.update(fmt.encode("utf-8"))
    h.update(b"\x1f")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()