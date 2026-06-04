"""
LEI backfill: link orphaned lei_records rows to companies.

Orphaned rows arise when the GLEIF feed arrives before the company record
exists in our DB, so _find_company_id() at insert time returns None.
This module re-runs the same matching logic against the current companies
table and patches company_id where a match is now found.
"""
import logging

from src.config import LEI_BACKFILL_CHUNK_SIZE
from src.ingestion.gleif import (
    LEI_MATCH_CONFIDENCE_THRESHOLD,
    _upsert_lei_review_queue,
    resolve_company_match,
)

logger = logging.getLogger(__name__)


def backfill_lei_company_links(conn) -> dict:
    """
    For each lei_records row WHERE company_id IS NULL, attempt to match to
    a company via source_ref (registered_as) or normalised_name.

    Processes in batches of LEI_BACKFILL_CHUNK_SIZE, advancing by cursor so
    already-checked-but-unmatched rows are not re-scanned in the same run.
    Safe to re-run: matched rows are excluded by the WHERE company_id IS NULL
    predicate on subsequent runs.

    Returns {"scanned": int, "matched": int, "ambiguous": int, "unmatched": int}.
    """
    scanned = 0
    matched = 0
    ambiguous = 0
    last_id = None

    while True:
        with conn.cursor() as cur:
            if last_id is None:
                cur.execute(
                    """
                    SELECT id, lei_code, registered_as, legal_name
                    FROM lei_records
                    WHERE company_id IS NULL
                    ORDER BY id
                    LIMIT %s
                    """,
                    (LEI_BACKFILL_CHUNK_SIZE,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, lei_code, registered_as, legal_name
                    FROM lei_records
                    WHERE company_id IS NULL AND id > %s
                    ORDER BY id
                    LIMIT %s
                    """,
                    (last_id, LEI_BACKFILL_CHUNK_SIZE),
                )
            batch = cur.fetchall()

        if not batch:
            break

        for lei_id, lei_code, registered_as, legal_name in batch:
            scanned += 1
            match = resolve_company_match(conn, registered_as, legal_name or "")
            company_id = (
                match.company_id
                if (
                    match.match_state == "VERIFIED"
                    and match.company_id
                    and match.confidence_score >= LEI_MATCH_CONFIDENCE_THRESHOLD
                )
                else None
            )

            if company_id:
                with conn.cursor() as upd:
                    upd.execute(
                        """
                        UPDATE lei_records
                        SET company_id = %s,
                            match_state = %s,
                            confidence_score = %s,
                            match_basis = %s,
                            matching_reason = %s
                        WHERE id = %s
                        """,
                        (
                            company_id,
                            match.match_state,
                            match.confidence_score,
                            match.match_basis,
                            match.reason,
                            lei_id,
                        ),
                    )
                matched += 1
                logger.info(
                    "lei_backfill_matched",
                    extra={"lei_id": str(lei_id), "company_id": company_id},
                )
                _upsert_lei_review_queue(
                    conn,
                    lei_code=lei_code,
                    registered_as=registered_as,
                    legal_name=legal_name or "",
                    match=match,
                )
            elif match.match_state == "AMBIGUOUS":
                ambiguous += 1
                with conn.cursor() as upd:
                    upd.execute(
                        """
                        UPDATE lei_records
                        SET match_state = %s,
                            confidence_score = %s,
                            match_basis = %s,
                            matching_reason = %s
                        WHERE id = %s
                        """,
                        (
                            match.match_state,
                            match.confidence_score,
                            match.match_basis,
                            match.reason,
                            lei_id,
                        ),
                    )
                _upsert_lei_review_queue(
                    conn,
                    lei_code=lei_code,
                    registered_as=registered_as,
                    legal_name=legal_name or "",
                    match=match,
                )
                logger.warning(
                    "lei_backfill_ambiguous",
                    extra={
                        "lei_id": str(lei_id),
                        "candidate_company_ids": match.candidate_company_ids,
                    },
                )
            else:
                with conn.cursor() as upd:
                    upd.execute(
                        """
                        UPDATE lei_records
                        SET match_state = %s,
                            confidence_score = %s,
                            match_basis = %s,
                            matching_reason = %s
                        WHERE id = %s
                        """,
                        (
                            match.match_state,
                            match.confidence_score,
                            match.match_basis,
                            match.reason,
                            lei_id,
                        ),
                    )
                logger.debug(
                    "lei_backfill_unmatched",
                    extra={"lei_id": str(lei_id), "registered_as": registered_as},
                )

        conn.commit()
        last_id = batch[-1][0]

    unmatched = scanned - matched - ambiguous
    logger.info(
        "lei_backfill_complete",
        extra={
            "scanned": scanned,
            "matched": matched,
            "ambiguous": ambiguous,
            "unmatched": unmatched,
        },
    )
    return {
        "scanned": scanned,
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
    }
