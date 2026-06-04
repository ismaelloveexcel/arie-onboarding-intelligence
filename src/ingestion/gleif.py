"""
GLEIF LEI ingestion.

Queries the GLEIF REST API for new LEI registrations by country
and date. Matches to companies in our DB using Companies House
registration number (registeredAs) as primary key, normalised
name as fallback.

Supported country codes: GB, MU, AE
"""
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

_GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
_TIMEOUT = 30
_PAGE_SIZE = 100
_RATE_LIMIT_DELAY = 0.5
_COUNTRY_CODES = ["GB", "MU", "AE"]
LEI_MATCH_CONFIDENCE_THRESHOLD = 0.95


@dataclass(frozen=True)
class MatchResult:
    company_id: str | None
    match_state: str
    confidence_score: float
    match_basis: str | None
    candidate_company_ids: list[str]
    reason: str


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower())).strip()


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


def _candidate_ids_by_source_ref(conn, registered_as: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM companies WHERE source_ref = %s ORDER BY id",
            (registered_as,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def _candidate_ids_by_name(conn, legal_name: str) -> list[str]:
    normalised = _normalise(legal_name)
    if not normalised:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM companies WHERE normalised_name = %s ORDER BY id",
            (normalised,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def resolve_company_match(conn, registered_as: str | None, legal_name: str) -> MatchResult:
    """
    Deterministic-only company matching:
    - VERIFIED: exactly one candidate
    - AMBIGUOUS: multiple candidates (must go to manual review)
    - UNMATCHED: no candidates
    """
    if registered_as:
        source_ref_candidates = _candidate_ids_by_source_ref(conn, registered_as)
        if len(source_ref_candidates) == 1:
            return MatchResult(
                company_id=source_ref_candidates[0],
                match_state="VERIFIED",
                confidence_score=1.0,
                match_basis="registered_as",
                candidate_company_ids=source_ref_candidates,
                reason="Unique source_ref match",
            )
        if len(source_ref_candidates) > 1:
            return MatchResult(
                company_id=None,
                match_state="AMBIGUOUS",
                confidence_score=0.5,
                match_basis="registered_as",
                candidate_company_ids=source_ref_candidates,
                reason="Multiple source_ref matches",
            )

    name_candidates = _candidate_ids_by_name(conn, legal_name)
    if len(name_candidates) == 1:
        return MatchResult(
            company_id=name_candidates[0],
            match_state="VERIFIED",
            confidence_score=0.95,
            match_basis="normalised_name",
            candidate_company_ids=name_candidates,
            reason="Unique normalised_name match",
        )
    if len(name_candidates) > 1:
        return MatchResult(
            company_id=None,
            match_state="AMBIGUOUS",
            confidence_score=0.4,
            match_basis="normalised_name",
            candidate_company_ids=name_candidates,
            reason="Multiple normalised_name matches",
        )

    return MatchResult(
        company_id=None,
        match_state="UNMATCHED",
        confidence_score=0.0,
        match_basis=None,
        candidate_company_ids=[],
        reason="No deterministic candidate",
    )


def _find_company_id(conn, registered_as: str | None, legal_name: str) -> str | None:
    match = resolve_company_match(conn, registered_as, legal_name)
    if (
        match.match_state == "VERIFIED"
        and match.company_id
        and match.confidence_score >= LEI_MATCH_CONFIDENCE_THRESHOLD
    ):
        return match.company_id
    return None


def _upsert_lei_review_queue(
    conn,
    *,
    lei_code: str,
    registered_as: str | None,
    legal_name: str,
    match: MatchResult,
) -> None:
    if match.match_state != "AMBIGUOUS":
        if match.match_state == "VERIFIED":
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lei_link_review_queue WHERE lei_code = %s", (lei_code,))
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lei_link_review_queue (
                lei_code, registered_as, legal_name, match_basis,
                confidence_score, candidate_company_ids, reason, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (lei_code) DO UPDATE SET
                registered_as = EXCLUDED.registered_as,
                legal_name = EXCLUDED.legal_name,
                match_basis = EXCLUDED.match_basis,
                confidence_score = EXCLUDED.confidence_score,
                candidate_company_ids = EXCLUDED.candidate_company_ids,
                reason = EXCLUDED.reason,
                status = 'pending',
                updated_at = NOW()
            """,
            (
                lei_code,
                registered_as,
                legal_name,
                match.match_basis,
                match.confidence_score,
                Jsonb(match.candidate_company_ids),
                match.reason,
            ),
        )


def _fetch_page(client: httpx.Client, country: str,
                target_date: date, page: int) -> dict:
    resp = client.get(
        _GLEIF_URL,
        params={
            "filter[entity.legalAddress.country]": country,
            "filter[registration.initialRegistrationDate]": target_date.isoformat(),
            "page[size]": _PAGE_SIZE,
            "page[number]": page,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_gleif_registrations(conn, target_date: date | None = None) -> int:
    """
    Fetch new LEI registrations for target_date (defaults to yesterday).
    Upserts into lei_records. Returns count of new/updated records.
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    total_upserted = 0

    with httpx.Client(headers={"Accept": "application/vnd.api+json"}) as client:
        for country in _COUNTRY_CODES:
            page = 1
            country_count = 0

            while True:
                try:
                    data = _fetch_page(client, country, target_date, page)
                except Exception as exc:
                    logger.warning(
                        "gleif_page_fetch_failed",
                        extra={
                            "country": country,
                            "page": page,
                            "error": str(exc),
                        },
                    )
                    break

                records = data.get("data") or []
                total_pages = (
                    data.get("meta", {})
                    .get("pagination", {})
                    .get("pageCount", 1)
                )

                for record in records:
                    attrs = record.get("attributes", {})
                    entity = attrs.get("entity", {})
                    reg = attrs.get("registration", {})

                    lei_code = attrs.get("lei", "")
                    legal_name = entity.get("legalName", {}).get("name", "")
                    registered_as = entity.get("registeredAs") or None

                    match = resolve_company_match(conn, registered_as, legal_name)
                    company_id = (
                        match.company_id
                        if (
                            match.match_state == "VERIFIED"
                            and match.company_id
                            and match.confidence_score >= LEI_MATCH_CONFIDENCE_THRESHOLD
                        )
                        else None
                    )

                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO lei_records (
                                    company_id, lei_code, legal_name,
                                    jurisdiction, entity_status,
                                    registration_status, registered_on,
                                    last_updated_on, managing_lou,
                                    registered_as, gleif_url, raw_data,
                                    match_state, confidence_score, match_basis, matching_reason
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s
                                )
                                ON CONFLICT (lei_code) DO UPDATE SET
                                    company_id = CASE
                                        WHEN EXCLUDED.match_state = 'VERIFIED'
                                             AND EXCLUDED.company_id IS NOT NULL
                                        THEN EXCLUDED.company_id
                                        ELSE lei_records.company_id
                                    END,
                                    entity_status = EXCLUDED.entity_status,
                                    registration_status = EXCLUDED.registration_status,
                                    last_updated_on = EXCLUDED.last_updated_on,
                                    managing_lou = EXCLUDED.managing_lou,
                                    match_state = EXCLUDED.match_state,
                                    confidence_score = EXCLUDED.confidence_score,
                                    match_basis = EXCLUDED.match_basis,
                                    matching_reason = EXCLUDED.matching_reason,
                                    raw_data = EXCLUDED.raw_data,
                                    last_seen = NOW()
                                """,
                                (
                                    company_id,
                                    lei_code,
                                    legal_name,
                                    entity.get("jurisdiction"),
                                    entity.get("status"),
                                    reg.get("status"),
                                    _parse_date(
                                        reg.get("initialRegistrationDate")
                                    ),
                                    _parse_date(reg.get("lastUpdateDate")),
                                    reg.get("managingLou"),
                                    registered_as,
                                    f"https://search.gleif.org/#/record/{lei_code}",
                                    Jsonb(attrs),
                                    match.match_state,
                                    match.confidence_score,
                                    match.match_basis,
                                    match.reason,
                                ),
                            )
                        _upsert_lei_review_queue(
                            conn,
                            lei_code=lei_code,
                            registered_as=registered_as,
                            legal_name=legal_name,
                            match=match,
                        )
                        country_count += 1
                    except Exception as exc:
                        logger.warning(
                            "gleif_upsert_failed",
                            extra={
                                "lei_code": lei_code,
                                "error": str(exc),
                            },
                        )

                if page >= total_pages:
                    break
                page += 1
                time.sleep(_RATE_LIMIT_DELAY)

            conn.commit()
            logger.info(
                "gleif_country_done",
                extra={
                    "country": country,
                    "date": target_date.isoformat(),
                    "upserted": country_count,
                },
            )
            total_upserted += country_count

    logger.info(
        "gleif_fetch_done",
        extra={
            "date": target_date.isoformat(),
            "total_upserted": total_upserted,
        },
    )
    return total_upserted
