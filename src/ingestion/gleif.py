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
from datetime import date, timedelta

import httpx
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

_GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
_TIMEOUT = 30
_PAGE_SIZE = 100
_RATE_LIMIT_DELAY = 0.5
_COUNTRY_CODES = ["GB", "MU", "AE"]


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower())).strip()


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


def _find_company_id(conn, registered_as: str | None, legal_name: str) -> str | None:
    """
    Match a GLEIF record to a company in our DB.
    Primary: exact match on company_number (registeredAs).
    Fallback: normalised name match.
    Returns company UUID string or None.
    """
    if registered_as:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM companies WHERE source_ref = %s LIMIT 1",
                (registered_as,),
            )
            row = cur.fetchone()
            if row:
                return str(row[0])

    normalised = _normalise(legal_name)
    if normalised:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM companies WHERE normalised_name = %s LIMIT 1",
                (normalised,),
            )
            row = cur.fetchone()
            if row:
                return str(row[0])

    return None


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

                    company_id = _find_company_id(conn, registered_as, legal_name)

                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO lei_records (
                                    company_id, lei_code, legal_name,
                                    jurisdiction, entity_status,
                                    registration_status, registered_on,
                                    last_updated_on, managing_lou,
                                    registered_as, gleif_url, raw_data
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s
                                )
                                ON CONFLICT (lei_code) DO UPDATE SET
                                    company_id = COALESCE(
                                        EXCLUDED.company_id,
                                        lei_records.company_id
                                    ),
                                    entity_status = EXCLUDED.entity_status,
                                    registration_status = EXCLUDED.registration_status,
                                    last_updated_on = EXCLUDED.last_updated_on,
                                    managing_lou = EXCLUDED.managing_lou,
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
                                ),
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
