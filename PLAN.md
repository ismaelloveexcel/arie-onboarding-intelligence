# Deployment Contract — Arie Leads

**This file is a contract, not a roadmap.**

The system is already working end-to-end (see `SYSTEM_STATE.md` for proof). There are exactly **5 gate items** blocking production freeze. Nothing else is allowed to be worked on until the gates pass and the system is declared frozen.

Engineering rules still apply: sync FastAPI, raw psycopg, HTMX + Jinja, deterministic scoring, no Redis/Celery/AI/microservices. If a proposed change isn't listed here as a gate item, it goes to `BACKLOG.md` instead.

**Freeze rule:** once all gate items pass and `/health` on Railway returns `queue_fresh:true`, this file is done. No further changes without an observed production failure.

How to use: do items **in order**. Each has a precise acceptance check. Do not start the next item until the current one's check passes.

---

## ✅ Gate 0 — Baseline (DONE)

Verified 2026-05-25. All flows passing. See `SYSTEM_STATE.md`.

---

## Gate Items (do in order — these are the ONLY remaining work)

### ✅ G1 — Railway build: install Playwright + auto-migrate on deploy

**Done 2026-05-25.**
- `nixpacks.toml` created: pip install + playwright install in build phase; `alembic upgrade head && uvicorn` as start cmd.
- `railway.toml`: `startCommand` removed (nixpacks takes over); healthcheck wired (see G4).

**Accept:** Next Railway deploy build log shows `playwright install` succeeded; startup log shows `alembic upgrade head` before uvicorn binds.

---

### [ ] G2 — Schedule the nightly pipeline

**Why it's a gate:** Without a schedule, the queue never refreshes on Railway. The tool shows stale data permanently.

**Option A (preferred if Railway Cron is on your plan):** Add a Railway Cron service in the same project. Start command: `python -m src.pipeline`. Schedule: `0 2 * * *` UTC. No code changes needed.

**Option B (implemented — use if Cron not available):** Route `POST /internal/run-pipeline` is now in [src/main.py](src/main.py). Workflow [.github/workflows/nightly.yml](.github/workflows/nightly.yml) triggers it at 02:00 UTC.

Required env vars / GitHub secrets:
- `PIPELINE_SECRET` — set in Railway env vars AND as a GitHub Actions secret
- `RAILWAY_APP_URL` — set as a GitHub Actions secret (e.g. `https://your-app.railway.app`)

**Accept:** One manual trigger (either Railway Cron run or `workflow_dispatch` from GitHub Actions UI) produces a `nightly_complete` log line with non-zero `companies_fetched_uk` and `/health` shows updated `queue_refreshed_at`.

---

### [ ] G3 — Real `actor` in audit log

**Done 2026-05-25.**
- `ACTOR_NAMES` env var added to [src/config.py](src/config.py).
- "Acting as" dropdown added to nav in [src/templates/base.html](src/templates/base.html).
- `POST /me` route in [src/main.py](src/main.py) sets actor cookie.
- All hardcoded `"system"` replaced with `request.cookies.get("actor", "unknown")`.
- Actor context passed to all TemplateResponse calls.

Required env var: `ACTOR_NAMES` — comma-separated names (e.g. `Isuda,Partner`).

**Accept:** Change actor in the nav, perform an RM action, `/audit` shows the chosen name in the Actor column.

---

### ✅ G4 — Wire healthcheck to Railway

**Done 2026-05-25.**
- [railway.toml](railway.toml): `healthcheckPath = "/health"` and `healthcheckTimeout = 10` added.

**Accept:** Railway deploy panel shows "Healthy" after next deploy.

---

## Pre-freeze checklist (run this before declaring the system frozen)

- [x] G1 — nixpacks.toml + Railway build fixed.
- [x] G2 — Nightly pipeline confirmed running (2026-06-01 03:21 UTC, SUCCESS).
- [x] G3 — Actor dropdown live, audit shows real names.
- [x] G4 — Railway healthcheck wired.
- [x] `ruff check src/ tests/ scripts/` → 0 errors.
- [x] Restart local uvicorn, all flows from `SYSTEM_STATE.md` still pass.
- [x] Railway deploy is Healthy.
- [x] Nightly pipeline ran → `queue_fresh: true` in `/health`.
- [x] Commit: `"ship: all production gates passed"`.
- [x] Update `SYSTEM_STATE.md`: freeze status set to **FROZEN** 2026-06-01.

---

## After freeze — first week

Open `/health` each morning for 7 days. If `queue_fresh: false` two mornings in a row → cron broke; debug that specific thing. Do not open unrelated code.

---

## Backlog (write ideas here, implement nothing until a gate fails)

_Empty for now. Add items here instead of implementing them._

---

## Hard rule

Before any change to backend code is started, answer: **"Which production gate does this fix?"**
If the answer is "none", write it in the Backlog section above and close the editor.
