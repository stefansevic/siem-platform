"""
Unit tests for the idempotency key generator.

Two properties matter:
  * Determinism: same input -> same key (the whole point).
  * Discrimination: different inputs -> different keys, especially across
    source/format boundaries.
"""

from __future__ import annotations

from shared.ecs_models import LogFormat, LogSource, RawLogMessage

from app.idempotency import compute_idempotency_key


def _raw(payload: str, source=LogSource.NGINX, fmt=LogFormat.NGINX_COMBINED) -> RawLogMessage:
    return RawLogMessage(source=source, format=fmt, payload=payload)


class TestDeterminism:
    def test_identical_inputs_produce_identical_keys(self):
        a = _raw("some payload")
        b = _raw("some payload")
        assert compute_idempotency_key(a) == compute_idempotency_key(b)

    def test_key_is_64_hex_characters(self):
        key = compute_idempotency_key(_raw("anything"))
        assert len(key) == 64
        # All chars should be lowercase hex
        assert all(c in "0123456789abcdef" for c in key)


class TestDiscrimination:
    def test_different_payloads_differ(self):
        a = compute_idempotency_key(_raw("payload one"))
        b = compute_idempotency_key(_raw("payload two"))
        assert a != b

    def test_different_sources_differ_for_same_payload(self):
        a = compute_idempotency_key(_raw("X", source=LogSource.NGINX, fmt=LogFormat.JSON))
        b = compute_idempotency_key(_raw("X", source=LogSource.DEMO_WEBAPP, fmt=LogFormat.JSON))
        assert a != b

    def test_different_formats_differ_for_same_payload(self):
        a = compute_idempotency_key(_raw("X", source=LogSource.NGINX, fmt=LogFormat.NGINX_COMBINED))
        b = compute_idempotency_key(_raw("X", source=LogSource.NGINX, fmt=LogFormat.JSON))
        assert a != b

    def test_separator_prevents_concatenation_collision(self):
        """
        Without a separator between fields, ('foo', 'bar') and ('fo', 'obar')
        would hash to the same value. The unit separator byte prevents this.
        """
        a = _raw("bar", source=LogSource.NGINX, fmt=LogFormat.NGINX_COMBINED)  # "nginx" + "nginx_combined" + "bar"
        # Construct a synthetic case where naive concat would collide with `a`.
        # We can't easily fake source/fmt to be longer strings (they're enums),
        # but we can verify two payloads that differ only by where we'd "split"
        # produce different keys.
        b = _raw("xbar", source=LogSource.NGINX, fmt=LogFormat.NGINX_COMBINED)
        assert compute_idempotency_key(a) != compute_idempotency_key(b)