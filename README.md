# Arie Onboarding Intelligence Platform

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
| `DATABASE_URL` | Yes | PostgreSQL connection string for the **production** runtime and the nightly pipeline. Never used by CI. |
| `DATABASE_URL_TEST` | Yes (CI / local tests) | PostgreSQL connection string for a **non-production** database used by the test workflow and local `pytest`. Must point at an isolated throwaway DB. |
| `COMPANIES_HOUSE_API_KEY` | Yes | Companies House API key |
| `ACTOR_NAMES` | Yes | Comma-separated RM names shown in nav |
| `APP_ENV` | No | One of `development` or `production`. Set to `production` to disable `/docs`. |
| `PIPELINE_SECRET` | No | Shared secret for `POST /internal/run-pipeline` |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

CI must use `DATABASE_URL_TEST`. The GitHub Actions secret name must be exactly `DATABASE_URL_TEST`. The nightly pipeline workflow (`daily.yml`) is currently the only workflow permitted to use the production `DATABASE_URL` secret.
