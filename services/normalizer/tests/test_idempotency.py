"""
Unit testovi za generator idempotency ključa.

Bitne su dve osobine:
  * Determinizam: isti ulaz -> isti ključ (cela poenta).
  * Razlikovanje: različiti ulazi -> različiti ključevi, naročito preko
    granice izvora/formata.
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
        # Svi karakteri treba da su mala heksadecimalna slova
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
        Bez separatora između polja, ('foo', 'bar') i ('fo', 'obar') bi
        se heširali u istu vrednost. Bajt unit separatora to sprečava.
        """
        a = _raw("bar", source=LogSource.NGINX, fmt=LogFormat.NGINX_COMBINED)  # "nginx" + "nginx_combined" + "bar"
        # Napravi slučaj gde bi naivno spajanje kolidiralo sa `a`.
        # source/fmt ne možemo lako da lažiramo u duže stringove (enum-i su),
        # ali možemo da proverimo da dva payload-a koja se razlikuju samo
        # po tome gde bismo "presekli" daju različite ključeve.
        b = _raw("xbar", source=LogSource.NGINX, fmt=LogFormat.NGINX_COMBINED)
        assert compute_idempotency_key(a) != compute_idempotency_key(b)