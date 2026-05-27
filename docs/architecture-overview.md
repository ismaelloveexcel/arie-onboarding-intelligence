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

## External dependencies

- (List API keys, external services, scraping targets, and their criticality)

## Known constraints and operational notes

- (Anything that isn't obvious from the code)
