"""
Korelacioni engine: spaja pravila i prozore po subjektu.

Engine je jedina ulazna tačka za dolazeće događaje. Drži in-memory
stanje za sva pravila i svaki događaj prosleđuje prozorima koje on zanima.

Raspored stanja:
    self._windows: Dict[(rule_name, subject_key), SlidingWindow]

    Svako pravilo ima svoj prostor ključeva preko rule_name prefiksa;
    subjekti (obično source IP) su izolovani po pravilu, pa ista IP može
    imati i brute-force prozor I directory-scanning prozor bez mešanja.

Model niti:
    Jednonitni po dizajnu. Redis consumer je async i obrađuje događaje
    redom, pa nema istovremenog pristupa dict-u stanja. Ako ikad zatreba
    paralelna evaluacija, ovde bi se dodao lock ili šardovanje po hešu
    subjekta.

Periodično čišćenje:
    Prozori neaktivnih subjekata zauzimaju memoriju dok se ne očiste.
    Engine izlaže prune_stale(), koji consumer poziva svakih N sekundi
    da izbaci subjekte čiji je prozor potpuno istekao (prazan).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from shared.ecs_models import ECSEvent, Incident

from app.rules import CorrelationRule
from app.windows import SlidingWindow

logger = logging.getLogger(__name__)


# ============================================
# Stats (cheap observability)
# ============================================

@dataclass
class EngineStats:
    """Laki brojači za osmatranje kroz log linije."""
    events_processed: int = 0
    events_skipped: int = 0          # nijedno pravilo ih nije zanimalo
    incidents_emitted: int = 0
    windows_pruned: int = 0
    per_rule_incidents: dict = field(default_factory=dict)

    def record_incident(self, rule_name: str) -> None:
        self.incidents_emitted += 1
        self.per_rule_incidents[rule_name] = (
            self.per_rule_incidents.get(rule_name, 0) + 1
        )


# ============================================
# Engine
# ============================================

class CorrelationEngine:
    """
    Vodi evaluaciju pravila nad svim dolazećim ECSEvent-ima.

    Args:
        rules: lista CorrelationRule instanci. Redosled funkcionalno nije
               bitan; utiče samo na redosled incidenata vraćenih za jedan
               događaj (retko u praksi).
    """

    def __init__(self, rules: Iterable[CorrelationRule]):
        self._rules: List[CorrelationRule] = list(rules)
        # Ključ: (rule_name, subject_key) -> SlidingWindow
        self._windows: dict[Tuple[str, str], SlidingWindow] = {}
        self.stats = EngineStats()

        if not self._rules:
            raise ValueError("CorrelationEngine needs at least one rule")

    @property
    def rules(self) -> List[CorrelationRule]:
        return list(self._rules)

    # ----- main API -----

    def process(self, event: ECSEvent) -> List[Incident]:
        """
        Prosledi događaj svakom relevantnom pravilu. Vraća listu incidenata
        koje je ovaj jedan događaj proizveo (obično 0; ponekad 1; vrlo retko
        više, ako više pravila okine na isti događaj).
        """
        self.stats.events_processed += 1
        incidents: List[Incident] = []
        was_handled = False

        for rule in self._rules:
            subject_key = rule.subject(event)
            if subject_key is None:
                continue
            was_handled = True

            window = self._get_or_create_window(rule, subject_key)
            window.add(event.timestamp, event)

            try:
                incident = rule.evaluate(window, event)
            except Exception:
                # Bagovito pravilo nikad ne sme da obori engine.
                logger.exception(
                    "rule %s raised on event id=%s; skipping",
                    rule.name, event.id,
                )
                continue

            if incident is not None:
                incidents.append(incident)
                self.stats.record_incident(rule.name)

        if not was_handled:
            self.stats.events_skipped += 1

        return incidents

    def prune_stale(self, now: datetime) -> int:
        """
        Izbaci prazne prozore. Prozor je "ustajao" kad su mu svi unosi
        istekli u odnosu na `now`. Vraća broj uklonjenih prozora.
        """
        to_remove = []
        for key, window in self._windows.items():
            window.prune(now)
            if len(window) == 0:
                to_remove.append(key)

        for key in to_remove:
            del self._windows[key]

        self.stats.windows_pruned += len(to_remove)
        return len(to_remove)

    # ----- introspekcija (za testove i debug logovanje) -----

    def window_count(self) -> int:
        return len(self._windows)

    def get_window(
        self, rule_name: str, subject_key: str,
    ) -> Optional[SlidingWindow]:
        return self._windows.get((rule_name, subject_key))

    # ----- internals -----

    def _get_or_create_window(
        self, rule: CorrelationRule, subject_key: str,
    ) -> SlidingWindow:
        key = (rule.name, subject_key)
        window = self._windows.get(key)
        if window is None:
            window = SlidingWindow(rule.window_duration)
            self._windows[key] = window
        return window