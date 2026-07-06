"""
Nightly pipeline orchestrator.
Run directly: python -m src.pipeline
Or via GitHub Actions cron.
"""

import logging
import signal
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from src.config import (
    CH_ENRICHMENT_BATCH_SIZE,
    PIPELINE_ENRICHMENT_TIMEOUT_SECONDS,
    PIPELINE_SHADOW_TIMEOUT_SECONDS,
    PIPELINE_SOURCE_TIMEOUT_SECONDS,
    SCORING_SHADOW_MODE,
    SHADOW_BACKFILL_BATCH_SIZE,
    SHADOW_BACKFILL_LOCK_TIMEOUT_MS,
    SHADOW_BACKFILL_MAX_BATCHES,
    SHADOW_SCORE_ACTIVE_STALE_DAYS,
)
from src.db import get_conn
from src.ingestion.companies_house import fetch_uk_incorporations, run_ch_enrichment_batch
from src.ingestion.gleif import fetch_gleif_registrations
from src.ingestion.lei_backfill import backfill_lei_company_links
from src.ingestion.mauritius import fetch_mauritius_incorporations
from src.shadow_scoring import backfill_active_shadow_scores
from src.scoring import SCORING_VERSION, build_reason_summary, calculate_score

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_ID = 12345


class PipelineStepTimeout(TimeoutError):
    pass


@dataclass
class StepResult:
    name: str
    status: str
    count: int | None = None
    duration_seconds: float = 0.0
    error: str | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.name,
            "status": self.status,
            "count": self.count,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "details": self.details or {},
        }


