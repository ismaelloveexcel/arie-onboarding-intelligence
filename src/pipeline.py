"""
Nightly pipeline orchestrator.
Run directly: python -m src.pipeline
Or via GitHub Actions cron.
"""

import logging
import time

from src.config import CH_ENRICHMENT_BATCH_SIZE
from src.db import get_conn
from src.ingestion.companies_house import (
    fetch_uk_incorporations,
    run_ch_enrichment_batch,
)
from src.ingestion.gleif import fetch_gleif_registrations
from src.ingestion.lei_backfill import backfill_lei_company_links
from src.ingestion.mauritius import fetch_mauritius_incorporations
from src.scoring import (
    SCORING_VERSION,
    build_reason_summary,
    build_why_reasons,
    calculate_arie_fit,
    calculate_freshness,
    calculate_keyword_score,
    calculate_score,
    compute_priority_score,
    derive_enrichment_tier,
    derive_lead_readiness,
    derive_reachability_status,
    load_lead_keywords,
    load_priority_weights,
)

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_ID = 12345


def _acquire_lock(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
        return cur.fetchone()[0]


def _release_lock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))


def _score_new_companies(conn) -> int:
    """Score companies that have no current score or whose current score
    was produced by a different scoring_version. Returns count scored."""
    weights = load_priority_weights(conn)
    keywords = load_lead_keywords(conn)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                   c.sic_codes, c.incorporation_date, c.website
            FROM companies c
            WHERE NOT EXISTS (
                SELECT 1 FROM lead_scores ls
                WHERE ls.company_id = c.id
                  AND ls.is_current = TRUE
                  AND ls.scoring_version = %s
            )
            """,
            (SCORING_VERSION,),
        )
        rows = cur.fetchall()

    count = 0
    for row in rows:
        company_id, name, jurisdiction, entity_type, sic_codes, inc_date, website = row
        company = {
            "company_name": name,
            "jurisdiction": jurisdiction,
            "entity_type": entity_type,
            "sic_codes": sic_codes or [],
            "incorporation_date": inc_date,
        }

        with conn.cursor() as lei_cur:
            lei_cur.execute(
                """
                SELECT registered_on
                FROM lei_records
                WHERE company_id = %s
                """,
                (company_id,),
            )
            lei_row = lei_cur.fetchone()

        lei_data = None
        if lei_row and lei_row[0] is not None:
            from datetime import date as _date

            days = (_date.today() - lei_row[0]).days
            lei_data = {"days_since_registration": days}

        with conn.cursor() as psc_cur:
            psc_cur.execute(
                "SELECT country_of_residence, ceased_on, email FROM company_pscs WHERE company_id = %s",
                (company_id,),
            )
            psc_raw = psc_cur.fetchall()
            pscs_data = [
                {"country_of_residence": r[0], "ceased_on": r[1]} for r in psc_raw
            ]
            psc_email_present = any((r[2] or "").strip() for r in psc_raw)

        with conn.cursor() as off_cur:
            off_cur.execute(
                "SELECT nationality, resigned_on, email FROM company_officers WHERE company_id = %s",
                (company_id,),
            )
            off_raw = off_cur.fetchall()
            officers_data = [
                {"nationality": r[0], "resigned_on": r[1]} for r in off_raw
            ]
            officer_email_present = any((r[2] or "").strip() for r in off_raw)
            active_officers = sum(1 for r in off_raw if r[1] is None)

        # Legacy intelligence score (drives the existing Score Breakdown card)
        score, codes, tier = calculate_score(
            company,
            lei=lei_data,
            pscs=pscs_data or None,
            officers=officers_data or None,
        )
        summary = build_reason_summary(codes)

        # New dimensional scores
        arie_fit_score, _fit_codes = calculate_arie_fit(company)
        freshness_score = calculate_freshness(inc_date)
        keyword_score, _kw_matched = calculate_keyword_score(name or "", keywords)
        founder_quality_score = 50  # neutral until PR 3
        cross_border_score = 0  # populated in PR 3
        risk_score = 50  # neutral until PR 5

        # Pull company-level contact assets (empty until PR 2)
        with conn.cursor() as cc_cur:
            cc_cur.execute(
                "SELECT website, generic_email, linkedin_url FROM company_contacts WHERE company_id = %s",
                (company_id,),
            )
            cc_row = cc_cur.fetchone()
        cc_website = cc_row[0] if cc_row else None
        cc_email = cc_row[1] if cc_row else None
        cc_linkedin = cc_row[2] if cc_row else None

        has_website = bool((website or "").strip()) or bool((cc_website or "").strip())
        has_email = (
            officer_email_present or psc_email_present or bool((cc_email or "").strip())
        )
        has_linkedin = bool((cc_linkedin or "").strip())

        reachability_status = derive_reachability_status(
            website=website or cc_website,
            has_email=has_email,
            linkedin_url=cc_linkedin,
        )

        # Get existing RM status (if any) so readiness reflects engagement
        with conn.cursor() as ra_cur:
            ra_cur.execute(
                "SELECT status FROM rm_actions WHERE company_id = %s", (company_id,)
            )
            ra_row = ra_cur.fetchone()
        rm_status = ra_row[0] if ra_row else None

        lead_readiness = derive_lead_readiness(
            arie_fit_score=arie_fit_score,
            reachability_status=reachability_status,
            rm_status=rm_status,
        )

        priority_score = compute_priority_score(
            arie_fit_score=arie_fit_score,
            founder_quality_score=founder_quality_score,
            freshness_score=freshness_score,
            keyword_score=keyword_score,
            cross_border_score=cross_border_score,
            risk_score=risk_score,
            weights=weights,
        )

        enrichment_tier = derive_enrichment_tier(priority_score)

        why_reasons = build_why_reasons(
            company=company,
            arie_fit_score=arie_fit_score,
            freshness_score=freshness_score,
            reachability_status=reachability_status,
            has_website=has_website,
            has_email=has_email,
            has_linkedin=has_linkedin,
            lei=lei_data,
            officers_count=active_officers,
        )

        from psycopg.types.json import Jsonb

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lead_scores SET is_current = FALSE WHERE company_id = %s AND is_current = TRUE",
                (company_id,),
            )
            cur.execute(
                """
                INSERT INTO lead_scores (
                    company_id, score, tier, reason_codes,
                    reason_summary, scoring_version, is_current,
                    arie_fit_score, keyword_score, freshness_score,
                    founder_quality_score, cross_border_score, risk_score,
                    priority_score, reachability_status, lead_readiness,
                    enrichment_tier, why_reasons
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, TRUE,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    company_id,
                    score,
                    tier,
                    codes,
                    summary,
                    SCORING_VERSION,
                    arie_fit_score,
                    keyword_score,
                    freshness_score,
                    founder_quality_score,
                    cross_border_score,
                    risk_score,
                    priority_score,
                    reachability_status,
                    lead_readiness,
                    enrichment_tier,
                    Jsonb(why_reasons),
                ),
            )
        count += 1

    conn.commit()
    return count


