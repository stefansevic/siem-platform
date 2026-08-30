"""
Korelaciona pravila.

Svako pravilo je samostalna klasa sa dve odgovornosti:

    subject(event)   -> str | None
        Kaže o kom "akteru" je događaj. Engine grupiše događaje po
        subjektu, pa svaki subjekat dobije svoj SlidingWindow. Vraća
        None ako događaj nije relevantan za ovo pravilo (npr. uspešan
        login nije bitan za brute-force detekciju).

    evaluate(window, event) -> Incident | None
        Na osnovu postojećeg prozora za taj subjekat i tek dodatog
        događaja, odlučuje da li je pravilo okinulo. Vraća popunjen
        Incident kad pogodi, ili None.

Engine ne zna šta koje pravilo traži. Samo prolazi kroz listu pravila,
prosleđuje događaje i dalje šalje incidente.

Napomene o dizajnu:
    * Pravila se instanciraju jednom, na startu, sa svojim pragovima.
      Menjanje pragova je promena konfiguracije, ne koda.
    * Pravila su bez I/O. Samo gledaju SlidingWindow koji su dobila i
      vrate rezultat. Čuvanje i obaveštavanje su posao engine-a i
      Alert Managera.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Optional
from uuid import uuid4

from shared.ecs_models import (
    ECSEvent,
    EventCategory,
    EventOutcome,
    Incident,
    IncidentSeverity,
)

from app.windows import SlidingWindow


# ============================================
# Base class
# ============================================

class CorrelationRule(ABC):
    """Apstraktna osnova za sva korelaciona pravila."""

    name: str
    version: str = "1.0"
    severity: IncidentSeverity
    window_duration: timedelta

    @abstractmethod
    def subject(self, event: ECSEvent) -> Optional[str]:
        """
        Vrati ključ grupisanja za ovaj događaj, ili None ako pravilo
        uopšte ne mari za ovaj događaj.
        """

    @abstractmethod
    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        """
        Primeni logiku pravila. Dati prozor sadrži sve relevantne ranije
        događaje za ovaj subjekat (već filtrirane kroz subject()), sa
        novim događajem već dodatim na kraj.
        """

    # ----- shared helper -----

    def _build_incident(
        self,
        window: SlidingWindow,
        event: ECSEvent,
        subject_key: str,
        details: Optional[dict] = None,
    ) -> Incident:
        """
        Zajednički kostur Incident-a. Podklase popune `details` sa
        kontekstom specifičnim za pravilo.
        """
        contributing = [e.id for e in window.events() if isinstance(e, ECSEvent)]
        return Incident(
            id=uuid4(),
            rule_name=self.name,
            rule_version=self.version,
            severity=self.severity,
            first_event_at=window.first_timestamp() or event.timestamp,
            last_event_at=event.timestamp,
            source_ip=event.source_ip,
            target_user_name=event.user_name,
            event_count=window.count(),
            details=details or {},
            contributing_events=contributing,
        )


# ============================================
# Rule 1: Brute-Force Detection
# ============================================

class BruteForceRule(CorrelationRule):
    """
    Okida kad jedna source IP napravi previše neuspelih pokušaja
    autentifikacije u kratkom prozoru.

    Specifikacija: X neuspeha sa iste source IP unutar T minuta.
    Podrazumevano iz projektnog zadatka: X = 5, T = 1 minut.

    Ideja: klasičan napad pogađanja lozinke. Pravilo gleda
    `event.category = authentication` I `event.outcome = failure`,
    grupisano po `source.ip`. Uspešni login-i i ne-auth događaji se
    potpuno ignorišu (subject() vrati None).
    """

    name = "brute_force"
    severity = IncidentSeverity.HIGH

    def __init__(self, *, threshold: int = 5, window: timedelta = timedelta(minutes=1)):
        if threshold < 2:
            raise ValueError("threshold must be at least 2")
        self.threshold = threshold
        self.window_duration = window

    def subject(self, event: ECSEvent) -> Optional[str]:
        if event.event_category != EventCategory.AUTHENTICATION.value:
            return None
        if event.event_outcome != EventOutcome.FAILURE.value:
            return None
        if event.source_ip is None:
            return None
        return str(event.source_ip)

    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        # Prozor već sadrži samo neuspehe za ovu IP, jer je subject()
        # sve ostalo odfiltrirao.
        if window.count() < self.threshold:
            return None

        # Različiti pokušani username-i su koristan forenzički kontekst.
        usernames = sorted({
            e.user_name for e in window.events()
            if isinstance(e, ECSEvent) and e.user_name
        })
        return self._build_incident(
            window=window,
            event=event,
            subject_key=str(event.source_ip),
            details={
                "threshold": self.threshold,
                "window_seconds": int(self.window_duration.total_seconds()),
                "failure_count": window.count(),
                "distinct_usernames": usernames,
            },
        )


# ============================================
# Rule 2: Resource Enumeration (Directory Scanning)
# ============================================

class DirectoryScanningRule(CorrelationRule):
    """
    Okida kad jedna source IP proba mnogo različitih putanja i dobija
    404 odgovore, PRAĆENE neovlašćenim pristupom (403).

    veliki broj 404 grešaka sa iste source IP
    praćenih neovlašćenim pristupom (403) unutar prozora.

    Ideja: alati kao DirBuster/Gobuster napamet probaju česte putanje
    tražeći skrivene endpointe (404), a zatim pokušavaju pristup
    zaštićenom resursu (403). Obeležje je širok spektar RAZLIČITIH 404
    putanja praćen bar jednim 403 odgovorom. Brojanje različitih URL-ova
    (umesto ukupnih 404) smanjuje lažne alarme od pogrešno podešenog
    klijenta koji ponavlja isti pokvaren URL; uslov 403 realizuje
    višestepeni obrazac iz specifikacije.
    """

    name = "directory_scanning"
    severity = IncidentSeverity.MEDIUM

    def __init__(
        self,
        *,
        threshold: int = 20,
        window: timedelta = timedelta(seconds=60),
    ):
        if threshold < 2:
            raise ValueError("threshold must be at least 2")
        self.threshold = threshold
        self.window_duration = window

    def subject(self, event: ECSEvent) -> Optional[str]:
        # U prozor ulaze i 404 (skeniranje) i 403 (neovlašćen pristup koji
        # prati skeniranje). Grupisanje je po source IP.
        if event.http_response_status_code not in (404, 403):
            return None
        if event.source_ip is None:
            return None
        if not event.url_path:
            return None
        return str(event.source_ip)

    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        distinct_404 = {
            e.url_path for e in window.events()
            if isinstance(e, ECSEvent) and e.url_path
            and e.http_response_status_code == 404
        }
        forbidden = [
            e for e in window.events()
            if isinstance(e, ECSEvent) and e.http_response_status_code == 403
        ]
        # veliki broj različitih 404 PRAĆENIH neovlašćenim
        # pristupom (403). Oba uslova moraju biti ispunjena.
        if len(distinct_404) < self.threshold:
            return None
        if not forbidden:
            return None

        return self._build_incident(
            window=window,
            event=event,
            subject_key=str(event.source_ip),
            details={
                "threshold": self.threshold,
                "window_seconds": int(self.window_duration.total_seconds()),
                "distinct_paths_count": len(distinct_404),
                "forbidden_count": len(forbidden),
                "sample_paths": sorted(distinct_404)[:20],
            },
        )


# ============================================
# Rule 3: Account Takeover Pattern
# ============================================

class AccountTakeoverRule(CorrelationRule):
    """
    Okida kad niz neuspelih login-a za određeni nalog sa jedne source IP
    bude praćen uspešnim login-om za taj isti nalog ubrzo posle.

    neuspesi praćeni uspehom za isti (source_ip, username)
    par unutar kratkog prozora.

    Ideja: napadač pogodi iz N+1. pokušaja. Čist brute force (samo neuspesi)
    propušta trenutak kad proboj uspe; ovo pravilo eksplicitno hvata uspeh
    koji sledi. Uparivanje po username-u sprečava lažne alarme kad više
    legitimnih korisnika deli istu IP (NAT, kancelarijski gateway) - kad
    troje kucaju greškom, a četvrti se normalno uloguje, to nije ATO.

    Implementacija: subject() prima I neuspehe I uspehe za istu source IP,
    pa prozor drži ceo niz. evaluate() okida na uspeh kad mu je unutar
    prozora prethodilo bar `failure_threshold` neuspeha ZA TAJ USERNAME.
    """
    name = "account_takeover"
    severity = IncidentSeverity.CRITICAL
    

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        window: timedelta = timedelta(minutes=10),
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.failure_threshold = failure_threshold
        self.window_duration = window

    def subject(self, event: ECSEvent) -> Optional[str]:
        if event.event_category != EventCategory.AUTHENTICATION.value:
            return None
        if event.source_ip is None:
            return None
        # Bitni su i uspesi i neuspesi; ishod je već odredio mapper.
        if event.event_outcome not in (
            EventOutcome.SUCCESS.value, EventOutcome.FAILURE.value,
        ):
            return None
        return str(event.source_ip)

    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        # Okida samo uspeh; inače samo nakupljamo stanje.
        if event.event_outcome != EventOutcome.SUCCESS.value:
            return None

        # Uparivanje po username-u: ATO traži da i neuspeli pokušaji I
        # uspešan login ciljaju isti nalog. Bez ovoga, troje ljudi koji
        # pogreše šifru dok se četvrti legitimno uloguje bi okinuli
        # lažan ATO.
        target_user = event.user_name
        if target_user is None:
            return None

        prior_failures = [
            e for e in list(window.events())[:-1]
            if isinstance(e, ECSEvent)
            and e.event_outcome == EventOutcome.FAILURE.value
            and e.user_name == target_user
        ]
        if len(prior_failures) < self.failure_threshold:
            return None

        return self._build_incident(
            window=window,
            event=event,
            subject_key=str(event.source_ip),
            details={
                "failure_threshold": self.failure_threshold,
                "window_seconds": int(self.window_duration.total_seconds()),
                "preceding_failure_count": len(prior_failures),
                "successful_user_name": event.user_name,
                "attempted_usernames": sorted({
                    f.user_name for f in prior_failures if f.user_name
                }),
            },
        )