@contextmanager
def _time_limit(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise_timeout(_signum, _frame):
        raise PipelineStepTimeout(f"step exceeded {seconds}s timeout")

    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    previous_timer = signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _run_step(
    conn,
    name: str,
    func: Callable[[], Any],
    *,
    timeout_seconds: int,
    critical: bool = False,
) -> StepResult:
    started = time.time()
    try:
        with _time_limit(timeout_seconds):
            raw_result = func()
        conn.commit()
        duration = round(time.time() - started, 2)
        count = raw_result if isinstance(raw_result, int) else None
        details = raw_result if isinstance(raw_result, dict) else None
        result = StepResult(
            name=name,
            status="completed",
            count=count,
            duration_seconds=duration,
            details=details,
        )
        logger.info("pipeline_step_completed", extra=result.as_dict())
        return result
    except Exception as exc:
        conn.rollback()
        duration = round(time.time() - started, 2)
        result = StepResult(
            name=name,
            status="failed",
            duration_seconds=duration,
            error=str(exc)[:500],
        )
        logger.warning(
            "pipeline_step_failed" if not critical else "pipeline_critical_step_failed",
            extra=result.as_dict(),
        )
        if critical:
            raise
        return result


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
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                   c.sic_codes, c.incorporation_date
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
        company_id, name, jurisdiction, entity_type, sic_codes, inc_date = row
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
                "SELECT country_of_residence, ceased_on FROM company_pscs WHERE company_id = %s",
                (company_id,),
            )
            pscs_data = [
                {"country_of_residence": row[0], "ceased_on": row[1]}
                for row in psc_cur.fetchall()
            ]

        with conn.cursor() as off_cur:
            off_cur.execute(
                "SELECT nationality, resigned_on FROM company_officers WHERE company_id = %s",
                (company_id,),
            )
            officers_data = [
                {"nationality": row[0], "resigned_on": row[1]}
                for row in off_cur.fetchall()
            ]

        score, codes, tier = calculate_score(
            company, lei=lei_data, pscs=pscs_data or None, officers=officers_data or None
        )
        summary = build_reason_summary(codes)

        with conn.cursor() as cur:
            # Invalidate any existing current score first
            cur.execute(
                "UPDATE lead_scores SET is_current = FALSE WHERE company_id = %s AND is_current = TRUE",
                (company_id,),
            )
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
    lei_count: int | None = None,
    scores_count: int | None = None,
    queue_rows: int | None = None,
    duration_seconds: float | None = None,
    error: str | None = None,
    source_results: list[dict[str, Any]] | None = None,
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
                    lei_count = %s,
                    scores_count = %s,
                    queue_rows = %s,
                    duration_seconds = %s,
                    error = %s,
                    source_results = %s
                WHERE id = %s
                """,
                (
                    status,
                    uk_count,
                    mu_count,
                    lei_count,
                    scores_count,
                    queue_rows,
                    duration_seconds,
                    error,
                    Jsonb(source_results or []),
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
    step_results: list[StepResult] = []

    with get_conn() as conn:
        _reap_stuck_runs(conn)
        run_id = _start_run(conn)
        try:
            if not _acquire_lock(conn):
                logger.warning("pipeline_already_running_skipping")
                _finish_run(conn, run_id, status="skipped_locked")
                return

            uk_result = _run_step(
                conn,
                "companies_house",
                lambda: fetch_uk_incorporations(conn),
                timeout_seconds=PIPELINE_SOURCE_TIMEOUT_SECONDS,
            )
            step_results.append(uk_result)

            mu_result = _run_step(
                conn,
                "mauritius_mns",
                lambda: fetch_mauritius_incorporations(conn),
                timeout_seconds=PIPELINE_SOURCE_TIMEOUT_SECONDS,
            )
            step_results.append(mu_result)

            lei_result = _run_step(
                conn,
                "gleif",
                lambda: fetch_gleif_registrations(conn),
                timeout_seconds=PIPELINE_SOURCE_TIMEOUT_SECONDS,
            )
            step_results.append(lei_result)

            backfill_result_step = _run_step(
                conn,
                "lei_backfill",
                lambda: backfill_lei_company_links(conn),
                timeout_seconds=PIPELINE_SOURCE_TIMEOUT_SECONDS,
            )
            step_results.append(backfill_result_step)

            enrichment_result_step = _run_step(
                conn,
                "companies_house_enrichment",
                lambda: run_ch_enrichment_batch(conn, CH_ENRICHMENT_BATCH_SIZE),
                timeout_seconds=PIPELINE_ENRICHMENT_TIMEOUT_SECONDS,
            )
            step_results.append(enrichment_result_step)

            successful_source_count = sum(
                1
                for result in (uk_result, mu_result, lei_result)
                if result.status == "completed"
            )
            if successful_source_count == 0:
                raise RuntimeError("all external source steps failed")

            score_result = _run_step(
                conn,
                "scoring",
                lambda: _score_new_companies(conn),
                timeout_seconds=PIPELINE_SOURCE_TIMEOUT_SECONDS,
                critical=True,
            )
            step_results.append(score_result)

            queue_result = _run_step(
                conn,
                "queue_refresh",
                lambda: _refresh_queue(conn),
                timeout_seconds=PIPELINE_SOURCE_TIMEOUT_SECONDS,
                critical=True,
            )
            step_results.append(queue_result)

            shadow_result = {
                "batches_processed": 0,
                "scanned": 0,
                "scored": 0,
                "failed": 0,
            }
            if SCORING_SHADOW_MODE:
                shadow_result_step = _run_step(
                    conn,
                    "shadow_scoring",
                    lambda: backfill_active_shadow_scores(
                        conn,
                        stale_days=SHADOW_SCORE_ACTIVE_STALE_DAYS,
                        batch_size=SHADOW_BACKFILL_BATCH_SIZE,
                        max_batches=SHADOW_BACKFILL_MAX_BATCHES,
                        lock_timeout_ms=SHADOW_BACKFILL_LOCK_TIMEOUT_MS,
                    ),
                    timeout_seconds=PIPELINE_SHADOW_TIMEOUT_SECONDS,
                )
                step_results.append(shadow_result_step)
                if shadow_result_step.details:
                    shadow_result = shadow_result_step.details
            zero_streak = _mauritius_zero_streak(conn)

            if zero_streak >= 3:
                logger.warning(
                    "mauritius_zero_streak",
                    extra={"consecutive_empty_days": zero_streak},
                )

            duration = round(time.time() - start, 1)
            failed_steps = [
                result for result in step_results if result.status != "completed"
            ]
            status = "partially_completed" if failed_steps else "completed"
            uk_count = uk_result.count
            mu_count = mu_result.count
            lei_count = lei_result.count
            backfill_result = backfill_result_step.details or {}
            enrichment_result = enrichment_result_step.details or {}
            scores_count = score_result.count
            queue_rows = queue_result.count
            logger.info(
                "nightly_complete",
                extra={
                    "event": "nightly_complete",
                    "status": status,
                    "companies_fetched_uk": uk_count,
                    "companies_fetched_mu": mu_count,
                    "lei_matches": lei_count,
                    "lei_backfill_scanned": backfill_result.get("scanned", 0),
                    "lei_backfill_matched": backfill_result.get("matched", 0),
                    "lei_backfill_unmatched": backfill_result.get("unmatched", 0),
                    "officers_fetched": enrichment_result.get("officers", 0),
                    "pscs_fetched": enrichment_result.get("pscs", 0),
                    "enrichment_failures": enrichment_result.get("failed", 0),
                    "enrichment_skipped_rate_limit": 0,
                    "scores_generated": scores_count,
                    "shadow_scoring_scanned": shadow_result["scanned"],
                    "shadow_scoring_scored": shadow_result["scored"],
                    "shadow_scoring_failed": shadow_result["failed"],
                    "queue_rows": queue_rows,
                    "duration_seconds": duration,
                    "scoring_version": SCORING_VERSION,
                    "mauritius_zero_streak": zero_streak,
                },
            )
            _finish_run(
                conn,
                run_id,
                status=status,
                uk_count=uk_count,
                mu_count=mu_count,
                lei_count=lei_count,
                scores_count=scores_count,
                queue_rows=queue_rows,
                duration_seconds=duration,
                error="; ".join(
                    f"{result.name}: {result.error}" for result in failed_steps
                )[:2000]
                if failed_steps
                else None,
                source_results=[result.as_dict() for result in step_results],
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
                source_results=[result.as_dict() for result in step_results],
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
