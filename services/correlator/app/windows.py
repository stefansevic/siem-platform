"""
SlidingWindow drži niz (timestamp, event) unosa i automatski izbacuje
one starije od svoje podešene dužine trajanja.

Struktura je namerno generička: ne zna šta je "event". Pravila prave
jedan prozor po (pravilo, subjekat) paru - npr. jedan SlidingWindow
neuspelih login-a po source IP za brute-force pravilo.

Napomene o dizajnu:
    * Unosi se drže SORTIRANI po timestamp-u. Događaji ne moraju da stižu
      u vremenskom redosledu (mrežno kašnjenje, preuređivanje u stream-u),
      pa se novi unos ubacuje na pravo mesto (bisect), a ne prosto na kraj.
      Time su izbacivanje sa početka i first_timestamp() uvek tačni.
    * Vreme toka ("sada") je NAJVEĆI viđeni timestamp, ne poslednji dodati.
      Tako zakasneli, stariji događaj ne pomera granicu prozora unazad.
    * Izbacivanje je lenjo: dešava se na početku svakog add(). Prozori
      neaktivnih subjekata se čiste tek eksplicitnim prune()-om, koji
      engine poziva periodično.
    * Vreme dolazi spolja (nema datetime.now() unutra), pa testovi mogu
      deterministički da pomeraju vreme.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Iterator, List, Optional


@dataclass(frozen=True)
class WindowEntry:
    """Jedan unos u kliznom prozoru."""
    timestamp: datetime
    event: Any  # šta god pravilu treba da zadrži (ECSEvent, dict, itd.)


class SlidingWindow:
    """
    Vremenski ograničen niz događaja, sortiran po timestamp-u.

    """

    def __init__(self, duration: timedelta):
        if duration.total_seconds() <= 0:
            raise ValueError("duration must be positive")
        self._duration = duration  # koliko unazad prozor pamti (npr. 60s)
        self._entries: List[WindowEntry] = []
        self._max_ts: Optional[datetime] = None  # najveći viđeni timestamp = "sada"

    # ----- Mutators -----

    def add(self, timestamp: datetime, event: Any) -> None:
        """
        Dodaj unos na pravo mesto (po timestamp-u) i izbaci istekle.

        "Sada" je najveći viđeni timestamp, pa zakasneli stariji događaj
        ne pomera granicu prozora. Prozor napreduje po vremenu iz stream-a,
        ne po zidnom satu.
        """
        entry = WindowEntry(timestamp, event)
        # Ubaci u sortiran niz po timestamp-u (stabilno na kraj za jednake).
        bisect.insort(self._entries, entry, key=lambda e: e.timestamp)

        if self._max_ts is None or timestamp > self._max_ts:
            self._max_ts = timestamp
        self._evict_older_than(self._max_ts - self._duration)

    def prune(self, now: datetime) -> int:
        """
        Prinudno izbaci unose u odnosu na dato `now`. Vraća broj
        izbačenih unosa. Koristi ga engine u periodičnom čišćenju.
        """
        # "Sada" ne sme da ide unazad u odnosu na najveći viđeni timestamp.
        if self._max_ts is not None and now < self._max_ts:
            now = self._max_ts
        before = len(self._entries)
        self._evict_older_than(now - self._duration)
        return before - len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._max_ts = None

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
        """Izbaci unose čiji je timestamp strogo manji od cutoff-a.

        Niz je sortiran po timestamp-u, pa je dovoljno skidati sa početka.
        """
        idx = 0
        n = len(self._entries)
        while idx < n and self._entries[idx].timestamp < cutoff:
            idx += 1
        if idx:
            del self._entries[:idx]
