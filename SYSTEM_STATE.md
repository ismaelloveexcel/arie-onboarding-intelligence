# System State — Arie Leads

_Last verified: 2026-05-25_
_Freeze status: **NOT FROZEN** — 6 gate items outstanding (see below)_

---

## Verified Flows (tested against live Railway DB, 2026-05-25)

| Flow | Status | Evidence |
|---|---|---|
| GET `/` | ✅ 200 | Queue renders, 100 rows |
| GET `/leads/{id}` | ✅ 200 | Score breakdown + action panel rendered |
| GET `/upload` | ✅ 200 | Upload form renders |
| GET `/audit` | ✅ 200 | Audit log renders |
| GET `/health` | ✅ 200 | `db:connected`, `queue_fresh:true`, `queue_rows:100` |
| POST `/leads/{id}/action` | ✅ 200 + ✓ Saved | Writes `rm_actions`, writes `audit_log` |
| POST `/upload` (CSV) | ✅ 200 + preview | Creates `pending_uploads` row with UUID |
| POST `/upload/{id}/confirm` | ✅ 303 | Inserts rows into `companies` |
| GET `/leads/{nonexistent}` | ✅ 404 | Error handling correct |
| `ruff check src/ tests/` | ✅ 0 errors | — |

---

## Known Limitations (real, not speculative — all must be resolved before freeze)

1. **Scores never re-calculate after first assignment** — `_score_new_companies` only scores companies with no existing score. A `SCORING_VERSION` bump has no effect. Old leads keep stale scores forever. _(Bug — gate item G1)_

2. **Playwright not in Railway build** — `railway.toml` `startCommand` doesn't install Playwright. Mauritius scraper will crash silently on Railway. _(Deployment blocker — gate item G2)_

3. **No alembic migration on deploy** — Schema changes require a manual `alembic upgrade head` SSH session. _(Deployment blocker — gate item G2, same fix)_

4. **Queue never refreshes in production** — No cron or schedule runs `python -m src.pipeline`. Queue will show stale data forever after first deploy. _(Production usefulness blocker — gate item G3)_

5. **`actor` hardcoded as `"system"`** — Every entry in the audit log shows `system`. Audit log is useless for identifying who did what. _(Functional gap — gate item G4)_

6. **No Railway healthcheck configured** — Railway doesn't detect unhealthy deploys. Zero-downtime restarts can't work. _(Deployment gap — gate item G5)_

---

## Not a problem (deferred — do not implement without a new observed failure)

- Unpinned `requirements.txt` — all packages install and run correctly now
- `_parse_upload_csv` type annotation — cosmetic, no runtime effect
- `_render_action_panel` f-string — works correctly; DRY violation only
- Unused tables (`users`, `merge_lineage`) — no code reads/writes them; no harm
- Missing query indexes — 100 rows, no measurable latency
- Mauritius scraper DOM precision — needs manual inspection of live MNS site; not blocking
- Test coverage — 2-user internal tool; manual testing sufficient at this scale

---

## Freeze Rule

> Once G1–G5 are resolved AND `/health` on the Railway URL returns `db:connected, queue_fresh:true`:
> **backend is frozen**.
>
> No further changes unless an item from the table below is triggered by observed production behaviour.

| Allowed trigger | Example |
|---|---|
| Observable failure | endpoint returns 5xx, data corrupted, audit log broken |
| Measurable performance | query >500ms under real load, DB deadlock |
| Security violation | unauthenticated access, injection risk |

**Not a valid trigger:** cleanup, refactor, "could be cleaner", "better architecture", "might scale better", industry best practice.

If a proposed change doesn't map to a row in the above table, write it in `BACKLOG.md` and do not implement it.
