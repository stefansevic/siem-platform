# SIEM Platform

Modular SIEM-like platform for web application security event correlation.

This is the prototype developed for the bachelor thesis "Platforma za
praćenje i analizu bezbednosnih događaja u veb aplikacijama" at
University of Novi Sad. The platform ingests heterogeneous logs from
multiple sources, normalizes them to ECS, correlates events against
sliding-window rules (brute-force, directory scanning, account
takeover), and surfaces incidents on a React dashboard.

## Quick Start

Prerequisites: Docker Desktop with Docker Compose v2, Python 3.11+.

```bash
git clone https://github.com/stefansevic/siem-platform.git
cd siem-platform
./scripts/setup.sh
docker compose up -d --build
```

The setup script verifies tooling, creates `.env` from
`.env.example`, and installs the Python packages used by the
experiment framework (`requests`, `pyyaml`, `matplotlib`, `numpy`).
It is idempotent — safe to re-run.

The first Docker build takes 3–5 minutes. Once all services report
healthy:

| Component        | URL                               |
|------------------|-----------------------------------|
| Dashboard        | http://localhost:3000             |
| API Gateway      | http://localhost:8005             |
| Demo webapp      | http://localhost:9000             |
| Nginx (proxy)    | http://localhost:8080             |
| Elasticsearch    | http://localhost:9200             |

Verify the stack with:

```bash
docker compose ps
curl http://localhost:8005/health
```

## Architecture

Five FastAPI microservices behind a Redis Streams message bus:

log sources ──► Ingestor ──► raw_logs (Redis stream)
│
▼
Normalizer ──► Postgres + Elasticsearch
│
▼
normalized_events (Redis stream)
│
▼
Correlator ──► incidents (Redis stream)
│
▼
Alert Manager ──► Postgres + Webhook + Console
│
▼
API Gateway ──► React Dashboard

Postgres is the transactional source of truth. Elasticsearch
mirrors events for full-text search (Search page in the dashboard).
See `DECISIONS.md` for the design rationale behind each component.

## Generating Activity for the Demo

The platform comes with attack simulation scripts that drive the
detection rules end-to-end.

Run a single scenario (resets the database first):

```bash
python3 experiments/run_scenario.py \
  experiments/scenarios/basic_brute_force.yaml --reset-db
```

Run the full experimental suite (76 runs, ~50 minutes):

```bash
python3 experiments/run_all.py
```

Compute Precision/Recall/F1 metrics from completed runs:

```bash
python3 experiments/compute_metrics.py
cat experiments/results/per_rule.csv
```

Generate the six plots used in the thesis:

```bash
for plot in experiments/plots/0*.py; do
  python3 "$plot"
done
```

## Common Issues

**Port already in use.** One of `3000`, `5432`, `6379`, `8005`,
`8080`, `9000`, or `9200` is already taken on the host. Free the
port or override it in `.env`.

**`docker compose up` fails on first run.** Elasticsearch needs
`vm.max_map_count >= 262144`. On Linux:

```bash
sudo sysctl -w vm.max_map_count=262144
```

On Docker Desktop (Windows/macOS), increase memory allocation to at
least 4 GB in Settings → Resources.

**Dashboard shows no data.** Check that all containers are healthy
(`docker compose ps`) and that the demo webapp received traffic.
Generate some via:

```bash
python3 experiments/attacks/traffic_normal.py --duration 30
```

**Search page returns no results.** The API Gateway's Elasticsearch
client may have failed to connect at startup. Restart the gateway:

```bash
docker compose restart api-gateway
```

(Lazy reconnect was added in a recent fix; this should self-heal,
but the manual restart works as a fallback.)

## Resetting the Environment

To wipe all events, incidents, and Elasticsearch indices:

```bash
docker compose exec -T postgres psql -U siem_admin -d siem \
  -c "TRUNCATE incidents, events RESTART IDENTITY CASCADE;"
curl -X DELETE "http://localhost:9200/events-$(date +%Y.%m.%d)"
docker compose exec -T redis redis-cli FLUSHDB
docker compose restart normalizer correlator alert-manager
```

The same workflow runs automatically before every experimental run
when `--reset-db` is passed.

## Repository Layout

services/         five FastAPI microservices
shared/           ECS models, Redis key conventions, ES helpers
infrastructure/   Postgres migrations, Nginx config, Redis config
log-sources/      demo webapp (FastAPI)
frontend/         React + Vite + TypeScript dashboard
experiments/      attack scripts, scenarios, orchestrators, plots
scripts/          setup and operational helpers
docs/             methodology and results documentation
DECISIONS.md      24 ADRs covering all architectural choices

## Documentation

- `DECISIONS.md` — Architecture Decision Records
- `docs/experiments_methodology.md` — experimental setup details
- `docs/experiments_log.md` — results and findings from the 76-run suite
- `docs/experiments_methodology_sr.md` — methodology, Serbian translation
- `docs/experiments_log_sr.md` — results log, Serbian translation

## License

See `LICENSE`.