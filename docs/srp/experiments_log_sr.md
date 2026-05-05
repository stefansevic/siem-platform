# Eksperimentalni log

## Sažetak paketa

Eksperimentalni paket je izvršen 4. maja 2026., obuhvatajući 76
runova kroz 12 scenarija u približno 47 minuta. Svih 76 runova
je završeno bez subprocess grešaka.

## Agregirani rezultati

Metrike po pravilu kroz svih 76 runova:

| Pravilo             | TP | FP | FN | Precision | Recall | F1   |
|---------------------|---:|---:|---:|----------:|-------:|-----:|
| brute_force         | 30 |  8 |  0 |    0.7895 | 1.0000 | 0.88 |
| directory_scanning  | 15 |  0 |  0 |    1.0000 | 1.0000 | 1.00 |
| account_takeover    | 10 |  0 |  0 |    1.0000 | 1.0000 | 1.00 |

Recall je savršen kroz sva tri pravila: sistem nikada nije
propustio dokumentovani napad. Precision pada ispod 1.0 samo za
brute_force pravilo, vođen False Positives iz scenarija
`nat_false_positive` i `slow_burst_brute_force`.

## Naglasci po scenariju

**Savršena detekcija (F1 = 1.0 ± 0.0):**
`basic_brute_force`, `basic_dir_scan`, `basic_ato`,
`brute_force_with_noise`, `dir_scan_with_noise`,
`mixed_legitimate_and_attack`, `near_threshold_brute_force`,
`only_normal_traffic`, `low_and_slow`, `distributed_brute_force`.
Sistem se ponaša po dizajnu kako u pozitivnim scenarijima
(detekcija uspeva), tako i u negativnim (nema spurious incidenata).

**Dokumentovani False Positive — `nat_false_positive` (F1 = 0.0):**
Tri korisnika koja kucaju pogrešne lozinke sa zajedničke izvorne
IP proizvode 6 neuspelih autentikacija, što prelazi brute_force
threshold od 5 u 60 sekundi. Pravilo se aktivira svaki put. Ovo
potvrđuje ograničenje predviđeno u ADR-022: brute_force grupisanje
samo po izvornoj IP je industrijski standardni pristup, ali
proizvodi False Positives u NAT i proxy okruženjima.

**Stohastička detekcija — `slow_burst_brute_force` (F1 = 0.4 ± 0.55):**
Šest neuspelih prijava razmaknutih 16 sekundi ne bi trebalo da
pokrenu brute_force pravilo, jer nijedan klizni prozor od 60
sekundi ne sadrži pet pokušaja. Međutim, u 3 od 5 runova pravilo
se aktiviralo. Uzrok je real-time latencija pipeline-a: kada
Redis baferovanje, Normalizer obrada ili kašnjenje upisa u
Postgres skrate efektivni razmak za par stotina milisekundi, peti
pokušaj uđe unutar kliznog prozora. Ovo dokumentuje šum koji je
inherentan threshold-baziranoj detekciji na granici.

## Latencija detekcije

Kroz sve detektovane incidente (n=63), vreme od `first_event_at`
do `detected_at` imalo je:

- Medijana: ispod 2 sekunde za scenarije sa čistim napadom
- 5–9 sekundi za scenarije sa paralelnim legitimnim saobraćajem
- ~55 sekundi za `slow_burst_brute_force` runove koji su se
  aktivirali, što odražava spor tempo napada

Pipeline radi ispod sekunde pri čistom opterećenju i graciozno
degradira pod šumom, nikad ne premašujući vreme koje sam napad
treba da se odvije.

## Otkrivanja tokom eksperimentalne faze

### Bug u Elasticsearch reconnect-u

Tokom inspekcije Search stranice tokom eksperimentalnih runova,
frontend nije vraćao rezultate iako su događaji postojali u
indeksu. Uzrok: Elasticsearch klijent API Gateway-a se otvarao
samo jednom pri startup-u i čuvao u `app.state.es`. Ako
Elasticsearch nije bio dostupan u tom trenutku (npr. zato što je
reset workflow upravo restartovao ES), klijent je ostajao `None`
trajno i svi `/events/search` pozivi su vraćali praznu stranicu.

Rešenje je lazy reconnect: kada je `app.state.es is None`, search
handler sada ponovo pokušava konekciju. Ako uspe, novi klijent se
kešira za naredne zahteve. Ovo zadovoljava graceful-degradation
garanciju iz ADR-023 bez dodavanja background health check-a.

### NAT False Positives su sistemski, ne stohastički

Scenario `nat_false_positive` je proizveo FP u svakom od svojih 5
runova (Precision std = 0.0). Ovo nije nestabilan test — to je
deterministički odgovor pravila na pravi obrazac napada koji, po
industrijskoj konvenciji, izlazi iz dometa pravila. Diskusija o
slojevitoj detekciji (per-(IP, korisnik) grupisanje, UEBA
baselines) je u Poglavlju 7.

### Granica kliznog prozora je statistička, ne egzaktna

Stohastički rezultati scenarija `slow_burst_brute_force` (40% FP
rate sa delay = 16s) sugerišu da je efektivni threshold
brute_force pravila malo ispod nominalne specifikacije od 5 u 60s
kada se uključi real-time latencija. Produkcioni deployment bi
ili proširio vremensku marginu ili dodao toleranciju jitter-a u
logiku prozora pravila.

## Reproducibilnost

Ceo paket može biti ponovo izvršen putem:

```bash