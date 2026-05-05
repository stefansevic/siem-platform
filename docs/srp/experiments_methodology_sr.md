# Eksperimentalna metodologija

## Cilj

Kvantitativno evaluirati tačnost detekcije SIEM platforme kroz
reproducibilne simulacije napada, mereći Precision, Recall i F1
score po pravilu detekcije.

Ovaj dokument opisuje eksperimentalnu postavku. Rezultati se
nalaze u `experiments_log_sr.md`.

## Definicije

Za svaki par (pravilo, run), izlaz sistema se klasifikuje u jednu
od četiri kategorije:

- **True Positive (TP)** — Scenario je očekivao incident za pravilo
  X i sistem ga je generisao.
- **False Positive (FP)** — Scenario nije očekivao incident za
  pravilo X, ali ga je sistem generisao.
- **False Negative (FN)** — Scenario je očekivao incident za pravilo
  X, ali ga sistem nije generisao.
- **True Negative (TN)** — Scenario nije očekivao incident i sistem
  ga nije generisao.

Metrike se izvode po pravilu agregacijom kroz svih 76 runova:

Precision = TP / (TP + FP)
Recall    = TP / (TP + FN)
F1        = 2 * Precision * Recall / (Precision + Recall)

Za negativne scenarije (`expected_incidents: []`) Precision se
definiše kao 1.0 kada je FP=0, kako bi se izbegao slučaj 0/0.

## Eksperimentalni okvir

Okvir se sastoji od tri sloja:

**Skripte za napade** u `experiments/attacks/` — samostalne CLI
alatke koje simuliraju po jedan obrazac napada (`brute_force.py`,
`directory_scan.py`, `account_takeover.py`, `traffic_normal.py`).

**Scenariji** u `experiments/scenarios/*.yaml` — deklarativni opisi
eksperimentalnog runa. Svaki scenario specificira očekivane
incidente, pojedinačne korake napada (sekvencijalne ili paralelne)
i pauze između koraka. Definisano je dvanaest scenarija: tri
pozitivna ("basic") napada, dva napada izmešana sa legitimnim
saobraćajem, četiri negativna scenarija koji ne treba da pokrenu
detekciju, dva granična slučaja koji testiraju logiku thresholda
i kliznog prozora, i jedan realističan kombinovani scenario.

**Orkestratori** — `run_scenario.py` izvršava jedan scenario i
beleži i ground truth (očekivani ishod iz YAML-a) i stvarno
generisane incidente (preuzete sa API Gateway-a). Oba se upisuju
u `experiments/runs/<id>.json`. `run_all.py` pokreće ceo paket
prema `run_all.config.yaml`.

## Resetovanje baze između runova

Svaki run počinje sa `--reset-db`, što izvršava potpun reset
pipeline-a:

1. `TRUNCATE incidents, events RESTART IDENTITY CASCADE` na Postgres-u
2. `DELETE` svih `events-*` indeksa na Elasticsearch-u
3. `FLUSHDB` na Redis-u (briše streamove i consumer grupe)
4. `docker compose restart` Normalizer i Correlator servisa,
   kako bi se ponovo kreirale consumer grupe i obrisalo
   in-memory stanje kliznih prozora
5. Pauza od 3 sekunde da se servisi reconnect-uju

Reset je neophodan jer:

- Prozor deduplikacije Alert Manager-a (5 minuta po ADR-016) bi
  inače spojio incidente iz uzastopnih runova u jedan red baze,
  proizvodeći False Negatives za svaki run posle prvog.
- In-memory stanje kliznog prozora Correlator-a (po ADR-013) bi
  prenelo događaje iz jednog runa u sledeći, što bi iskrivilo
  threshold-ove.

## Životni ciklus runa

Nakon što scenario završi YAML korake, `run_scenario.py` čeka
dodatne 3 sekunde i zatim pita API Gateway za incidente
detektovane u vremenskom prozoru runa (sa tolerancijom od 5
sekundi sa svake strane). Rezultat se ugrađuje u ground-truth JSON
kao `actual_incidents`. Ovim se rezultat runa "zaključava" pre
nego što sledeći reset obriše bazu.

`compute_metrics.py` čita `actual_incidents` direktno iz svakog
ground-truth fajla, umesto da ponovo pita API. Ovo razdvaja
računanje metrika od trenutnog stanja baze i čini metrički
pipeline determinističkim.

## Logika podudaranja

Lista `expected_incidents` scenarija i lista `actual_incidents`
runa se uparuju na nivou **rule_name**, sa `min_count`
semantikom: očekivani unos se računa kao True Positive ako
detektovani incidenti uključuju najmanje `min_count` incidenata sa
istim `rule_name`, bez obzira na izvornu IP, ciljanog korisnika ili
druge metapodatke incidenta.

Ovaj izbor izbegava krhkost zbog Docker network artefakata (svaki
incident ima `source_ip` = `172.18.0.1` po default-u), a i dalje
tačno detektuje TP-ove i FP-ove.

## Broj runova

Ukupno 76 runova je raspoređeno asimetrično kroz scenarije, kako
bi se balansirala statistička snaga sa vremenom izvršavanja:

- 10 runova svaki za četiri najvažnija scenarija
  (`basic_brute_force`, `basic_dir_scan`, `basic_ato`,
  `only_normal_traffic`)
- 5 runova svaki za scenarije srednje stohastičnosti
  (`brute_force_with_noise`, `dir_scan_with_noise`,
  `near_threshold_brute_force`, `slow_burst_brute_force`,
  `nat_false_positive`, `mixed_legitimate_and_attack`)
- 3 runa svaki za potpuno deterministične negativne scenarije
  (`low_and_slow`, `distributed_brute_force`)

Ukupno vreme izvršavanja: približno 47 minuta sekvencijalno.

## Parametri pravila detekcije

Tri korelaciona pravila koriste industrijski standardne thresholde
(po ADR-014):

- **brute_force**: 5 neuspelih autentikacija u 60 sekundi,
  grupisano po izvornoj IP (po ADR-022).
- **directory_scanning**: 20 različitih 404 putanja u 60 sekundi,
  grupisano po izvornoj IP.
- **account_takeover**: 5 neuspelih autentikacija praćenih
  uspešnom prijavom u 600 sekundi, grupisano po (izvorna IP,
  korisnik).

Ove vrednosti nisu tunirane na eksperimentalnim podacima; fiksirane
su tokom Nedelje 8 na osnovu industrijskih SOC playbook-ova pre
nego što je bilo koji run izvršen.

## Izlazni artefakti

Svako izvršavanje paketa proizvodi:

- `experiments/runs/*.json` — jedan ground-truth JSON po runu
- `experiments/results/per_run.jsonl` — metrike po runu
- `experiments/results/per_rule.csv` — agregirane metrike po pravilu
- `experiments/results/per_scenario.csv` — mean ± std po scenariju
- `experiments/run_all.log` — vremenski log paketa
- `experiments/results/plots/*.png` — šest grafikona za Poglavlje 6