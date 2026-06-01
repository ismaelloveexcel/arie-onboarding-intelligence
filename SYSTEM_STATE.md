# System State — Arie Leads

_Last verified: 2026-06-01_
_Freeze status: **FROZEN** — all gates passed 2026-06-01_

---

## Verified Flows (tested against live Railway DB, 2026-05-25)

| Flow | Status | Evidence |
|---|---|---|
| GET `/` | ✅ 200 | Queue renders, 837 rows |
| GET `/leads/{id}` | ✅ 200 | Score breakdown + action panel rendered |
| GET `/upload` | ✅ 200 | Upload form renders |
| GET `/audit` | ✅ 200 | Audit log renders |
| GET `/health` | ✅ 200 | `db:connected`, `queue_fresh:true`, `queue_rows:837` |
| POST `/leads/{id}/action` | ✅ 200 + ✓ Saved | Writes `rm_actions`, writes `audit_log` |
| POST `/upload` (CSV) | ✅ 200 + preview | Creates `pending_uploads` row with UUID |
| POST `/upload/{id}/confirm` | ✅ 303 | Inserts rows into `companies` |
| GET `/leads/{nonexistent}` | ✅ 404 | Error handling correct |
| POST `/admin/ch-enrichment` | ✅ 401 unauth / 200 with token | CH enrichment endpoint secured |
| POST `/admin/lei-backfill` | ✅ 401 unauth / 200 with token | LEI backfill endpoint secured |
| Nightly pipeline (GitHub Actions) | ✅ Running | Ran 2026-06-01 03:21–03:30 UTC, 45 UK + 169 MU, 49 scored |
| `ruff check src/ tests/ scripts/` | ✅ 0 errors | CI scope expanded to scripts/ |

---

## Gate Closure Record

| Gate | Resolved | Evidence |
|---|---|---|
| G0 — Baseline | 2026-05-25 | All flows passing end-to-end |
| G1 — Playwright + auto-migrate on deploy | 2026-05-25 | `nixpacks.toml` build confirmed on Railway |
| G2 — Nightly pipeline scheduled | 2026-06-01 | `daily.yml` ran at 03:21 UTC, `nightly_complete` logged |
| G3 — Real actor in audit log | 2026-05-25 | `ACTOR_NAMES` env var + actor cookie live |
| G4 — Railway healthcheck wired | 2026-05-25 | Railway deploy panel shows Healthy |
| Security — Admin endpoints locked | 2026-05-30 | Both `/admin/*` return 401 without token |
| CH enrichment | 2026-05-30 | Officers + PSCs tables live, nightly pipeline calls enrichment batch |

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

---
*See `docs/` for architecture overview, operational runbooks, and architectural decision records.*
