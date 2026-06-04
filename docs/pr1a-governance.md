# PR1a Governance Contract (Implementation Baseline)

PR1a is foundation-only. It must not change queue ordering, ranking behavior, or RM workflow.

## Locked controls

- `SCORING_SHADOW_MODE=true` (compute/store only)
- `SCORING_DISPLAY_ENABLED=false` (no user-facing score rollout)
- Single recompute path: `recompute_lead(lead_id, trigger_type, ...)`
- Deterministic scoring fingerprint:
  - `score_version`
  - `weights_version`
  - `rules_version`
  - `evidence_hash`

## Active lead backfill policy

Active lead = in queue or assigned, not in terminal status list, updated within stale window.

Config:
- `SHADOW_SCORE_ACTIVE_STALE_DAYS`
- `ACTIVE_TERMINAL_STATUSES`

Backfill guardrails:
- `SHADOW_BACKFILL_BATCH_SIZE`
- `SHADOW_BACKFILL_MAX_BATCHES`
- `SHADOW_BACKFILL_LOCK_TIMEOUT_MS`

## Operational visibility query (shadow mode)

```sql
SELECT
  COUNT(*) FILTER (WHERE score_state = 'scored')   AS scored,
  COUNT(*) FILTER (WHERE score_state = 'unscored') AS unscored,
  COUNT(*) FILTER (WHERE score_state = 'failed')   AS failed
FROM companies;
```

```sql
SELECT
  trigger_type,
  status,
  COUNT(*) AS run_count,
  ROUND(AVG(duration_ms)::numeric, 1) AS avg_duration_ms
FROM score_runs
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY trigger_type, status
ORDER BY trigger_type, status;
```

## Rollback

1. Set `SCORING_SHADOW_MODE=false`
2. Redeploy
3. Verify:
   - queue endpoint still sorted by existing `lead_scores`/`queue_snapshot`
   - no new `score_runs` rows after deploy
