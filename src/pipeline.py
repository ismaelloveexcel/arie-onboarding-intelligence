"""
Nightly pipeline orchestrator.
Run directly: python -m src.pipeline
Or via GitHub Actions cron.
"""
import logging
import time

from src.db import get_conn
from src.ingestion.companies_house import fetch_uk_incorporations
from src.ingestion.mauritius import fetch_mauritius_incorporations
from src.scoring import SCORING_VERSION, build_reason_summary, calculate_score

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
    """Score all companies that have no current lead_score. Returns count scored."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                   c.sic_codes, c.incorporation_date
            FROM companies c
            WHERE NOT EXISTS (
                SELECT 1 FROM lead_scores ls
                WHERE ls.company_id = c.id AND ls.is_current = TRUE
            )
            """
        )
        rows = cur.fetchall()

    count = 0
    for row in rows:
        company_id, name, jurisdiction, entity_type, sic_codes, inc_date = row
        company = {
            "company_name": name,
            "jurisdiction": jurisdiction,
            "entity_type": entity_type,
            "sic_codes": sic_codes or [],
        }
        score, codes, tier = calculate_score(company)
        summary = build_reason_summary(codes)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_scores (
                    company_id, score, tier, reason_codes,
                    reason_summary, scoring_version, is_current
                ) VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                """,
                (company_id, score, tier, codes, summary, SCORING_VERSION),
            )
        count += 1

    conn.commit()
    return count


def _refresh_queue(conn) -> int:
    """Atomic queue snapshot refresh. Returns new row count."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE queue_snapshot")
        cur.execute(
            """
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
            """
        )
        cur.execute("SELECT COUNT(*) FROM queue_snapshot")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def _mauritius_zero_streak(conn) -> int:
    """Count consecutive days with zero Mauritius ingestion (approximation via audit log)."""
    # Simple heuristic: check if any Mauritius companies were updated recently
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM companies
            WHERE source_system = 'mauritius_mns'
              AND updated_at >= NOW() - INTERVAL '3 days'
            """
        )
        recent = cur.fetchone()[0]
    return 0 if recent > 0 else 3


def run() -> None:
    start = time.time()
    logger.info("pipeline_started")

    with get_conn() as conn:
        try:
            if not _acquire_lock(conn):
                logger.warning("pipeline_already_running_skipping")
                return

            uk_count = fetch_uk_incorporations(conn)
            mu_count = fetch_mauritius_incorporations(conn)
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
                    "scores_generated": scores_count,
                    "queue_rows": queue_rows,
                    "duration_seconds": duration,
                    "scoring_version": SCORING_VERSION,
                    "mauritius_zero_streak": zero_streak,
                },
            )

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
