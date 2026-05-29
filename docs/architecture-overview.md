# Architecture Overview — Onboarding Intelligence Platform

## System purpose

<!-- Fill in: what problem this solves for Arie Finance, what it replaces or supplements -->

## Data sources

- **UK Companies House API** — (status, notes)
- **Mauritius MNS portal scraping** — (status, notes)
- **DIFC** — currently parked (see [decisions/0001-difc-gibraltar-parked.md](decisions/0001-difc-gibraltar-parked.md))
- **Gibraltar** — currently parked (see [decisions/0001-difc-gibraltar-parked.md](decisions/0001-difc-gibraltar-parked.md))

## Pipeline stages

Describe each stage in order.

- **Ingest** — (description)
- **Score** — (description)
- **Route** — (description)
- **Deliver** — (description)

## Routing rules

Which leads go to which team members and under what conditions.

- **Ismael** — (criteria)
- **Tasneem** — (criteria; note: direct clients)
- **Aisha** — (criteria; note: introducers)
- **Stephen** — (criteria; note: introducers)
- **Rajesh** — (criteria; note: introducers)

## Deployment topology

- **Platform:** Railway
- **Runtime:** FastAPI + uvicorn
- **Database:** Postgres (migrations via Alembic)
- **Build:** nixpacks (`nixpacks.toml`)
- **Config:** (fill in env var summary)

## Nightly pipeline execution

The Railway service hosts the FastAPI web app only — it does **not** run the
ingestion pipeline. The nightly pipeline runs as a scheduled GitHub Actions
workflow (`.github/workflows/daily.yml`, cron `0 2 * * *` UTC) which invokes
`python -m src.pipeline` against the production database using repository
secrets. If the queue stops refreshing, check GitHub Actions first; Railway
logs will not show pipeline activity. The Railway-side advisory lock and
`pipeline_runs` reaper still apply because both jobs connect to the same
Postgres instance.

## External dependencies

- (List API keys, external services, scraping targets, and their criticality)

## Known constraints and operational notes

- (Anything that isn't obvious from the code)
