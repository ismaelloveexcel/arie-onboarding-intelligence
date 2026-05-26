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
    if from_date is None:
        from_date = date.today() - timedelta(days=1)
    to_date = date.today()

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
