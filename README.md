# Arie Leads Intelligence Platform

Internal lead management tool for Arie Finance. Ingests company data from Companies House (UK) and the Mauritius Business Registry, scores and tiers leads, and provides an RM workflow for status tracking and audit logging.

**Live:** https://arie-onboarding-intelligence-production.up.railway.app

---

## Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Jinja2 + HTMX |
| Database | PostgreSQL (psycopg3 binary) |
| Migrations | Alembic |
| Scraping | Playwright (Mauritius) |
| Deployment | Railway (EU West) |
| Python | 3.12 |

---

## Features

- **Lead Queue** — paginated, filterable by tier, jurisdiction, assigned RM, and status
- **Lead Detail** — score breakdown, reason codes, RM action panel (assign, status, notes, follow-up date)
- **Audit Log** — every RM action is logged with actor, old value, new value, and IP
- **CSV Upload** — upload a spreadsheet of companies for preview and confirmation
- **Nightly Pipeline** — ingests from Companies House API + Mauritius scraper, scores, and refreshes the queue
- **Shadow Scoring Foundation (PR1a)** — computes versioned shadow scores and evidence in parallel, without changing queue ordering
- **Health endpoint** — `GET /health` returns DB connection status and queue freshness

---

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in values:

```
DATABASE_URL=postgresql://...
COMPANIES_HOUSE_API_KEY=...
ACTOR_NAMES=Name1,Name2
APP_ENV=development
```

Run migrations:

```bash
alembic upgrade head
```

Start the server:

```bash
uvicorn src.main:app --reload --port 8002
```

---

## Running Tests

```bash
.venv\Scripts\pytest -q
.venv\Scripts\ruff check src/ tests/
```

All 17 tests must pass and ruff must report no errors before any commit.

---

## Deployment (Railway)

Deployments are triggered manually via:

```bash
railway up --detach
```

Railway runs `alembic upgrade head` before starting uvicorn (configured in `nixpacks.toml`).

---

## Project Structure

```
src/
  main.py          # All FastAPI routes
  pipeline.py      # Nightly ingestion + scoring pipeline
  scoring.py       # Lead scoring logic
  config.py        # Env var loading
  db.py            # DB connection context manager
  ingestion/
    companies_house.py   # UK Companies House API ingestion
    mauritius.py         # Mauritius Business Registry scraper
  templates/       # Jinja2 HTML templates
  static/          # CSS
migrations/        # Alembic migration versions
tests/             # pytest test suite
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string consumed by the app/tests runtime. In CI, `test.yml` injects the `DATABASE_URL_TEST` secret into this variable. For local `pytest`, point this at a non-production DB. |
| `DATABASE_URL_TEST` | Yes (CI secret) | GitHub Actions secret name for the non-production CI test DB URL. CI maps it to `DATABASE_URL`; runtime code does not read `DATABASE_URL_TEST` directly. |
| `COMPANIES_HOUSE_API_KEY` | Yes | Companies House API key |
| `RM_NAMES` | Yes | Comma-separated RM names for the lead "Assign To" dropdown |
| `ACTOR_NAMES` | Yes | Comma-separated names for the nav "Acting as" dropdown |
| `APP_ENV` | No | One of `development` or `production`. Set to `production` to disable `/docs`. |
| `PIPELINE_SECRET` | No | Shared secret for `POST /internal/run-pipeline` |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `ALLOWED_DB_HOSTS` | No | Comma-separated host substrings. If set, the host parsed from `DATABASE_URL` must contain one of these substrings or the app refuses to start. Recommended in every deployed environment (prod, CI, local) to prevent accidental cross-environment DB connections. Leave unset to disable the guard. |
| `SCORING_SHADOW_MODE` | No | Enables PR1a shadow scoring compute path (default: `true`). Does not change user-facing ranking. |
| `SCORING_DISPLAY_ENABLED` | No | Enables user-visible display of shadow scoring artifacts (default: `false`). Keep disabled in PR1a. |
| `SHADOW_SCORE_ACTIVE_STALE_DAYS` | No | Active lead freshness window for shadow backfill selection (default: `120`). |
| `SHADOW_BACKFILL_BATCH_SIZE` | No | Maximum leads processed per shadow backfill batch (default: `100`). |
| `SHADOW_BACKFILL_MAX_BATCHES` | No | Maximum backfill batches per run to cap load (default: `20`). |
| `SHADOW_BACKFILL_LOCK_TIMEOUT_MS` | No | Per-transaction lock timeout for shadow backfill operations (default: `3000`). |
| `ACTIVE_TERMINAL_STATUSES` | No | Explicit comma-separated terminal statuses excluded from "active lead" shadow backfill. |

CI must use a GitHub Actions secret named exactly `DATABASE_URL_TEST`, and `test.yml` must inject it into `DATABASE_URL`. For local testing, set `DATABASE_URL` directly to a non-production DB. The nightly pipeline workflow (`daily.yml`) is currently the only workflow permitted to use the production `DATABASE_URL` secret.

## PR1a Operational Notes

- Shadow scoring runs via a single recompute contract (`recompute_lead`) and writes to:
  - `lead_signal_scores` (versioned component/final scores)
  - `lead_score_evidence` (evidence payload + why output)
  - `score_runs` (triggered execution audit)
- Manual active-lead backfill endpoint:
  - `POST /admin/shadow-scoring/backfill`
  - bearer auth via `ADMIN_TOKEN`
- Rollback: set `SCORING_SHADOW_MODE=false` and redeploy. Existing queue ordering is unaffected because queue snapshot still reads `lead_scores`.
