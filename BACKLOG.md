# Backlog — Arie Leads

Ideas captured here instead of implemented. Nothing in this file is planned work.
An item moves out of here ONLY when it maps to an observed production failure.

## Captured

- Re-score on version change (`_score_new_companies` in pipeline.py: when `SCORING_VERSION` is bumped, existing scores need `is_current=FALSE` + new row inserted) — only triggers when you intentionally change the scoring version; no current user-facing impact
- Fix `_parse_upload_csv` return type annotation (`tuple[list[dict]…]` → `tuple[list[str]…]`) — cosmetic
- Extract RM action panel to `_action_panel.html` partial — DRY improvement, no current bug
- Drop unused DB tables (`users`, `merge_lineage`) and `canonical_company_id` column — no harm while unused
- Add missing query indexes (`companies_normalised_name`, `audit_log_created_at`, `audit_log_entity`) — revisit if query latency becomes observable
- Mauritius scraper DOM hardening — requires manual inspection of MNS site; do only if Mauritius data shows zero rows for 3+ consecutive nights
- Route smoke tests (`tests/test_routes.py`) — add if a regression is missed in production
- Upload round-trip test (`tests/test_upload.py`) — add if upload bugs are reported
- Pipeline smoke test (`tests/test_pipeline.py`) — add if pipeline failures are missed
- `itsdangerous`-signed actor cookie — revisit if deployment grows beyond 2 trusted users
