"""
 idempotency ključevi za normalizovane događaje.

Normalizer je at-least-once consumer. Da bi čuvanje bilo idempotentno
(duplikati se tiho preskaču), svaki ECSEvent nosi SHA-256 heš od trojke
(source, format, payload) iz koje je nastao.

"""

from __future__ import annotations

import hashlib

from shared.ecs_models import RawLogMessage


def compute_idempotency_key(raw: RawLogMessage) -> str:
    """
    Vraća SHA-256 heš, Dužina heša odgovara koloni `events.idempotency_key CHAR(64)`.
    """
    # Zbog `use_enum_values=True` ovo su već stringovi u runtime-u,
    # ali ih svejedno pretvaramo, da ne zavisimo od formata.
    source = str(raw.source)
    fmt = str(raw.format)
    payload = raw.payload

    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\x1f")  # ASCII unit separator: sprečava koliziju od spajanja polja
    h.update(fmt.encode("utf-8"))
    h.update(b"\x1f")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()