def _refresh_queue(conn) -> int:
    """Atomic queue snapshot refresh. Returns new row count."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE queue_snapshot")
        cur.execute("""
            INSERT INTO queue_snapshot (
                canonical_company_id, company_name, jurisdiction, entity_type,
                incorporation_date, verify_url, priority_score, tier,
                reason_codes, reason_summary, scoring_version, refreshed_at
            )
            SELECT
                c.id,
                c.company_name,
                c.jurisdiction,
                c.entity_type,
                c.incorporation_date,
                c.verify_url,
                ls.score,
                ls.tier,
                ls.reason_codes,
                ls.reason_summary,
                ls.scoring_version,
                NOW()
            FROM companies c
            JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
            WHERE c.canonical_company_id IS NULL
            ORDER BY ls.score DESC
            """)
        cur.execute("SELECT COUNT(*) FROM queue_snapshot")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def _mauritius_zero_streak(conn) -> int:
    """Count consecutive days with zero Mauritius ingestion (approximation via audit log)."""
    # Simple heuristic: check if any Mauritius companies were updated recently
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM companies
            WHERE source_system = 'mauritius_mns'
              AND updated_at >= NOW() - INTERVAL '3 days'
            """)
        recent = cur.fetchone()[0]
    return 0 if recent > 0 else 3


