import logging
import re
import time

from psycopg.types.json import Jsonb

from src.db import upsert_company

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = {"global business company", "gbc", "authorised company", "ac"}
_TIMEOUT = 60
_RETRIES = 3


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower())).strip()


def fetch_mauritius_incorporations(conn) -> int:
    """
    Scrape Mauritius MNS for new GBC and Authorised Company incorporations.
    Returns count of records processed.
    Failure does NOT raise — caller receives 0 and a WARNING is logged.
    """
    for attempt in range(1, _RETRIES + 1):
        try:
            companies = _scrape_mns()
            if not companies:
                logger.warning(
                    "mauritius_scraper_zero_results",
                    extra={"attempt": attempt},
                )
                return 0

            count = 0
            for company in companies:
                entity_type = (company.get("entity_type") or "").lower()
                if entity_type not in _ALLOWED_TYPES:
                    continue
                _upsert_item(conn, company)
                count += 1

            conn.commit()
            logger.info("mauritius_fetch_done", extra={"count": count})
            return count

        except Exception as exc:
            logger.warning(
                "mauritius_scraper_error",
                extra={"attempt": attempt, "error": str(exc)},
            )
            if attempt < _RETRIES:
                time.sleep(attempt * 2)

    logger.error("mauritius_scraper_all_retries_failed")
    return 0


def _scrape_mns() -> list[dict]:
    """
    Playwright scraper for Mauritius MNS Business Registration database.
    Returns list of company dicts with keys:
      company_name, entity_type, registration_number, incorporation_date

    NOTE: entity_type cannot currently be determined from the page without
    interacting with the SharePoint search form. Until that DOM interaction
    is implemented (separate PR, needs live DOM recon), every row is returned
    with entity_type=None, which causes fetch_mauritius_incorporations to
    skip every row via the _ALLOWED_TYPES filter. This is intentional:
    prefer an honest zero over fabricated entity_type labels.
    """
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(_TIMEOUT * 1000)

            page.goto("https://mns.govmu.org/Pages/Business-Registration-Database.aspx")
            page.wait_for_load_state("networkidle")

            try:
                results.extend(_extract_rows(page))
            except Exception as exc:
                logger.warning("mauritius_extract_error", extra={"error": str(exc)})

            logger.warning(
                "mauritius_entity_type_unknown",
                extra={
                    "rows_extracted": len(results),
                    "reason": "form interaction not implemented; entity_type left None",
                },
            )
        finally:
            browser.close()

    return results


def _extract_rows(page) -> list[dict]:
    """Extract raw company rows from the MNS results table.

    entity_type is intentionally left as None — see _scrape_mns docstring.
    Callers must filter by entity_type via _ALLOWED_TYPES.
    """
    rows = []
    try:
        cells = page.query_selector_all("table tr")
    except Exception as exc:
        logger.warning("mauritius_query_selector_failed", extra={"error": str(exc)})
        return rows

    for row in cells:
        try:
            tds = row.query_selector_all("td")
            if len(tds) < 3:
                continue
            rows.append(
                {
                    "company_name": tds[0].inner_text().strip(),
                    "registration_number": tds[1].inner_text().strip(),
                    "entity_type": None,
                    "incorporation_date": None,
                }
            )
        except Exception as exc:
            logger.warning("mauritius_row_parse_failed", extra={"error": str(exc)})
            continue
    return rows


def _upsert_item(conn, item: dict) -> None:
    name = item.get("company_name") or ""
    entity_type = item.get("entity_type") or ""

    upsert_company(
        conn,
        {
            "source_system": "mauritius_mns",
            "source_ref": item.get("registration_number") or name,
            "company_name": name,
            "normalised_name": _normalise(name),
            "jurisdiction": "Mauritius",
            "entity_type": entity_type,
            "incorporation_date": item.get("incorporation_date"),
            "registered_address": None,
            "sic_codes": [],
            "website": None,
            "verify_url": "https://mns.govmu.org/Pages/Business-Registration-Database.aspx",
            "raw_data": Jsonb(item),
        },
    )
