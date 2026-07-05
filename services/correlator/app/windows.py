"""
SlidingWindow drži niz (timestamp, event) unosa i automatski izbacuje
one starije od svoje podešene dužine trajanja.

Struktura je namerno generička: ne zna šta je "event". Pravila prave
jedan prozor po (pravilo, subjekat) paru - npr. jedan SlidingWindow
neuspelih login-a po source IP za brute-force pravilo.

Napomene o dizajnu:
    * deque zbog O(1) append i O(1) popleft, što odgovara pristupu
      "najnovije na jednom kraju, najstarije na drugom".
    * Izbacivanje je lenjo: dešava se na početku svakog add(). Prozori
      neaktivnih subjekata se čiste tek eksplicitnim prune()-om, koji
      engine poziva periodično.
    * Vreme dolazi spolja (nema datetime.now() unutra), pa testovi mogu
      deterministički da pomeraju vreme.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Iterator, Optional


@dataclass(frozen=True)
class WindowEntry:
    """Jedan unos u kliznom prozoru."""
    timestamp: datetime
    event: Any  # šta god pravilu treba da zadrži (ECSEvent, dict, itd.)


class SlidingWindow:
    """
    Deque događaja ograničen vremenom.

    """

    def __init__(self, duration: timedelta):
        if duration.total_seconds() <= 0:
            raise ValueError("duration must be positive")
        self._duration = duration # koliko unazad prozor pamti (npr. 60s)
        self._entries: deque[WindowEntry] = deque()

    # ----- Mutators -----

    def add(self, timestamp: datetime, event: Any) -> None:
        """
        Dodaj unos; izbaci one koji su istekli u odnosu na timestamp
        novog unosa. Timestamp najnovijeg unosa se tretira kao "sada",
        pa prozor napreduje po vremenu iz stream-a, ne po zidnom satu.
        """
        self._evict_older_than(timestamp - self._duration)
        self._entries.append(WindowEntry(timestamp, event))

    def prune(self, now: datetime) -> int:
        """
        Prinudno izbaci unose u odnosu na dato `now`. Vraća broj
        izbačenih unosa. Koristi ga engine u periodičnom čišćenju.
        """
        before = len(self._entries)
        self._evict_older_than(now - self._duration)
        return before - len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    # ----- Inspectors -----

    def count(self, predicate: Optional[Callable[[Any], bool]] = None) -> int:
        """
        Vraća broj unosa trenutno u prozoru. Ako je dat predikat,
        broje se samo unosi čiji event ga zadovoljava. Ovaj poziv NE
        čisti prozor - broj odražava sve što je dodato; za "sveže"
        brojanje treba eksplicitno pozvati prune().
        """
        if predicate is None:
            return len(self._entries)
        return sum(1 for e in self._entries if predicate(e.event))

    def events(self) -> Iterator[Any]:
        """Prolazi kroz događaje (bez timestamp-a), od najstarijeg."""
        for entry in self._entries:
            yield entry.event

    def entries(self) -> Iterable[WindowEntry]:
        """Prolazi kroz (timestamp, event) unose, od najstarijeg."""
        return tuple(self._entries)

    def first_timestamp(self) -> Optional[datetime]:
        return self._entries[0].timestamp if self._entries else None

    def last_timestamp(self) -> Optional[datetime]:
        return self._entries[-1].timestamp if self._entries else None

    @property
    def duration(self) -> timedelta:
        return self._duration

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    # ----- Internal -----

    def _evict_older_than(self, cutoff: datetime) -> None:
        """Izbaci unose čiji je timestamp strogo manji od cutoff-a."""
        while self._entries and self._entries[0].timestamp < cutoff:
            self._entries.popleft()