def _start_run(conn) -> str | None:
    """Insert a pipeline_runs row with status='running'. Returns the run id."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (status) VALUES ('running') RETURNING id"
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        return str(run_id)
    except Exception as exc:
        logger.warning("pipeline_run_record_start_failed", extra={"error": str(exc)})
        conn.rollback()
        return None


def _reap_stuck_runs(conn) -> int:
    """Mark any pipeline_runs rows stuck in 'running' for >90 minutes as 'aborted'.

    Protects against zombie rows when the pipeline process is killed mid-flight
    (Railway redeploy, OOM, etc). The advisory lock self-releases on connection
    close, but the pipeline_runs row update never happens — this catches them.
    Returns the count of rows reaped.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pipeline_runs
                SET status = 'aborted',
                    error = 'reaped: stuck in running state >90min (likely process killed)',
                    completed_at = NOW(),
                    duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))
                WHERE status = 'running'
                  AND started_at < NOW() - INTERVAL '90 minutes'
                RETURNING id
                """)
            reaped_ids = [str(row[0]) for row in cur.fetchall()]
        conn.commit()
        if reaped_ids:
            logger.info(
                "pipeline_runs_reaped",
                extra={"count": len(reaped_ids), "reaped_ids": reaped_ids},
            )
        return len(reaped_ids)
    except Exception as exc:
        logger.warning("pipeline_reap_failed", extra={"error": str(exc)})
        conn.rollback()
        return 0


def _finish_run(
    conn,
    run_id: str | None,
    status: str,
    uk_count: int | None = None,
    mu_count: int | None = None,
    scores_count: int | None = None,
    queue_rows: int | None = None,
    duration_seconds: float | None = None,
    error: str | None = None,
) -> None:
    if run_id is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_runs
                SET completed_at = NOW(),
                    status = %s,
                    uk_count = %s,
                    mu_count = %s,
                    scores_count = %s,
                    queue_rows = %s,
                    duration_seconds = %s,
                    error = %s
                WHERE id = %s
                """,
                (
                    status,
                    uk_count,
                    mu_count,
                    scores_count,
                    queue_rows,
                    duration_seconds,
                    error,
                    run_id,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("pipeline_run_record_finish_failed", extra={"error": str(exc)})
        conn.rollback()


def run() -> None:
    start = time.time()
    logger.info("pipeline_started")

    with get_conn() as conn:
        _reap_stuck_runs(conn)
        run_id = _start_run(conn)
        try:
            if not _acquire_lock(conn):
                logger.warning("pipeline_already_running_skipping")
                _finish_run(conn, run_id, status="skipped_locked")
                return

            uk_count = fetch_uk_incorporations(conn)
            mu_count = fetch_mauritius_incorporations(conn)
            lei_count = fetch_gleif_registrations(conn)
            backfill_result = backfill_lei_company_links(conn)
            enrichment_result = run_ch_enrichment_batch(conn, CH_ENRICHMENT_BATCH_SIZE)
            if uk_count == 0:
                logger.error(
                    "pipeline_ingestion_failure",
                    extra={
                        "event": "pipeline_ingestion_failure",
                        "reason": "UK ingestion returned zero records",
                        "uk_count": uk_count,
                        "mu_count": mu_count,
                    },
                )
                raise RuntimeError(
                    "UK ingestion returned zero records — pipeline marked as failed"
                )
            scores_count = _score_new_companies(conn)
            queue_rows = _refresh_queue(conn)
            zero_streak = _mauritius_zero_streak(conn)

            if zero_streak >= 3:
                logger.warning(
                    "mauritius_zero_streak",
                    extra={"consecutive_empty_days": zero_streak},
                )

            duration = round(time.time() - start, 1)
            logger.info(
                "nightly_complete",
                extra={
                    "event": "nightly_complete",
                    "companies_fetched_uk": uk_count,
                    "companies_fetched_mu": mu_count,
                    "lei_matches": lei_count,
                    "lei_backfill_scanned": backfill_result["scanned"],
                    "lei_backfill_matched": backfill_result["matched"],
                    "lei_backfill_unmatched": backfill_result["unmatched"],
                    "officers_fetched": enrichment_result["officers"],
                    "pscs_fetched": enrichment_result["pscs"],
                    "enrichment_failures": enrichment_result["failed"],
                    "enrichment_skipped_rate_limit": 0,
                    "scores_generated": scores_count,
                    "queue_rows": queue_rows,
                    "duration_seconds": duration,
                    "scoring_version": SCORING_VERSION,
                    "mauritius_zero_streak": zero_streak,
                },
            )
            _finish_run(
                conn,
                run_id,
                status="success",
                uk_count=uk_count,
                mu_count=mu_count,
                scores_count=scores_count,
                queue_rows=queue_rows,
                duration_seconds=duration,
            )

        except Exception as exc:
            duration = round(time.time() - start, 1)
            logger.exception("pipeline_failed")
            _finish_run(
                conn,
                run_id,
                status="failed",
                duration_seconds=duration,
                error=str(exc)[:2000],
            )
            raise
        finally:
            _release_lock(conn)


if __name__ == "__main__":
    import logging as _logging
    from pythonjsonlogger import jsonlogger
    from src.config import LOG_LEVEL

    handler = _logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter())
    _logging.getLogger().addHandler(handler)
    _logging.getLogger().setLevel(getattr(_logging, LOG_LEVEL.upper(), _logging.INFO))

    run()
