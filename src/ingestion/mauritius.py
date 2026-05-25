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

            # Filter to GBC and Authorised Company types
            for entity_type_filter in ("Global Business Company", "Authorised Company"):
                time.sleep(1)
                try:
                    rows = _extract_rows(page, entity_type_filter)
                    results.extend(rows)
                except Exception as exc:
                    logger.warning(
                        "mauritius_filter_error",
                        extra={"entity_type": entity_type_filter, "error": str(exc)},
                    )
        finally:
            browser.close()

    return results


def _extract_rows(page, entity_type_filter: str) -> list[dict]:
    """Extract company rows for a given entity type filter from MNS page."""
    rows = []
    try:
        cells = page.query_selector_all("table tr")
        for row in cells:
            tds = row.query_selector_all("td")
            if len(tds) < 3:
                continue
            rows.append(
                {
                    "company_name": tds[0].inner_text().strip(),
                    "registration_number": tds[1].inner_text().strip(),
                    "entity_type": entity_type_filter,
                    "incorporation_date": None,
                }
            )
    except Exception:
        pass
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
