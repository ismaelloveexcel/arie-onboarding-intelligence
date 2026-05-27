import logging
import re
import time
from datetime import date, datetime, timedelta

import httpx
from psycopg.types.json import Jsonb

from src.config import COMPANIES_HOUSE_API_KEY
from src.db import upsert_company

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.company-information.service.gov.uk"
_PAGE_SIZE = 100
_MAX_RECORDS = 500
_TIMEOUT = 30
_RETRY_DELAYS = [1, 2, 4]  # exponential backoff seconds

# Pre-filter to UK SIC codes that map to the scoring families in src/scoring.py
# (642xx FINANCIAL, 663xx FUND_MGMT, 620xx FINTECH). Cuts ingestion noise by ~80%
# vs unfiltered scraping while preserving every code that can currently earn points.
_SIC_PREFILTER = ",".join([
    # 642xx — Activities of holding companies (financial holding)
    "64201", "64202", "64203", "64204", "64205", "64209",
    # 663xx — Fund management activities
    "66300",
    # 620xx — Computer programming / IT services (fintech proxy)
    "62011", "62012", "62020", "62030", "62090",
])


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower())).strip()


def _get_with_retry(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    for attempt, delay in enumerate(_RETRY_DELAYS + [None], 1):
        try:
            resp = client.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if delay is None:
                raise
            logger.warning(
                "companies_house_retry",
                extra={"attempt": attempt, "error": str(exc)},
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


def fetch_uk_incorporations(conn, from_date: date | None = None) -> int:
    """
    Fetch UK incorporations from Companies House and upsert into companies table.
    Returns count of records processed.
    """
    # Default: ingest yesterday's complete day. Today is still in progress at
    # Companies House so partial counts would be misleading; the nightly run
    # picks up "yesterday" once it's fully closed.
    if from_date is None:
        from_date = date.today() - timedelta(days=1)
    to_date = from_date

    start_index = 0
    total_fetched = 0

    with httpx.Client(
        auth=(COMPANIES_HOUSE_API_KEY, ""),
        headers={"Accept": "application/json"},
    ) as client:
        while total_fetched < _MAX_RECORDS:
            params = {
                "incorporated_from": from_date.isoformat(),
                "incorporated_to": to_date.isoformat(),
                "sic_codes": _SIC_PREFILTER,
                "size": _PAGE_SIZE,
                "start_index": start_index,
            }

            try:
                resp = _get_with_retry(client, f"{_BASE_URL}/advanced-search/companies", params)
            except Exception as exc:
                logger.error(
                    "companies_house_fetch_failed",
                    extra={"start_index": start_index, "error": str(exc)},
                )
                break

            payload = resp.json()
            items = payload.get("items") or []
            total_results = payload.get("total_results", 0)

            if start_index == 0 and total_results > _MAX_RECORDS:
                logger.warning(
                    "companies_house_results_truncated",
                    extra={
                        "total_results": total_results,
                        "max_records": _MAX_RECORDS,
                        "dropped": total_results - _MAX_RECORDS,
                    },
                )

            if not items:
                break

            for item in items:
                if total_fetched >= _MAX_RECORDS:
                    break
                _upsert_item(conn, item)
                total_fetched += 1

            conn.commit()
            logger.info(
                "companies_house_page_done",
                extra={"start_index": start_index, "fetched": total_fetched},
            )

            if start_index + _PAGE_SIZE >= total_results:
                break

            start_index += _PAGE_SIZE
            time.sleep(0.5)

    return total_fetched


def _upsert_item(conn, item: dict) -> None:
    company_number = item.get("company_number") or ""
    addr = item.get("registered_office_address") or {}
    addr_parts = [
        addr.get("address_line_1"),
        addr.get("address_line_2"),
        addr.get("locality"),
        addr.get("postal_code"),
    ]
    registered_address = ", ".join(p for p in addr_parts if p)
    inc_date_raw = item.get("date_of_creation")
    inc_date = datetime.strptime(inc_date_raw, "%Y-%m-%d").date() if inc_date_raw else None
    name = item.get("company_name") or ""

    upsert_company(
        conn,
        {
            "source_system": "companies_house",
            "source_ref": company_number,
            "company_name": name,
            "normalised_name": _normalise(name),
            "jurisdiction": "UK",
            "entity_type": item.get("company_type"),
            "incorporation_date": inc_date,
            "registered_address": registered_address or None,
            "sic_codes": item.get("sic_codes") or [],
            "website": None,
            "verify_url": f"https://find-and-update.company-information.service.gov.uk/company/{company_number}",
            "raw_data": Jsonb(item),
        },
    )


# ---------------------------------------------------------------------------
# PSC + Officers enrichment
# ---------------------------------------------------------------------------

_ENRICHMENT_BACKOFF = [2, 4, 8]  # seconds; all ≤ 30s cap per spec


def _enrichment_get(client: httpx.Client, url: str) -> tuple[int, dict | None]:
    """
    GET `url` with retry logic for 429 and transient network errors.

    Returns:
        (200, dict)   on success
        (404, None)   on not-found (no retry)
        (429, None)   when rate-limit retries are exhausted
    Raises:
        httpx.HTTPError / httpx.TimeoutException on non-429, non-404 failure
        after all retries are spent.
    """
    for attempt, delay in enumerate(_ENRICHMENT_BACKOFF + [None], 1):
        try:
            resp = client.get(url, timeout=_TIMEOUT)
            if resp.status_code == 404:
                return 404, None
            if resp.status_code == 429:
                if delay is None:
                    logger.warning(
                        "ch_enrichment_429_exhausted",
                        extra={"url": url},
                    )
                    return 429, None
                sleep_s = min(delay, 30)
                logger.warning(
                    "ch_enrichment_rate_limited",
                    extra={"url": url, "attempt": attempt, "sleep_s": sleep_s},
                )
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp.status_code, resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if delay is None:
                raise
            logger.warning(
                "ch_enrichment_retry",
                extra={"url": url, "attempt": attempt, "error": str(exc)},
            )
            time.sleep(min(delay, 30))
    raise RuntimeError("unreachable")


def _upsert_officers(conn, company_id: str, items: list[dict]) -> int:
    """Upsert officer records into company_officers. Returns count upserted."""
    count = 0
    with conn.cursor() as cur:
        for item in items:
            dob = item.get("date_of_birth") or {}
            appointed_raw = item.get("appointed_on")
            resigned_raw = item.get("resigned_on")
            try:
                appointed_on = (
                    datetime.strptime(appointed_raw, "%Y-%m-%d").date()
                    if appointed_raw else None
                )
                resigned_on = (
                    datetime.strptime(resigned_raw, "%Y-%m-%d").date()
                    if resigned_raw else None
                )
            except ValueError:
                appointed_on = None
                resigned_on = None
            cur.execute(
                """
                INSERT INTO company_officers (
                    company_id, officer_name, role, appointed_on, resigned_on,
                    nationality, country_of_residence,
                    date_of_birth_year, date_of_birth_month, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, officer_name, appointed_on)
                    DO UPDATE SET
                        role                 = EXCLUDED.role,
                        resigned_on          = EXCLUDED.resigned_on,
                        nationality          = EXCLUDED.nationality,
                        country_of_residence = EXCLUDED.country_of_residence,
                        date_of_birth_year   = EXCLUDED.date_of_birth_year,
                        date_of_birth_month  = EXCLUDED.date_of_birth_month,
                        raw_json             = EXCLUDED.raw_json,
                        fetched_at           = NOW()
                """,
                (
                    company_id,
                    item.get("name") or "",
                    item.get("officer_role") or "",
                    appointed_on,
                    resigned_on,
                    item.get("nationality"),
                    item.get("country_of_residence"),
                    dob.get("year"),
                    dob.get("month"),
                    Jsonb(item),
                ),
            )
            count += 1
    return count


def _upsert_pscs(conn, company_id: str, items: list[dict]) -> int:
    """Upsert PSC records into company_pscs. Returns count upserted."""
    count = 0
    with conn.cursor() as cur:
        for item in items:
            notified_raw = item.get("notified_on")
            ceased_raw = item.get("ceased_on")
            try:
                notified_on = (
                    datetime.strptime(notified_raw, "%Y-%m-%d").date()
                    if notified_raw else None
                )
                ceased_on = (
                    datetime.strptime(ceased_raw, "%Y-%m-%d").date()
                    if ceased_raw else None
                )
            except ValueError:
                notified_on = None
                ceased_on = None
            cur.execute(
                """
                INSERT INTO company_pscs (
                    company_id, name, kind, nationality, country_of_residence,
                    natures_of_control, notified_on, ceased_on, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, name, notified_on)
                    DO UPDATE SET
                        kind                 = EXCLUDED.kind,
                        nationality          = EXCLUDED.nationality,
                        country_of_residence = EXCLUDED.country_of_residence,
                        natures_of_control   = EXCLUDED.natures_of_control,
                        ceased_on            = EXCLUDED.ceased_on,
                        raw_json             = EXCLUDED.raw_json,
                        fetched_at           = NOW()
                """,
                (
                    company_id,
                    item.get("name") or "",
                    item.get("kind") or "",
                    item.get("nationality"),
                    item.get("country_of_residence"),
                    item.get("natures_of_control"),
                    notified_on,
                    ceased_on,
                    Jsonb(item),
                ),
            )
            count += 1
    return count


def _set_last_enriched_at(conn, company_id) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE companies SET last_enriched_at = NOW() WHERE id = %s",
            (str(company_id),),
        )


def fetch_officers(company_number: str) -> list[dict]:
    """Fetch officer list for *company_number* from Companies House.
    Returns empty list on 404 or rate-limit exhaustion.
    """
    url = f"{_BASE_URL}/company/{company_number}/officers"
    with httpx.Client(
        auth=(COMPANIES_HOUSE_API_KEY, ""),
        headers={"Accept": "application/json"},
    ) as client:
        status, data = _enrichment_get(client, url)
    if data is None:
        return []
    return data.get("items") or []


def fetch_pscs(company_number: str) -> list[dict]:
    """Fetch PSC list for *company_number* from Companies House.
    Returns empty list on 404 or rate-limit exhaustion.
    """
    url = f"{_BASE_URL}/company/{company_number}/persons-with-significant-control"
    with httpx.Client(
        auth=(COMPANIES_HOUSE_API_KEY, ""),
        headers={"Accept": "application/json"},
    ) as client:
        status, data = _enrichment_get(client, url)
    if data is None:
        return []
    return data.get("items") or []


def enrich_company(conn, company_id, company_number: str) -> dict:
    """Fetch officers + PSCs for one company, upsert to DB, set last_enriched_at.

    Always sets last_enriched_at before returning so the scheduler does not
    re-queue the same company on the next run.

    Returns:
        {"officers": int, "pscs": int, "status": "ok"|"not_found"|"failed"}
    """
    officers_url = f"{_BASE_URL}/company/{company_number}/officers"
    pscs_url = (
        f"{_BASE_URL}/company/{company_number}/persons-with-significant-control"
    )

    status_o: int | None = None
    data_o: dict | None = None
    status_p: int | None = None
    data_p: dict | None = None

    try:
        with httpx.Client(
            auth=(COMPANIES_HOUSE_API_KEY, ""),
            headers={"Accept": "application/json"},
        ) as client:
            status_o, data_o = _enrichment_get(client, officers_url)

            if status_o == 429:
                logger.warning(
                    "ch_enrich_rate_limit_exhausted",
                    extra={"company_number": company_number, "endpoint": "officers"},
                )
                _set_last_enriched_at(conn, company_id)
                conn.commit()
                return {"officers": 0, "pscs": 0, "status": "failed"}

            if status_o == 404:
                logger.info(
                    "ch_enrich_not_found",
                    extra={"company_number": company_number},
                )
                _set_last_enriched_at(conn, company_id)
                conn.commit()
                return {"officers": 0, "pscs": 0, "status": "not_found"}

            status_p, data_p = _enrichment_get(client, pscs_url)

            if status_p == 429:
                logger.warning(
                    "ch_enrich_rate_limit_exhausted",
                    extra={"company_number": company_number, "endpoint": "pscs"},
                )
                _set_last_enriched_at(conn, company_id)
                conn.commit()
                return {"officers": 0, "pscs": 0, "status": "failed"}

            # Validate payload shapes — malformed response is treated as failed
            if data_o is not None and not isinstance(data_o, dict):
                raise ValueError(
                    f"Unexpected officers payload type: {type(data_o).__name__}"
                )
            if data_p is not None and not isinstance(data_p, dict):
                raise ValueError(
                    f"Unexpected PSC payload type: {type(data_p).__name__}"
                )

    except Exception as exc:
        logger.warning(
            "ch_enrich_failed",
            extra={"company_number": company_number, "error": str(exc)},
        )
        _set_last_enriched_at(conn, company_id)
        conn.commit()
        return {"officers": 0, "pscs": 0, "status": "failed"}

    officers_items = (data_o.get("items") or []) if data_o else []
    # 404 on PSC endpoint means no PSCs registered — treat as empty, not an error
    pscs_items = (data_p.get("items") or []) if data_p else []

    try:
        officers_count = _upsert_officers(conn, str(company_id), officers_items)
        pscs_count = _upsert_pscs(conn, str(company_id), pscs_items)
        _set_last_enriched_at(conn, company_id)
        conn.commit()
    except Exception as exc:
        logger.warning(
            "ch_enrich_db_failed",
            extra={"company_number": company_number, "error": str(exc)},
        )
        conn.rollback()
        try:
            _set_last_enriched_at(conn, company_id)
            conn.commit()
        except Exception:
            conn.rollback()
        return {"officers": 0, "pscs": 0, "status": "failed"}

    logger.info(
        "ch_enrich_complete",
        extra={
            "company_number": company_number,
            "officers": officers_count,
            "pscs": pscs_count,
        },
    )
    return {"officers": officers_count, "pscs": pscs_count, "status": "ok"}


def run_ch_enrichment_batch(conn, limit: int) -> dict:
    """Select up to *limit* un-enriched CH companies and enrich each.

    Selects WHERE source_system = 'companies_house' AND last_enriched_at IS NULL
    ordered by created_at DESC so newest companies are enriched first.

    Returns:
        {
            "enriched": int,            # companies with status ok or not_found
            "officers": int,            # total officer rows upserted
            "pscs": int,                # total PSC rows upserted
            "failed": int,              # companies that returned status=failed
        }
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_ref
            FROM companies
            WHERE source_system = 'companies_house'
              AND last_enriched_at IS NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    enriched = 0
    officers_total = 0
    pscs_total = 0
    failures = 0

    for company_id, company_number in rows:
        result = enrich_company(conn, company_id, company_number)
        if result["status"] == "failed":
            failures += 1
        else:
            enriched += 1
            officers_total += result["officers"]
            pscs_total += result["pscs"]

    logger.info(
        "ch_enrichment_batch_complete",
        extra={
            "limit": limit,
            "enriched": enriched,
            "officers": officers_total,
            "pscs": pscs_total,
            "failed": failures,
        },
    )
    return {
        "enriched": enriched,
        "officers": officers_total,
        "pscs": pscs_total,
        "failed": failures,
    }
